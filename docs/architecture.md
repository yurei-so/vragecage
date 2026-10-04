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
disposable world snapshot. The implemented lifecycle owns preparation, safe world import, startup,
readiness, bounded observation gates, timeout, graceful shutdown, log capture, and hashed receipts.
Imported saves are copied and hash-receipted; the operator's original is never run in place.

Tier 2 answers: "Does it still work inside the real engine?"

### Destructive lab authority

`stage-world --destructive-lab` grants one imported Magnetar fixture universal harness authority.
This is intentionally a world-level boundary rather than a grid ownership model: every grid inside
the copied world is an unsupported development target, while the source save remains untouched.
The authority is valid only when the instance contract, source import receipt, configured world
path, fresh launch run ID, and quarantine directory all agree. The engine owns and may rewrite the
contents of `World`, so authority metadata deliberately remains outside that mutable directory.

Each launch creates `Quarantine/<run-id>/` and passes that run identity to the engine. Generated
worlds, blueprints, screenshots, traces, and other exports are untrusted until a future explicit
release workflow promotes an individual artifact. A destructive-lab instance must never overwrite
its source save or silently publish Workshop content.

Commands are one-shot. Activation consumes a pending bounded command into the new run directory;
the command's operation ID, run ID, evidence, logs, package hashes, and terminal receipt remain
correlated even if compilation or startup fails before actuation.

The official Dedicated Server targets Windows. The Proton backend runs that unmodified server
through user-scoped UMU and runs the unmodified 32-bit SteamCMD in a private Bubblewrap mount
namespace. The optional Magnetar backend is a separate native-Linux route for unpublished local-mod
integration; it preserves a private Steam-shaped install/cache topology and loads only explicitly
staged packages. Neither changes system packages. Real smokes have loaded worlds, reached
`Game ready...`, and shut down with clean saves. A copied rover world has also loaded Voidwright
headlessly and produced contract-bound Land and Hybrid vehicle observations. This proves real
vehicle discovery, not actuator control or route completion.

## Narrow bridge

The bridge is test instrumentation, not a production remote-control service.

- Server-authoritative Magnetar plugin; fail closed outside a validated destructive-lab run.
- Fixed allowlist of versioned commands. No arbitrary scripts, terminal actions, paths, or target
  types; a bounded drive command names one positive controller entity ID inside the disposable fixture.
- The engine may directly control fixture grids under the universal harness principal. This is not
  a player, faction, or reusable production authorization identity.
- Local transport only. Artifacts remain beneath the active run's quarantine directory.
- Commands carry operation and scenario IDs. Duplicate operation IDs replay the original receipt.
- Monotonic event sequence numbers expose accepted, running, and terminal checkpoints honestly.
- Payloads are bounded. Large traces are written as artifacts and referenced by hash/path.

Current command set: one bounded drive-distance operation against an existing controller in the
copied fixture. Fixture lifecycle remains a worker concern rather than a general in-engine command.

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
4. **Complete:** prove isolated Linux workers through both UMU/Proton and native Magnetar; retain
   backend-specific receipt, package-integrity, and lifecycle gates.
5. **Complete:** copied-world Land and Hybrid discovery runs end-to-end with semantic receipt
   gates. A one-shot destructive-lab command moved a copied rover a bounded distance through the
   Magnetar server plugin, stopped it, and produced correlated operation/run/log/package receipts.
   Terrain-aware steering, Hybrid/AIR actuation, and combat remain later slices.

## Primary references

- Keen Software House, [Space Engineers Dedicated Server guide](https://www.spaceengineersgame.com/dedicated-servers/)
- Keen Software House, [Space Engineers ModAPI documentation](https://github.com/KeenSoftwareHouse/SpaceEngineersModAPI)
- Keen Software House, [public ModAPI multiplayer implementation](https://github.com/KeenSoftwareHouse/SpaceEngineers/blob/master/Sources/Sandbox.Game/ModAPI/MyModAPIHelper_ModAPI.cs)

The public game-source repository is reference material with its own license restrictions; VRageCage
must not copy engine implementation into a standalone simulator. The micro-world models behavior
through independently authored contracts and equations.
