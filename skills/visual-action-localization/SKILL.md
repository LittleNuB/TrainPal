---
name: visual-action-localization
version: 1.6.0
description: Localize all exercise demonstrations in one continuous video chunk.
---

# Visual action localization

核心任务：逐段看清教学动作，按画面中的动作编号和动作变化分段，
并用简体中文给动作命名。动作名称不必在字幕里出现：清晰可见的运动方向、
关节运动和器械足以支持常见动作名称时，应给出名称；不要仅仅因为字幕
只有“第几个动作”而返回 null。只有画面确实不足以区分动作时才用 null。
保留可辨认的握法、姿势和器械差异，不推断看不清的变式或训练效果。

## Input

A continuous video clip plus JSON metadata describing its clip-local timeline.
The clip is one overlapping chunk of a controlled source video. The caller,
not the model, converts returned times first to the analysis-range clock; the
run manager later adds the source-range offset exactly once.

## Output

Return JSON only:

```json
{
  "segments": [
    {
      "action_name": "string or null",
      "start_seconds": 0,
      "end_seconds": 0,
      "visual_cue": "short observable cue",
      "segment_role": "follow_along | teaching_demo | unknown",
      "sequence_label": "visible exercise number or null",
      "is_training_content": true,
      "text_parameters": null
      ,"tips": []
    }
  ]
}
```

## Rules

- Output clip-local seconds within the supplied timeline. Never add or guess a
  source-video offset.
- Inspect the complete continuous video clip and identify every observable
  exercise demonstration, keeping segments in timeline order.
- First distinguish introductory teasers, promotional/posing footage and end
  cards from the instructional sequence. Exclude clearly non-instructional
  footage from segments, or set `is_training_content` to false. Do not exclude
  a real exercise just because it appears at the beginning or has no caption.
- Read visible exercise numbers/headings throughout the clip BEFORE grouping
  movements. Every distinct numbered exercise must have its own segment and
  `sequence_label`, even when the camera, apparatus and muscle group are the same.
  Preserve changes in grip, palm direction, equipment and movement direction.
- Merge only consecutive moments of the SAME exercise in the SAME numbered
  section. Never merge across different headings or a clear movement change.
- Do not claim precision beyond what is visually observable.
- Name identifiable movements in natural Simplified Chinese, preserving grip,
  posture and equipment modifiers. Use visible motion and readable action labels
  together. Do not output English when a reliable Chinese equivalent exists.
  Do not reduce distinct variations to a generic exercise family. If identity
  remains ambiguous, keep the name `null`; a numbered heading alone is not a name.
- Read explicit training instructions displayed on screen. `text_parameters`
  may contain ONLY readable instructions attached to this exercise: `mode`,
  `sets`, `reps`, `reps_max`, `duration_seconds`, `rest_seconds`. Missing values
  stay null. For a repetitions range, reps is its lower bound and reps_max its
  upper bound (e.g. 8-12 repetitions is 8 and 12); never silently choose one value.
  An exact repetition count has reps_max null. Mode is reps or duration when
  supported by the instruction. If no readable instruction exists, use null.
  Do not infer these values from repetitions performed, clip length, a countdown
  without training context, model knowledge, promotional claims or creator load.
  Keep only a short observable cue, not full subtitles or account information.
- Set `segment_role` only from observable timeline evidence: use `follow_along`
  for a sustained interval presented for synchronous execution,
  `teaching_demo` for explanation, demonstration, or correction, and `unknown`
  whenever the role is not visually clear.
- Never convert the elapsed length of a teaching demonstration into a training
  duration.
- Do not infer body data, load, injury risk, exercise quality, or effectiveness.
- Visual cues describe visible evidence, not hidden reasoning.
- Extract at most three short action `tips` ONLY from clearly readable captions,
  not inferred posture or world knowledge. Each has `text` (2–80 characters,
  faithful complete caption excerpt), `category` (setup/path/breathing/rhythm/caution),
  and `evidence` {type:"visual",start_seconds,end_seconds} in clip-local time
  inside this action segment where the caption is visible. Keep negation and
  conditions; no medical, pain, rehabilitation, load or effectiveness advice.
  Unknown or unreadable captions mean []; do not fabricate tips.
