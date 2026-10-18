---
name: visual-action-localization
version: 1.8.0
description: Localize all exercise demonstrations in one continuous video chunk.
---

# Visual action localization

核心任务：逐段看清教学动作，按画面中的动作编号和动作变化分段，
并用简体中文给动作命名。动作名称不必在字幕里出现：清晰可见的运动方向、
关节运动和器械足以支持常见动作名称时，应给出名称；不要仅仅因为字幕
只有“第几个动作”而返回 null。只有画面确实不足以区分动作时才用 null。
保留可辨认的握法、姿势和器械差异，不推断看不清的变式或训练效果。

先通看完整片段，判断每段画面在教学中的用途，再提取训练动作。片头介绍健身房、
器械环境、肌群知识或课程主题时，作为背景出现的器械操作、展示和试看动作，
不构成独立的训练安排；不能因为人物在运动，就另列为一个训练动作。结合前后
标题、教学转折和具体训练要求判断，不以时间位置或是否有编号作为唯一依据。
片尾重放配合方案总表、总结或告别时，不把背景中的旧动作再列一次。真正的
热身、放松和后续训练轮次仍然保留。

热身属于实际训练：有具体热身或放松动作的画面，即使没有动作编号、没有组数或
次数，也必须提取，缺少的参数留 null。标题列出的正式动作数量不是整个视频的
动作数量上限。输出前检查正文前后实际做过的热身、放松是否被误当成背景展示。

短暂热身画面只能看清器械或大类、看不清具体运动路径时，使用有依据的大类名称，
不要猜测肩外旋、内旋、核心等具体部位或变式；正文清晰可辨的变式仍须保留。
名称与运动方向必须相容，不拼接互相矛盾的训练部位和动作名称。
一个 segment 只对应一个动作。同一“热身”等章节内先后改变器械与运动方式时，
分别定位独立动作，不用章节标题或并列器械名称将它们合成一个动作；只有实际
连续演示一个明确的组合动作时才作为整体。动作编号只是分段依据之一。

## Input

A continuous video clip plus JSON metadata describing its clip-local timeline.
The clip is one overlapping chunk of a controlled source video. The caller,
not the model, converts returned times first to the analysis-range clock; the
run manager later adds the source-range offset exactly once.

## Output

Return JSON only:

The following is a fictional structure example, not source evidence. Never copy its values into the analysis;
derive names, cues, ordinals, times, parameters and tips only from the supplied video.
The example uses unknown role, no visible ordinal, no readable training parameters
and no reliable caption tip; these are example values, not defaults for real observations.

```json
{
  "segments": [
    {
      "action_name": "合成动作",
      "start_seconds": 0,
      "end_seconds": 1,
      "visual_cue": "虚构结构示例，不是视频观察",
      "segment_role": "unknown",
      "sequence_label": null,
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
- `sequence_label` is a transcription of an explicitly visible exercise heading,
  NOT a position in your output array. This clip can begin halfway through an
  exercise: never number its segments 1, 2, 3 by appearance, restart numbering
  at the clip boundary, or infer a missing number from neighbouring exercises.
  Copy a readable exercise ordinal exactly, including any side/round/variation
  qualifier. If no exercise ordinal is visible in this segment, use null.
  Repetition counts, set counts, countdowns and technique-step numbers are not
  exercise ordinals. A null ordinal does not mean the exercise should be omitted.
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
  Showing someone doing an exercise is not by itself follow_along. Edited examples
  with technique captions are teaching_demo. Follow_along requires a continuous
  viewer-paced execution cue (such as a training countdown or repetitions paced
  for the viewer); an imperative caption or a brief warmup demonstration is not
  sufficient. If the intended pace is unclear, use unknown.
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
