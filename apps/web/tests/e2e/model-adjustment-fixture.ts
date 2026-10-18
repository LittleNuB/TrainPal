import type { Page } from '@playwright/test'
import type { DraftItem } from '../../src/domain/types'
import type { AdjustmentIntent, TrainingExperience } from '../../src/domain/personalization'
import { proposePlanAdjustment } from '../../src/personalization/adjustment-policy'

// Synthetic provider output only for isolated UI tests; never installed by the application.
export async function mockModelAdjustment(page: Page): Promise<void> {
  await page.route('**/api/v1/plan-adjustments', async route => {
    const input = route.request().postDataJSON() as {
      input_fingerprint: string; plan_revision: number; intent: AdjustmentIntent;
      experience: TrainingExperience | null; energy: 'usual' | 'tired';
      safety_stop: boolean; available_minutes: number | null;
      actions: Array<Pick<DraftItem, 'id' | 'name' | 'mode' | 'sets' | 'reps' | 'durationSeconds' | 'restSeconds'> & {
        confirmed: boolean; feedback: 'too_hard' | 'just_right' | 'too_easy' | null;
      }>;
    }
    const proposal = proposePlanAdjustment({
      basePlan: { id: 'current', name: 'Synthetic', linkedPlanId: null, updatedAt: '',
        items: input.actions.map(action => ({ ...action, sourceRef: null,
          segment: { value: null, source: null }, weightKg: { value: null, source: null },
          confirmationStatus: action.confirmed ? 'confirmed' : 'pending' })) },
      intent: input.intent, trainingExperience: input.experience, energy: input.energy,
      availableMinutes: input.available_minutes, hasSafetyStopSignal: input.safety_stop,
      contextRevision: 0, generatedAt: '',
      signals: input.actions.flatMap(action => action.feedback
        ? [{ itemId: action.id, value: action.feedback, recordedAt: '2026-10-18' }] : []),
    })
    await route.fulfill({ json: {
      input_fingerprint: input.input_fingerprint, plan_revision: input.plan_revision,
      mode: 'model', summary: '合成测试建议。', changes: proposal.changes.map(change => ({
        action_id: change.itemId, field: change.field, value: change.after.value, reason: '合成测试理由。',
      })),
    } })
  })
}
