# Repository instructions

## Working principles

1. **Think before coding.** State material assumptions, inspect the relevant
   implementation and domain decisions, and surface ambiguity before it turns
   into code.
2. **Simplicity first.** Build the smallest complete vertical slice. Do not add
   speculative abstractions, queues, databases, or framework layers.
3. **Surgical changes.** Preserve existing work, keep changes inside the named
   scope, and avoid opportunistic rewrites.
4. **Goal-driven execution.** Define observable success, verify through public
   interfaces, and finish with proportional tests and a clean handoff.

## Before making changes

- Read `CONTEXT.md`, the ADRs that touch the change, and the relevant GitHub
  issue including its comments and labels.
- Use the domain terms exactly as defined in `CONTEXT.md`.
- If a requested change contradicts an ADR, call out the conflict instead of
  silently overriding it.

## Product and architecture boundaries

- The product is an independent Web-first training product. Local video import
  is the primary prototype entry; controlled videos are a clearly labelled
  fallback. Arbitrary URL fetching, platform cookies, login-state reuse, and
  feed scraping are out of scope.
- The core object is a **训练动作**, not a complete video. A video action keeps
  its source and demonstration time range; a self-created action may have none.
- The user sees one TrainPal Agent. A replaceable 内容理解 Provider produces
  structured source evidence; TrainPal deterministically orchestrates the
  训练编译、个性化调整 and 动作要点补充 Skills. Skills do not call each
  other, write the draft, or own the training state machine.
- Analysis Runs are explicit, cancellable, and recoverable for a short time on
  the same device. Page navigation, refresh, or an SSE disconnect must not
  cancel a run. Do not turn them into permanent or cross-device background
  jobs, and do not introduce runtime mock fallbacks.
- Do not copy source code or components from the GPL-3.0 historical demo.
- Analysis starts only after an explicit user action and covers the complete
  selected source or an explicit coverage-gap retry range. The product target
  is at most 10 minutes, but each deployment must expose and enforce its
  measured capability. The current competition deployment is verified for a
  complete source of at most 300 seconds; longer originals must be cropped
  outside the app before upload. Page load, playback, and seeking must not
  auto-create a run.
- The device-demo production profile uses one public analysis slot and no judge
  slot. Do not add a completed-run cooldown or request quota by session or peer
  IP; after terminal cleanup the same session may start again immediately. Keep
  the legacy access-session endpoint compatible, but do not expose a judge-code
  control in the product or let a judge cookie add capacity.
- The competition catalog contains exactly five manifest-backed controlled
  sources. Public UI may show their rounded durations only, and every confirmed
  start must create a fresh real Analysis Run rather than replay cached output.
- Progress uses real processed source time and read-only intermediate discovery.
  Reliable partial results must expose coverage gaps and a per-gap retry; a
  provider or system failure must never be presented as “no action evidence.”
- Source loops use an 展开式执行时间线 such as `A1 → B1 → A2 → B2`.
  Do not introduce nested loop state or use sets as a substitute for rounds.
- Keep the current production Provider. The completed native audio/video
  benchmark did not select a Seed/Qwen quality winner or validate the long-video
  production route; a switch requires new reviewed evidence and an ADR.
- TrainPal is the frozen product, Agent, and cat-coach identity. GYMTI v1
  wording and the seven shared cat-coach assets are versioned product inputs;
  change them through the questionnaire contract or asset pipeline rather than
  ad hoc page copy. The current product contract,
  four-value field provenance (`video | rule | personalized | user`),
  non-blocking coach role, and five training presentation states are not
  deferred. Keep the current calorie contract until a separate decision changes
  it.
- GYMTI model enhancement is independent of video-analysis admission. Production
  uses the configured Ark/Doubao seam with a non-blocking global concurrency of
  three; overflow, timeout, provider errors, or invalid output must immediately
  use the deterministic local result. Send only contract-approved stable IDs,
  semantic labels, score state, candidate IDs, and reason codes.

## Experience boundaries

- The current design source of truth is
  `docs/design/trainpal-mobile-experience-brief.md`. Use journey-specific pages,
  a mobile-first responsive layout, a warm journal theme outside training, and
  a dark high-contrast training stage. Do not recreate a Douyin feed, right-side
  action rail, fixed phone shell, or mandatory 9:16 video stage.
- Top-level navigation is `首页 / 训练 / 我的`. Analysis, plan editing,
  personalization, an active training session, and results are immersive
  subflows. Each page has one primary task and one visually dominant action.
- Source clips are reference playback unless reliable source rhythm exists.
  Do not expose or persist the legacy public segment-role enum. Uncertainty
  appears as a 待确认动作 or an explicit coverage gap.

## Security and data handling

- Never print, log, commit, expose to the frontend, or include secrets in test
  artifacts. Local credentials belong only in ignored `.env.local` files.
- The user-selected source video may persist only in browser storage on the
  current device. Server-side upload copies, audio, video windows, frames,
  transcripts, prompts, and model responses are transient; delete them on
  success, partial completion, cancellation, and failure.
- Logs may contain run/source IDs, stages, timing, version identifiers, provider
  request IDs, and redacted error codes only.

## Verification

- Test behaviour at public seams: HTTP/SSE APIs, store actions, and visible
  user flows. Fake providers are allowed only in tests and must fail closed in
  other environments.
- Run focused tests while working and the complete `pnpm check` plus E2E suite
  before delivery. Real cloud smoke tests stay local and produce sanitized
  status evidence only.

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues for `LittleNuB/TrainPal`. See
`docs/agents/issue-tracker.md`.

### Triage labels

Use the canonical labels `needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. Read the root `CONTEXT.md` and relevant
ADRs under `docs/adr/`. See `docs/agents/domain.md`.
