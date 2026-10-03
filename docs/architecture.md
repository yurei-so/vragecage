# Architecture

## Purpose

VRageCage exists to make failures reproducible and cheap. It does not attempt to reimplement the
whole VRAGE engine, and it does not replace human evaluation of driving feel or presentation.

## Tier 1: deterministic micro-world

The micro-world owns portable logic: fixed-step vehicle models, planner/controller adapters,
scenario generation, exact oracles, fuzzing, shrinking, assertions, traces, and receipts. Runs must
be bounded by explicit tick and resource budgets. Every promoted regression carries its seed,
scenario document, expected outcome, and trace hash.

Tier 1 answers: "Is our logic internally correct and reproducible?"

## Tier 2: disposable real-engine worker

The worker launches Space Engineers Dedicated Server against a unique data directory and a
disposable world snapshot. The implemented lifecycle owns preparation, startup, readiness,
timeout, graceful shutdown, log capture, and hashed receipts. Once the bridge exists, each run will
install only the bridge/test mod and the mod under test.

Tier 2 answers: "Does it still work inside the real engine?"

The official Dedicated Server targets Windows. The approved Linux feasibility slice now runs the
unmodified server through user-scoped UMU/Proton and runs the unmodified 32-bit SteamCMD in a
private Bubblewrap mount namespace. It changes no system packages. A real smoke has loaded a world,
reached `Game ready...`, and shut down with a clean save. This proves the worker substrate, not the
bridge or a Voidwright scenario.

## Narrow bridge

The bridge is test instrumentation, not a production remote-control service.

- Server-authoritative session component; fail closed when not dedicated or not in an explicitly
  marked VRageCage world.
- Fixed allowlist of versioned commands. No arbitrary scripts, terminal actions, paths, or entity IDs.
- Local transport only. Prefer an append-only inbox/outbox beneath the worker's unique data path;
  use mod messages only for an attached test observer.
- Commands carry operation and scenario IDs. Duplicate operation IDs replay the original receipt.
- Monotonic event sequence numbers expose accepted, running, and terminal checkpoints honestly.
- Payloads are bounded. Large traces are written as artifacts and referenced by hash/path.

Initial command set: load fixture, reset, set destination, start, stop, and snapshot.

## Evidence model

Each engine frame should eventually expose, where the ModAPI permits it:

- grid/vehicle pose and linear/angular velocity;
- active controller state and destination;
- route identifier and current waypoint;
- bounded actuator summaries, including wheel propulsion/steering and thruster override;
- collision, stuck, timeout, manual-override, and reached-goal terminal states;
- relevant ownership, block, and grid state;
- game/mod versions, scenario seed, fixture hash, and log offsets.

Do not infer unavailable facts. A missing sensor is represented as unavailable, not zero.

## Failure promotion

1. Preserve the terminal receipt, trace, logs, fixture hash, seed, and world snapshot reference.
2. Re-run unchanged to classify deterministic versus intermittent behavior.
3. Shrink Tier 1 inputs automatically. Tier 2 shrinking is conservative: remove one fixture feature
   at a time and retain only reductions that reproduce the same terminal signature.
4. If the failure is expressible without VRAGE physics, translate it into a Tier 1 regression.
5. Keep the smallest real-engine reproducer as a Tier 2 regression either way.

## Human boundary

Automate correctness, authority, lifecycle, recovery, and reproducibility. Keep these human:

- whether vehicle behavior feels competent and predictable;
- UI, terminal controls, models, GPS/debug rendering, and accessibility;
- architecture, safety boundaries, feature intent, and the final definition of useful behavior.

## Stages

1. Harden the micro-world contracts, add property/oracle testing, and adapt Voidwright's pure core.
2. Define fixture and telemetry schemas plus a fake bridge for harness tests.
3. Build the server-side bridge mod with no external transport; prove world-marker and authority gates.
4. **Complete:** prove the isolated Linux engine worker via user-scoped UMU/Proton; retain the
   receipt and lifecycle gates.
5. Add one flat rover scenario end-to-end before terrain, air, hybrid, or combat cases.

## Primary references

- Keen Software House, [Space Engineers Dedicated Server guide](https://www.spaceengineersgame.com/dedicated-servers/)
- Keen Software House, [Space Engineers ModAPI documentation](https://github.com/KeenSoftwareHouse/SpaceEngineersModAPI)
- Keen Software House, [public ModAPI multiplayer implementation](https://github.com/KeenSoftwareHouse/SpaceEngineers/blob/master/Sources/Sandbox.Game/ModAPI/MyModAPIHelper_ModAPI.cs)

The public game-source repository is reference material with its own license restrictions; VRageCage
must not copy engine implementation into a standalone simulator. The micro-world models behavior
through independently authored contracts and equations.
