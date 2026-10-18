# Exercise reference seed: source, scope, and license

This directory's `exercise_reference_seed.json` contains eight text-only records
selected from `hasaneyldrm/exercises-dataset`, pinned to commit
`7455efae41b330c265e7cd4b78dfa848e7ce5ebd`.

- Source JSON: <https://github.com/hasaneyldrm/exercises-dataset/blob/7455efae41b330c265e7cd4b78dfa848e7ce5ebd/data/exercises.json>
- Source license: <https://github.com/hasaneyldrm/exercises-dataset/blob/7455efae41b330c265e7cd4b78dfa848e7ce5ebd/LICENSE>
- Source notice: <https://github.com/hasaneyldrm/exercises-dataset/blob/7455efae41b330c265e7cd4b78dfa848e7ce5ebd/NOTICE.md>

Source IDs: `0238`, `0237`, `0276`, `0383`, `0602`, `0601`, `0662`, `0993`.
Only the source ID, English name, equipment label, and English instruction text
are reproduced. English instructions are copied without rewriting. Chinese
names, aliases, and discriminators are TrainPal reference annotations, separately
marked `agent_reviewed_not_expert`; they are not upstream translations or
professional exercise guidance. Shared aliases are deliberate candidate-retrieval
hints, not proof that two variants are equivalent.

No images, GIFs, videos, image paths, media IDs, or media URLs are distributed in
the JSON. The media exception below is reproduced solely to retain the complete
upstream license and make the exclusion explicit; it does not authorize media use.
The upstream notice explicitly places non-media exercise names, categories, body
parts, equipment, targets, muscle groups, and multilingual instructions under MIT.
This records the repository's public license declaration, not an independent
guarantee of every upstream ownership claim.

## Quality and use boundary

All records are `reference_only: true`. This small seed is not a professionally
validated gold standard, a complete action catalog, or evidence of what a user's
video says or shows. Do not derive source timestamps, counts, sets, rest, or video
reminders from it, or establish action identity solely from a catalog match.
Do not substitute these instructions for
the original source utterance. A lookup may return multiple candidates or none;
uncertain names, variants, and equipment remain uncertain.

The source's Chinese translations were not imported because the audit found
meaning-affecting defects: record `0238` translates a cable bar as a barbell;
record `0279` omits the elevated-feet distinction and repeats a phrase. The source
also has English inconsistencies outside this selection: `0240` is named
"supine" while its instructions say "face down". These observations are not a
complete quality audit. Pinning the source does not establish factual accuracy.

## Complete upstream LICENSE at the pinned commit

```text
MIT License

Copyright (c) 2026 Hasan Emir Yıldırım

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation and data files (the "Software"),
to deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

------------------------------------------------------------------------------
MEDIA EXCEPTION
------------------------------------------------------------------------------

The MIT license above covers ONLY the code, tooling, dataset structure, and
instruction text/translations in this repository.

It DOES NOT cover the exercise media in the `images/` and `videos/`
directories. That media is © Gym visual (https://gymvisual.com/) and is
included here with the rights holder's written permission, at 180×180
resolution, and must retain the attribution "© Gym visual —
https://gymvisual.com/". Its use and reuse are governed by Gym visual's Terms
& Conditions (https://gymvisual.com/content/3-terms-and-conditions-of-use) and
by `NOTICE.md` in this repository — NOT by the MIT license above. Cloning this
repository does not grant you any license to the media; obtain your own from
Gym visual.
```
