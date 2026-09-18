---
name: training-speech-understanding
version: 1.6.0
description: Extract all explicit training-action signals from full-source timestamped speech.
---

# Training speech understanding

只提取明确的训练动作或训练要求。平台片尾音效、平台名、账号、关注提示、
广告、口号和背景音乐歌词都不是动作。若整段转录只有这些内容，返回空 signals。

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
    }
  ]
}
```

## Rules

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
- Set `segment_role` to `follow_along` only when speech explicitly invites the
  viewer to perform the action in sync, and to `teaching_demo` only when speech
  clearly frames the interval as explanation, demonstration, or correction.
  Otherwise return `unknown`.
- A teaching segment's elapsed video time is not a training-duration parameter.
- Keep signals in source-video timeline order.
- Evidence text must be a short transcript phrase, not hidden reasoning.
- Extract at most three concise action-specific `tips` from explicit speech only.
  Each has `text` (2–80 characters, an EXACT continuous transcript excerpt, no paraphrase),
  `category` (setup/path/breathing/rhythm/caution), and `evidence`
  {type:"speech",start_seconds,end_seconds} inside one supporting utterance and
  this action's interval. Keep complete meaning, conditions and negation. Do not
  use knowledge, prescription numbers, medical/rehabilitation claims, pain advice,
  body data, load or promises. No clear safe instruction means [].
