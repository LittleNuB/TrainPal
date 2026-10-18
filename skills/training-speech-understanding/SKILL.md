---
name: training-speech-understanding
version: 2.1.0
description: Extract all explicit training-action signals from full-source timestamped speech.
---

# Training speech understanding

只提取明确的训练动作或训练要求。平台片尾音效、平台名、账号、关注提示、
广告、口号和背景音乐歌词都不是动作。若整段转录只有这些内容，返回空 signals。

通读时分别核对热身、正式训练与收尾附加活动。明确建议的热身、放松或可选有氧，
即使没有动作编号或画面示范，也属于训练信号；保留“可以”“练完后”等条件在
evidence_text，不把它变成必做。片尾如果提出新的附加训练，必须保留；只有复述
前面已经说明的动作才是回顾。标题中的正式动作数量不是信号数量上限。

## Input

A JSON object containing the full source-video range and timestamped transcript
utterances. Times are seconds on the source-video clock.

## Output

Return JSON only:

```json
{
  "signals": [
    {
      "action_name": "string",
      "sets": null,
      "reps": null,
      "duration_seconds": null,
      "rest_seconds": null,
      "start_seconds": 0,
      "end_seconds": 0,
      "evidence_text": "short supporting phrase",
      "tips": [],
      "segment_role": "follow_along | teaching_demo | unknown",
      "is_training_content": true
      ,"content_scope": "individual_action"
    }
  ]
}
```

## Rules

- `content_scope` 区分独立执行与上下文：`individual_action` 是有具体动作方式，
  或本身具有明确独立执行时长/次数的活动；`general_context` 是阶段目的、章节概括
  或器械建议，没有说明具体怎么动，也没有独立执行要求。与训练相关不等于另一个动作。
  概括多个器械用于同一准备阶段的原话，如果没有逐个说明动作，不要拼成一个组合动作
  或猜出细分动作；保留完整原话但标 `general_context`。真实连续组合动作、明确的独立
  轮次与有明确时长的附加活动仍为 `individual_action`。概括说明仅作完整来源上下文，
  不编译为独立执行；不能通过此分类省略来源明确的参数或同步跟练要求。

- 每条 signal 对应一个动作或一种明确的活动。原句并列介绍几个独立动作时分别提取，
  可以引用同一完整语句时间，不猜测各自更细的起止点，不把并列名称拼成新动作。
  只有原话明确把多个阶段组成一个连续组合动作时，才保留为一个组合；器械列表
  本身不是动作要求。仅有“热身”“激活”等目的而没有具体活动时，不单独加一个动作。
- Extract only actions and parameters explicitly stated in the transcript.
- Set `is_training_content` false for a non-training mention if included at all;
  preferably omit it. A platform jingle or name must never become an exercise.
- Preserve missing fields as `null`; never invent a prescription.
- `duration_seconds` and `rest_seconds` are ALWAYS seconds, not the numeric
  token copied from speech. Resolve the spoken unit before filling either
  field: an explicit 20-minute activity is `duration_seconds: 1200`, whereas
  an explicit 20-second activity is `duration_seconds: 20`. Do not use the
  utterance or demonstration length as the activity duration. An unspecified
  unit or ambiguous range is not permission to guess a single seconds value.
- Keep activity duration and rest separate, and retain conditions or negation
  when deciding whether a statement actually prescribes the extracted action.
  A duration conversion does not turn a conditional suggestion into an
  unconditional instruction or a negative example into a training requirement.
- Treat spoken action labels as identifiers: preserve every distinguishing
  modifier such as seated, cross-body, drag, or hammer-grip. Never shorten a
  qualified label to a generic movement family such as curl or row.
- Keep sets, repetitions and duration in their parameter fields, not as an
  added prefix or parenthetical suffix in `action_name`. This does not permit
  removing a modifier that distinguishes the actual exercise or variation.
- Prefer a natural Simplified Chinese action label. Translate an explicitly
  spoken English exercise term only when the equivalent is reliable, retaining
  ALL posture, grip, equipment and direction modifiers. If no reliable Chinese
  equivalent is known, preserve the original term rather than invent a translation.
- Never extract or infer creator weight, user weight, body measurements, injury
  risk, exercise quality, or calories.
- Extract every distinct training action explicitly stated across the full source.
- Read the entire transcript before choosing occurrences. An opening outline,
  an umbrella label for several later exercises, or an ending list summarizing
  already explained exercises is not another execution. Extract the actual
  teaching occurrence once; preserve a later explicitly instructed round.
  Equipment adjustment alone is not a training action. A compound overview
  such as "band and roller warmup" must not become an additional action on top
  of the separately explained band and roller movements. Keep optional activities
  when actually proposed, preserving the condition in evidence_text.
- Set `segment_role` to `follow_along` only when speech explicitly invites the
  viewer to perform the action in sync, and to `teaching_demo` only when speech
  clearly frames the interval as explanation, demonstration, or correction.
  Otherwise return `unknown`.
  A recommendation such as "first do a warmup" is not a synchronous execution cue.
  Explaining what to do before or after a workout remains teaching_demo; only
  explicit real-time guidance to follow this interval supports follow_along.
- A teaching segment's elapsed video time is not a training-duration parameter.
- Keep signals in source-video timeline order.
- Evidence text must be a short transcript phrase, not hidden reasoning.
- Extract at most three concise action-specific `tips` from explicit speech only.
  Each has `text` (2–80 characters, the EXACT FULL text of one transcript utterance,
  including its punctuation, with only surrounding whitespace removed; no paraphrase),
  `category` (setup/path/breathing/rhythm/caution), and `evidence`
  {type:"speech",start_seconds,end_seconds} using that utterance's full time range,
  entirely inside this action's interval. Never select a substring, split at commas,
  remove a precondition, negation or side qualifier, or join separate utterances.
  If the full utterance is longer than 80 characters, depends on missing context,
  refers to multiple actions, or its attribution is uncertain, omit the tip.
  ASR utterance boundaries alone do not prove a complete sentence or correct audio
  recognition. Keep complete meaning, conditions and negation. Do not
  use knowledge, prescription numbers, medical/rehabilitation claims, pain advice,
  body data, load or promises. No clear safe instruction means [].
