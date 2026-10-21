// @vitest-environment node
import { describe, expect, it } from 'vitest'

import goldenVectors from '../../../contracts/gymti-golden-vectors.v1.json'

import { COACH_STYLE_IDS } from '@/domain/coach'
import { GYMTI_TYPES } from '@/domain/gymti'
import {
  deterministicLocalFallback,
  evaluateGymtiAnswers,
  GYMTI_QUESTIONNAIRE,
  legalNextQuestionCandidates,
  terminalQuestionGuaranteeViolations,
  validateQuestionnaireContract,
} from '@/features/gymti'

describe('GYMTI questionnaire contract', () => {
  it('loads the versioned JSON contract with stable result identifiers', () => {
    expect(validateQuestionnaireContract(GYMTI_QUESTIONNAIRE)).toEqual([])
    expect(GYMTI_QUESTIONNAIRE.version).toBe('gymti-questionnaire.v1')
    expect(GYMTI_QUESTIONNAIRE.gymtiTypes.map((type) => type.id)).toEqual([
      'IMNB',
      'KCAL',
      'HIDE',
      'LIFE',
      'CURV',
      'BOOM',
      'WINN',
    ])
    expect(GYMTI_QUESTIONNAIRE.coachStyleIds).toEqual([
      'hotblood',
      'gentle',
      'snarky',
      'analyst',
      'comedian',
      'challenger',
      'zen',
    ])
  })

  it('rejects a terminal bank that cannot prove every option converges', () => {
    const underpowered = JSON.parse(JSON.stringify(GYMTI_QUESTIONNAIRE)) as {
      rules: { terminal: { scoreBoost: number } }
      terminalBank: Array<{ options: Array<{ gymtiScores: Record<string, number> }> }>
    }
    underpowered.rules.terminal.scoreBoost = 1
    underpowered.terminalBank[0].options[0].gymtiScores = {}

    const errors = validateQuestionnaireContract(underpowered)

    expect(errors).toEqual(expect.arrayContaining([
      expect.stringContaining('terminal scoreBoost must dominate'),
      expect.stringContaining('terminal option must boost exactly one GYMTI type'),
    ]))
  })

  it('rejects contracts whose hard bans can remove every terminal style', () => {
    const unsafe = JSON.parse(JSON.stringify(GYMTI_QUESTIONNAIRE)) as {
      questions: Array<{
        options: Array<{ hardBans: string[]; isNoMatch: boolean }>
      }>
    }
    const scoredOptions = unsafe.questions
      .flatMap((question) => question.options)
      .filter((option) => !option.isNoMatch)
    COACH_STYLE_IDS.forEach((styleId, index) => {
      scoredOptions[index]!.hardBans = [styleId]
    })

    expect(validateQuestionnaireContract(unsafe)).toContain(
      'core hard bans must leave at least one coach style terminal route',
    )
  })
})

describe('GYMTI scoring', () => {
  it('keeps personality and coach-style channels independent', () => {
    const hotblood = evaluateGymtiAnswers(GYMTI_QUESTIONNAIRE, [
      { questionId: 'q01_energy_after_work', optionId: 'q01_c_music' },
      { questionId: 'q02_gym_attraction', optionId: 'q02_b_cardio' },
      { questionId: 'q03_data_report', optionId: 'q03_b_skip_math' },
      { questionId: 'q04_desktop_cat', optionId: 'q04_a_flag' },
      { questionId: 'q07_intensity_view', optionId: 'q07_a_evidence' },
    ])
    const snarky = evaluateGymtiAnswers(GYMTI_QUESTIONNAIRE, [
      { questionId: 'q01_energy_after_work', optionId: 'q01_c_music' },
      { questionId: 'q02_gym_attraction', optionId: 'q02_b_cardio' },
      { questionId: 'q03_data_report', optionId: 'q03_b_skip_math' },
      { questionId: 'q04_desktop_cat', optionId: 'q04_d_tease' },
      { questionId: 'q06_correction_tone', optionId: 'q06_b_joke' },
      { questionId: 'q09_sarcasm_limit', optionId: 'q09_a_precise' },
    ])

    expect(hotblood.formalResult).toMatchObject({
      gymtiType: 'BOOM',
      secondaryGymtiType: null,
      recommendedCoachStyleId: 'hotblood',
    })
    expect(hotblood.earlyCompletion).toBe(true)
    expect(snarky.formalResult).toMatchObject({
      gymtiType: 'BOOM',
      secondaryGymtiType: null,
      recommendedCoachStyleId: 'snarky',
    })
    expect(snarky.earlyCompletion).toBe(true)
  })

  it('only emits a secondary GYMTI type for a positive runner-up within one point', () => {
    const result = evaluateGymtiAnswers(GYMTI_QUESTIONNAIRE, [
      { questionId: 'q01_energy_after_work', optionId: 'q01_c_music' },
      { questionId: 'q02_gym_attraction', optionId: 'q02_b_cardio' },
      { questionId: 'q03_data_report', optionId: 'q03_b_skip_math' },
      { questionId: 'q04_desktop_cat', optionId: 'q04_d_tease' },
      { questionId: 'q05_reminder_aversion', optionId: 'q05_b_self_judgment' },
    ])

    expect(result.rankedResult).toMatchObject({
      gymtiType: 'BOOM',
      secondaryGymtiType: 'KCAL',
      recommendedCoachStyleId: 'comedian',
    })
    expect(result.formalResult).toBeNull()
    expect(result.earlyCompletion).toBe(false)
  })

  it('matches the shared golden vectors for scores, exclusions, early completion, and formal results', () => {
    for (const vector of goldenVectors.vectors) {
      const prefix: typeof vector.answers = []
      for (const answer of vector.answers) {
        expect(
          legalNextQuestionCandidates(GYMTI_QUESTIONNAIRE, prefix)
            .map((question) => question.id),
          `${vector.id}: ${answer.questionId} must be reachable`,
        ).toContain(answer.questionId)
        prefix.push(answer)
      }
      const evaluation = evaluateGymtiAnswers(GYMTI_QUESTIONNAIRE, vector.answers)

      expect(evaluation.scoreState.gymtiScores, vector.id).toEqual(vector.expected.gymtiScores)
      expect(evaluation.scoreState.coachStyleScores, vector.id).toEqual(vector.expected.coachStyleScores)
      expect(evaluation.scoreState.excludedCoachStyleIds, vector.id).toEqual(vector.expected.excludedCoachStyleIds)
      expect(evaluation.earlyCompletion, vector.id).toBe(vector.expected.earlyCompletion)
      expect(evaluation.rankedResult, vector.id).toMatchObject(vector.expected.result)
      const isFormal = vector.expected.earlyCompletion || vector.answers.length === 8
      if (isFormal) {
        expect(evaluation.formalResult, vector.id).toMatchObject(vector.expected.result)
      } else {
        expect(evaluation.formalResult, vector.id).toBeNull()
      }

      if ('terminalCandidateIds' in vector.expected) {
        expect(
          legalNextQuestionCandidates(GYMTI_QUESTIONNAIRE, vector.answers)
            .map((question) => question.id),
          vector.id,
        ).toEqual(vector.expected.terminalCandidateIds)
      }
    }
  })

  it('allows a non-terminal q7 early completion after q5 and q6 remain unresolved', () => {
    const vector = goldenVectors.vectors.find((item) => item.id === 'late-early-completion-q7')
    if (vector === undefined) throw new Error('late q7 golden vector is required')

    expect(evaluateGymtiAnswers(GYMTI_QUESTIONNAIRE, vector.answers.slice(0, 5)).earlyCompletion).toBe(false)
    expect(evaluateGymtiAnswers(GYMTI_QUESTIONNAIRE, vector.answers.slice(0, 6)).earlyCompletion).toBe(false)
    expect(evaluateGymtiAnswers(GYMTI_QUESTIONNAIRE, vector.answers).earlyCompletion).toBe(true)
    expect(legalNextQuestionCandidates(GYMTI_QUESTIONNAIRE, vector.answers)).toEqual([])
  })
})

describe('GYMTI terminal guarantee', () => {
  const hardBanPath = [
    { questionId: 'q01_energy_after_work', optionId: 'q01_c_music' },
    { questionId: 'q02_gym_attraction', optionId: 'q02_b_cardio' },
    { questionId: 'q03_data_report', optionId: 'q03_b_skip_math' },
    { questionId: 'q04_desktop_cat', optionId: 'q04_d_tease' },
    { questionId: 'q05_reminder_aversion', optionId: 'q05_b_self_judgment' },
    { questionId: 'q06_correction_tone', optionId: 'q06_b_joke' },
    { questionId: 'q08_return_after_skip', optionId: 'q08_b_dumbbell' },
  ]

  it('filters terminal questions that target a hard-banned style and guarantees every visible option', () => {
    const candidates = legalNextQuestionCandidates(GYMTI_QUESTIONNAIRE, hardBanPath)

    expect(candidates.map((question) => question.id)).not.toContain('terminal_snarky_goal')
    expect(candidates).toHaveLength(6)

    for (const candidate of candidates) {
      expect(terminalQuestionGuaranteeViolations(
        GYMTI_QUESTIONNAIRE,
        hardBanPath,
        candidate.id,
      )).toEqual([])

      for (const option of candidate.options) {
        const evaluation = evaluateGymtiAnswers(GYMTI_QUESTIONNAIRE, [
          ...hardBanPath,
          { questionId: candidate.id, optionId: option.id },
        ])
        expect(evaluation.formalResult).not.toBeNull()
        expect(evaluation.formalResult?.recommendedCoachStyleId).not.toBe('snarky')
      }
    }
  })

  it('uses the first candidate in the canonical contract order for local fallback', () => {
    const candidateIds = legalNextQuestionCandidates(GYMTI_QUESTIONNAIRE, hardBanPath)
      .map((question) => question.id)

    const selected = deterministicLocalFallback(candidateIds)

    expect(selected).toBe(candidateIds[0])
    expect(deterministicLocalFallback([])).toBeNull()
  })

  it('narrows adaptive candidates by tied leaders before missing signal coverage', () => {
    const noSignalAnswers = [
      { questionId: 'q01_energy_after_work', optionId: 'q01_no_match' },
      { questionId: 'q02_gym_attraction', optionId: 'q02_no_match' },
      { questionId: 'q03_data_report', optionId: 'q03_no_match' },
      { questionId: 'q04_desktop_cat', optionId: 'q04_no_match' },
    ]

    expect(
      legalNextQuestionCandidates(GYMTI_QUESTIONNAIRE, noSignalAnswers)
        .map((question) => question.id),
    ).toEqual(['q05_reminder_aversion'])

    const partiallyResolvedAnswers = [
      { questionId: 'q01_energy_after_work', optionId: 'q01_c_music' },
      { questionId: 'q02_gym_attraction', optionId: 'q02_b_cardio' },
      { questionId: 'q03_data_report', optionId: 'q03_b_skip_math' },
      { questionId: 'q04_desktop_cat', optionId: 'q04_d_tease' },
    ]
    const candidates = legalNextQuestionCandidates(
      GYMTI_QUESTIONNAIRE,
      partiallyResolvedAnswers,
    )

    expect(candidates.length).toBeGreaterThan(0)
    expect(candidates.length).toBeLessThan(GYMTI_QUESTIONNAIRE.rules.adaptiveQuestionIds.length)
    expect(candidates.map((question) => question.id)).toEqual(
      GYMTI_QUESTIONNAIRE.rules.adaptiveQuestionIds.filter((id) => (
        candidates.some((question) => question.id === id)
      )),
    )
  })

  it('proves every reachable unresolved seven-answer state has a safe terminal route', () => {
    const stateSignature = (answers: Array<{ questionId: string; optionId: string }>): string => {
      const state = evaluateGymtiAnswers(GYMTI_QUESTIONNAIRE, answers).scoreState
      return JSON.stringify({
        answerCount: answers.length,
        answeredQuestionIds: answers.map((answer) => answer.questionId).sort(),
        gymtiScores: state.gymtiScores,
        coachStyleScores: state.coachStyleScores,
        excludedCoachStyleIds: [...state.excludedCoachStyleIds].sort(),
        noMatchCount: state.noMatchCount,
      })
    }
    let frontier: Array<Array<{ questionId: string; optionId: string }>> = [[]]
    let unresolvedSevenAnswerStates = 0
    const terminalRepresentatives = new Map<
      string,
      Array<{ questionId: string; optionId: string }>
    >()

    for (let answerCount = 0; answerCount <= 7; answerCount += 1) {
      const nextStates = new Map<string, Array<{ questionId: string; optionId: string }>>()
      for (const answers of frontier) {
        const evaluation = evaluateGymtiAnswers(GYMTI_QUESTIONNAIRE, answers)
        if (evaluation.earlyCompletion) {
          expect(answerCount).toBeGreaterThanOrEqual(5)
          expect(legalNextQuestionCandidates(GYMTI_QUESTIONNAIRE, answers)).toEqual([])
          continue
        }

        if (answerCount === 7) {
          unresolvedSevenAnswerStates += 1
          terminalRepresentatives.set(
            [...evaluation.scoreState.excludedCoachStyleIds].sort().join('|'),
            answers,
          )
          continue
        }

        const candidates = legalNextQuestionCandidates(GYMTI_QUESTIONNAIRE, answers)
        for (const candidate of candidates) {
          expect(candidate.phase).not.toBe('terminal')
          for (const option of candidate.options) {
            const nextAnswers = [
              ...answers,
              { questionId: candidate.id, optionId: option.id },
            ]
            nextStates.set(stateSignature(nextAnswers), nextAnswers)
          }
        }
      }
      frontier = [...nextStates.values()]
    }

    expect(unresolvedSevenAnswerStates).toBeGreaterThan(0)
    expect(terminalRepresentatives.size).toBeGreaterThan(0)
    for (const answers of terminalRepresentatives.values()) {
      const candidates = legalNextQuestionCandidates(GYMTI_QUESTIONNAIRE, answers)
      expect(candidates.length).toBeGreaterThan(0)
      for (const candidate of candidates) {
        expect(candidate.phase).toBe('terminal')
        expect(terminalQuestionGuaranteeViolations(
          GYMTI_QUESTIONNAIRE,
          answers,
          candidate.id,
        )).toEqual([])
        for (const option of candidate.options) {
          expect(evaluateGymtiAnswers(GYMTI_QUESTIONNAIRE, [
            ...answers,
            { questionId: candidate.id, optionId: option.id },
          ]).formalResult).not.toBeNull()
        }
      }
    }
  }, 15_000)

  it('proves every terminal option dominates any legal seven-answer score history', () => {
    const maximumPriorGap = (ids: readonly string[], scoreFor: (id: string, option: {
      gymtiScores: Record<string, number>
      coachStyleScores: Record<string, number>
    }) => number): number => {
      const options = GYMTI_QUESTIONNAIRE.questions.flatMap((question) => question.options)
      return Math.max(...ids.flatMap((targetId) => ids.map((competitorId) => {
        const minimumTarget = Math.min(...options.map((option) => scoreFor(targetId, option)))
        const maximumCompetitor = Math.max(...options.map((option) => scoreFor(competitorId, option)))
        return 7 * (maximumCompetitor - minimumTarget)
      })))
    }

    const gymtiGap = maximumPriorGap(
      GYMTI_TYPES,
      (id, option) => option.gymtiScores[id] ?? 0,
    )
    const coachStyleGap = maximumPriorGap(
      COACH_STYLE_IDS,
      (id, option) => option.coachStyleScores[id] ?? 0,
    )

    expect(GYMTI_QUESTIONNAIRE.rules.terminal.scoreBoost).toBeGreaterThan(gymtiGap)
    expect(GYMTI_QUESTIONNAIRE.rules.terminal.scoreBoost).toBeGreaterThan(coachStyleGap)

    for (const question of GYMTI_QUESTIONNAIRE.terminalBank) {
      const targetStyles = new Set<string>()
      for (const option of question.options) {
        const gymtiTargets = Object.entries(option.gymtiScores)
          .filter(([, score]) => score >= GYMTI_QUESTIONNAIRE.rules.terminal.scoreBoost)
        const coachStyleTargets = Object.entries(option.coachStyleScores)
          .filter(([, score]) => score >= GYMTI_QUESTIONNAIRE.rules.terminal.scoreBoost)

        expect(gymtiTargets).toHaveLength(1)
        expect(coachStyleTargets).toHaveLength(1)
        expect(option.isNoMatch).toBe(false)
        targetStyles.add(coachStyleTargets[0][0])
      }
      expect(targetStyles).toHaveLength(1)
    }
  })
})
