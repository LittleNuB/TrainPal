# Command-timeout cleanup repair (#17)

## Scope and baseline

- Repository: LittleNuB/TrainPal.
- Fixed point: e504bd77b83cd9759e3fb89cad35c7022346ba3f (Draft #16).
- Main at start: d0d6a49d953ce25d7daa152e4a026d54710d3abb; it includes #15.
- This repair is stacked on #16 so its E2E tests use one worker for the shared
  analysis slot. It does not change production capacity or command deadlines.
- No Provider calls, real-media uploads, deployment, image build/publish, or
  changes to the old dirty worktrees.

## Reproduction and cause

Public seam: `LocalMediaProcessor.prepare`. The existing timeout test uses two
real local sleeping Python processes in place of FFmpeg; no actual user video.
The strengthened test delays only the OS kill boundary: immediate, video slower
(0.20 / 0.05 seconds), or audio slower (0.05 / 0.20 seconds). Command timeout
remains 0.05 seconds. Executable discovery is pinned to the current Python binary
so imageio's own executable probe cannot be mistaken for an extraction process.

Reproduction command:

```powershell
uv run --project services/analysis-api pytest services/analysis-api/tests/test_media_processor.py -k command_timeout_stops -q
```

Before the repair, the minimized test produced **1 passed / 2 failed** in two
successive invocations: after the timeout reached the caller, the slower child
still had `returncode is None`. A temporary targeted probe confirmed cancellation
interrupted the wait for process stopping while `poll()` also remained `None`;
this was not merely a stale return-code field. The probe has been removed.

Both extraction tasks enter their own timeout cleanup. The first one to finish
raises its timeout; `prepare` then cancels its unfinished sibling. That sibling's
`asyncio.timeout` has already consumed its cancellation, so the #15
`task.cancelling()` guard correctly does not identify it as cancellation cleanup.
The new cancel interrupts the sibling's awaited cleanup thread. The thread may
eventually finish, but the caller and temporary-directory scope return too early.

## Repair and contract

Process cleanup now has an owned task. Its caller shields it and continues waiting
even if cancellation arrives while stopping, then propagates cancellation after
cleanup completes. Shielding without waiting would still leave detached cleanup.
Existing kill / native fallback / process wait / communication cleanup logic and
deadlines are unchanged. The #15 parent-cancellation guard is unchanged.

The timeout regression still rejects any yielded prepared media and verifies both
processes exited, the temporary directory is gone, and the public cleanup monitor
is clean. The existing unstoppable-process scenario now covers both cancellation
and timeout: timeout reports `extraction cleanup failed`, and the monitor remains
dirty rather than claiming successful cleanup. Cancellation retains cancellation
semantics; the monitor continues to expose any surviving process.

## Verification

- Focused media processor suite: **19 passed**.
- Timeout, cancellation, unstoppable process and native-fallback cases:
  **20 consecutive invocations × 9 cases = 180 passed**.
- Full local `pnpm check`: passed lint, type checks, **204 web tests / 433 API
  tests**, and production build.
- Local `pnpm test:e2e`: **22 passed**, using the inherited single-worker setting.
- Independent Standards/Spec review and Linux CI: pending at this local
  verification checkpoint; final receipts will be recorded on the Draft PR.

This fixes a cleanup lifecycle bug, not analysis accuracy, analysis speed or
frontend density. Those remain separate issues. A future cleanup change should
retain asymmetric two-child tests; single-child or equal-speed tests missed the
coordination failure.
