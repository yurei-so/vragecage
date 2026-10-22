# Space Engineers automated test harness — dispatch review

Date: 2026-10-19
Status: local inventory and design plan; no service was started or changed for this review.

## Repository and current implementation

The requested project is already present at `/home/alu52/github/vragecage`. Its working tree
contains unrelated local changes, so this note is an additive review; no cleanup, reset, or commit
was performed. `/home/alu52/github/vragecage-profiler-prototype` is a separate profiling-data
prototype and should not be treated as the canonical harness checkout.

The existing design in `docs/architecture.md` already defines the two tiers:

1. **Fast micro-world.** `VrageCage.Core/SimulationRunner` executes fixed-timestep scenarios with
   bounded ticks, a simple 2D disk vehicle, acceleration/turn-rate limits, swept disk collision,
   JSONL telemetry, and scenario/trace hashes. `HeadingAwareGridOracle` supports exact path-cost
   comparisons. Current checked-in Core tests pass **13/13**; they include determinism, collision,
   bounds, and one exact-oracle case. A 100,000-case oracle comparison is described in the incoming
   dispatch history but is not present as a reproducible test in this checkout and should be
   recovered or rerun before relying on it as a release gate.
2. **Real engine.** `worker/` provides isolated Space Engineers Dedicated Server lifecycles,
   disposable world import, per-run quarantine, hashed receipts, Proton/UMU and Magnetar backends,
   plus a server plugin bridge. Existing bridge operations are bounded and tied to exact run,
   fixture, controller, camera, and operation identities. The current live V4 plugin command is a
   narrow coordinate-depth drive operation, not the full fixture-management API requested here.

## Bridge and evidence gaps

The existing harness already has useful safety properties: local worker ownership, copied-world
authority, no arbitrary script/terminal command surface, command/run correlation, bounded timeouts,
clean-stop receipts, and fail-closed source-save protection. Current implementation does not yet
provide a complete scenario lifecycle bridge for selecting a named scenario, loading/resetting a
fixture, spawning a known grid, setting a destination, starting/stopping Voidwright, and returning
a normalized terminal result. Keep those as explicit versioned operations over a per-instance local
transport; do not expose arbitrary entity creation, paths, scripts, or production-world controls.

Existing Voidwright observations include pose, linear velocity, sensor rays, selected wheel
overrides/angles, controller state, and run-bound preflight/operation IDs where the ModAPI exposes
them. The current record is not a subscription recorder and does not fully bind the harness run ID
to the mod's own run ID. Collision contact is currently reported unavailable; do not convert that
gap into a “no collision” result. A future recorder should use an explicit allowlist of grid IDs and
metrics, bounded cadence and byte/sample budgets, and per-record `(run_id, clock_id, tick,
sequence)` provenance. Split/merge/destruction, missing samples, and unsupported metrics need
explicit events/statuses. Do not infer per-grid CPU cost from grid size or server simulation ratio.

The profiler prototype (`/home/alu52/github/vragecage-profiler-prototype`) already specifies a
bounded subscribed-metric trace and CSV timeline export for synthetic data. It validates the
offline format only: it has not subscribed to a running engine, measured instrumentation overhead,
or established a common clock between HoloView and simulation ticks.

## Dedicated Server feasibility

Keen's current [Dedicated Servers guide](https://www.spaceengineersgame.com/dedicated-servers/)
documents a Windows PC and Windows-specific service setup; it does not describe a supported Linux
server product. This workstation's `mpai` worker has nevertheless run the game server in a
disposable environment through the repository's Proton/UMU and native Magnetar paths. Those are
tested local compatibility routes, not a claim of Keen-supported Linux operation. Keep the
engine/version/backend in each run receipt and continue treating host provisioning as a separate,
operator-owned task.

## Staged implementation plan

1. **Make Tier 1 reproducible.** Recover or rerun the stated 100,000 deterministic terrain/oracle
   comparison; save its generator, seed range, assertions, and summary. Add scenario minimization
   that preserves outcome class and a compact regression bundle containing the minimized scenario,
   seed, expected result, and trace hash. Add a portable steering/wheel-response model only where
   it is independently defined and calibrated; keep it clearly separate from VRAGE physics.
2. **Define the bridge contract.** Add a versioned scenario package with a fixed scenario ID,
   fixture digest, known entity catalogue, bounded conditions, expected terminal states, and
   exact per-run identities. Enumerate only load/reset, named fixture setup, destination, start,
   stop, and structured snapshot/result operations. Reject unknown IDs, stale run/operation IDs,
   over-budget values, and duplicate dispatches.
3. **Build engine instrumentation.** Keep the server-side test mod/plugin local to the disposable
   worker. Add selected-grid subscriptions and bounded event/metric capture; validate every field
   against the installed server assemblies. Record pose/velocity, controller/route state, actuator
   observations, grid/block changes, run markers, and only collision signals with a verified
   source. Preserve `unavailable` and `unsupported` distinctly from zero/false.
4. **Correlate and promote failures.** Map the harness run ID to the mod instance ID explicitly.
   Persist source JSONL, fixture and mod hashes, game/backend versions, seed, event sequence, clock
   identity, drop/truncation counts, and terminal receipt. Retry unchanged real-engine failures to
   classify repeatability, then remove fixture features one at a time. Promote engine-independent
   failures to Tier 1 and keep the smallest engine-dependent case as a Tier 2 fixture.
5. **Keep the human boundary clear.** Automate determinism, controller/path correctness, authority,
   startup/recovery, and structured result validation. Leave perceived driving quality, UI/GPS
   rendering, model quality, and usefulness to human review.

## Current blocker from the active V4 practice

The newly recorded open-area engine probe passed both run-bound preflights and applied wheel
overrides, but moved away from its target after initially closing slightly. It was stopped cleanly;
the full Ramp Bottom → Green Zone route remains unverified. This is a useful Tier 2 failure
example, but not yet a promoted Tier 1 regression because the steering/frame mapping is unresolved.
See `/home/alu52/github/voidwright/docs/land-v4-mod-owned-diagnostic-2026-10-19.md` for its exact
run and measurements.

## Review boundary

This is local design work only. No services were started, no live saves/configuration/permissions
were changed, and no commit or external publication was made. Open items are the reproducible
100,000-case artifact, the full enumerated bridge, verified collision source, metric-subscription
runtime, cross-clock mapping, and the V4 steering-direction failure.
