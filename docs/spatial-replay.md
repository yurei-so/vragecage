# Spatial replay and event timeline

`worker/spatial-replay-bundle.py` creates a portable offline replay from the preserved Blue Truck safe-zone A/B evidence. It never launches or edits a game world. The output contains:

- `manifest.json`: run and fixture identity, available-field counts, source hashes, and measurement interpretation.
- `samples.ndjson`: normalized pose and speed samples. Missing tick, orientation, velocity vectors, and simulation time stay `null`.
- `events.ndjson`: startup, movement, safe-zone relation, discontinuity, no-command, sample-sequence/gap, and shutdown markers with evidence references.
- `entities.json`: saved safe-zone geometry, Blue Truck and wheel-subgrid centers, and the explicit distinction between saved topology and runtime observations.
- `evidence/`: hash-verified copies of source JSON, placement audits, saved world snapshots, and optional original stopped-run server logs.
- `index.html`: self-contained top-down and oblique schematic replay with playback, frame stepping, event navigation, filters, search, zoom, orbit, focus, pan, and evidence detail.

## Reproduce the Blue Truck case

The original stopped-run server logs are in the VRageCage instance directories on the worker host. Copy them read-only to a workstation, then run:

```sh
python3 worker/spatial-replay-bundle.py \
  --enabled-log /path/to/SpaceEngineersDedicated_20261021_181253507.log \
  --disabled-log /path/to/SpaceEngineersDedicated_20261021_181428992.log \
  --output artifacts/black-box-blue-truck-2026-10-21
```

To build from the preserved JSON summaries only, omit both log arguments. The command refuses to overwrite a non-empty output directory. It copies each source artifact, records its SHA-256, and verifies the copy. The generated HTML has no network or server dependency and can be opened directly in a browser.

When a source log is supplied, `events.ndjson` also includes one `LOG_PARSE_COVERAGE` event per log. It reports total lines, lines matched by the current event-marker rules, and unclassified lines. Unmatched raw log text remains in the hash-verified evidence copy rather than being duplicated into the timeline.

## Measurement rules

- The imported save pose is a reference marker, not a runtime sample.
- The dashed imported-to-first-runtime connector is an unobserved offset, not a path.
- Net displacement is endpoint distance. Integrated path length sums only adjacent runtime samples whose source-log interval is at most two seconds; larger or unparseable intervals are excluded and marked as gaps.
- The dedicated log timestamps are preserved verbatim but have no verified timezone. They are not presented as UTC. Simulation ticks and simulation time were not present in these captured lines.
- Wheel state values are captured as observed properties from the log. They do not establish that the wheels caused physical motion.
- Safe-zone inside/outside markers are derived from saved sphere geometry and the two poses. No boundary crossing is claimed across the missing interval.
- The oblique view is a schematic of grid/subgrid centers and conservative saved extents, not an oriented block wireframe. Runtime subgrid transforms, grid orientation, terrain, ownership changes, and safe-zone changes are not preserved in this case evidence. HoloView virtual captures from a different run are intentionally not attached as if they were synchronized evidence.

## Safe-zone A/B interpretation

The enabled and disabled source variants differed only in `SafeZone.Enabled`. Both source fixture snapshots, placement audits, and server logs are retained in the bundle. In both conditions the first usable runtime pose was roughly 463 m from the imported Blue Truck position. The disabled run's first sample reported nonzero linear and angular speed before any control command; later samples show it settling. The enabled run's retained sample window reports stationary pose and zero speed after the unexplained offset, but the transition that produced the offset is unobserved. The experiment therefore cannot determine whether safe-zone state affected propulsion.

## Follow-up instrumentation

The next capture revision should write simulation tick and simulation time at each pose sample, a monotonic source sequence and drop count, quaternion orientation, linear and angular velocity vectors, per-wheel state, command lifecycle, physics initialization, safe-zone state and geometry, ownership, and grid topology events. Keep passive capture opt-in and bounded. The current bundle is a replay of what the preserved evidence supports, not a substitute for those missing runtime observations.
