# CLI contract

`vragecage` is the stable cross-repository interface to the real-engine worker. It is a small
Python standard-library client; callers do not need the .NET projects or Proton details in their
own repository.

## Addressing the worker

Select a provisioned SSH target with `--host` or `VRAGECAGE_HOST`. For a durable local default,
create `~/.config/vragecage/config.json` containing `{"host":"user@worker-host"}`. Use `--local`
only while operating directly on a provisioned worker. The repository intentionally contains no
operator username, hostname, or network address.

The client deliberately passes `-F /dev/null` to SSH. This avoids depending on workstation SSH
configuration and keeps the worker address explicit.

## Commands

| Command | Purpose | Mutates worker state |
| --- | --- | --- |
| `doctor [--engine ENGINE]` | Check the selected backend's prerequisites | No |
| `prepare NAME [--engine ENGINE]` | Copy the Empty World, render its config, and record the backend | Yes |
| `run NAME` | Start a prepared instance as a transient user service | Yes |
| `wait NAME [observation gates]` | Wait for readiness and optional bounded observation evidence | No |
| `status NAME` | Return user-service state and the latest receipt, if one exists | No |
| `stop NAME` | Signal the named server, wait for its clean exit, and stop its user service | Yes |
| `receipt NAME` | Return the latest hashed server receipt | No |
| `smoke NAME` | Prepare, start, verify, stop, and receipt a disposable instance | Yes |
| `stage-mod NAME PATH --name MOD` | Atomically copy and register a local mod package | Yes |
| `stage-plugin NAME PATH --id PLUGIN` | Atomically copy and register a Magnetar lab plugin | Yes |
| `stage-passive-telemetry NAME PATH` | Stage the dedicated player-consented read-only telemetry plugin on a stopped Magnetar instance | Yes; plugin registration and a read-only observer contract |
| `passive-telemetry-status NAME` | Verify the observer contract and report the current run ID | No |
| `stage-world NAME PATH [--engine ENGINE] [--destructive-lab]` | Copy a local save into a new guarded instance with a content receipt | Yes |
| `lab-drive NAME CONTROLLER_ID [--reverse]` | Queue one bounded, one-shot drive command for the next lab run; reverse mode is tightly capped | Yes |
| `lab-play-recorder NAME CONTROLLER_ID RECORDER_ID [--duration-ticks TICKS]` | Play a saved Path Recorder route on the same disposable grid for a bounded window | Yes |
| `lab-follow-recorder NAME CONTROLLER_ID RECORDER_ID [limits]` | Follow its saved points with Voidwright's bounded wheel guidance | Yes |
| `lab-sensor-frame NAME CAMERA_ID` | Queue one bounded server-side depth frame for the next lab run | Yes |
| `lab-rotate-stator NAME STATOR_ID CAMERA_ID EXPECTED_NAME ANGLE` | Aim one named rotor with an attached camera at up to 0.5 RPM in a disposable fixture | Yes |
| `fixture-inspect NAME` | Read grids, Remote Controls, camera blocks, and player GPS targets from a lab copy | No |
| `fixture-opt-in NAME CONTROLLER_ID [--label LABEL]` | Add the Voidwright opt-in marker to one copied-world Remote Control | Yes |
| `passive-telemetry-export NAME RUN_ID --output-dir DIR` | Verify and copy a completed read-only Driving Lab JSONL and summary pair from one exact disposable Magnetar instance | Reads worker; writes local files |
| `lab-trial NAME [exact binding fields]` | Preview a fixed low-speed GPS trial and input digest; queue/start nothing | No command/world writes; lock metadata only |
| `lab-trial NAME [same fields] --execute --expect-plan-sha256 HASH` | Run the reviewed request once; stop only with fresh zero-human evidence, otherwise leave it running for operator review | Yes |
| `lab-trial-status NAME --fixture-id ID --request-id ID` | Read that fixture's durable request journal without replay | No |

## Higher-level lab trial

The workflow supports operator-observed testing in a disposable world. The
server plugin permits a connected human during the bounded one-shot actuation.
After arrival, the worker reports success independently from cleanup and leaves
the service running if it cannot prove a fresh zero-human state. Existing
low-level commands remain available; do not use them concurrently with a trial.

Choose one exact configured destructive-lab instance, fixture ID, ordinary
dynamic grid name, Remote Control, same-grid front camera and exact exporter
path. Entity IDs are canonical positive decimal strings. Generate a unique
lowercase 32-hex request identity and preserve it through preview/retry/status.
For example, after setting those values from fixture inspection:

```bash
vragecage --local lab-trial "$INSTANCE" \
  --fixture-id "$FIXTURE_ID" --request-id "$REQUEST_ID" \
  --controller-id "$CONTROLLER_ID" --depth-camera-id "$CAMERA_ID" \
  --grid-name "$GRID_NAME" --target "End Position" \
  --snapshot-relative-path "$SNAPSHOT_RELATIVE_PATH"
```

This returns `vragecage.lab-trial-preview.v1`, `plan` and `plan_sha256`.
No command is queued and no server starts. The path must be exactly
`Storage/.../agent-core-snapshot-v1.json` relative to this instance's `World`,
with no symlink traversal or path escape. The chosen source/config/world and
staged-package hashes, identity pins, GPS coordinates and limits enter the
digest. Optional binary sector cache and Magnetar loader/profile files are
included when present. A staging lock and before/after checks reject drift.

After approval for that operation, repeat the **same fields and request ID**
with `--execute --expect-plan-sha256 "$PLAN_SHA256"`. The digest is an input
binding, not a permission grant. Limits are fixed to 0.5 m/s, 3.1 m arrival,
1,800 simulation ticks and a 300-second observation wait. Targets are the
existing seven supported drive labels; `Observation Towers` is excluded.

Execution reserves a durable `.vragecage-trials/<request-id>.json` journal,
queues the bounded depth goal, identifies its new activated run, waits for
operation evidence, and stops only that same owned run if fresh zero-human
evidence permits. Plugin/package/contract/run/operation correlation and clean
shutdown remain separate requirements for success. Startup errors after
activation still reconcile ownership and attempt guarded cleanup.

Operator presence does not gate the bounded actuator command. If a human is
connected, exporter evidence is stale or missing, or another run replaces the
owned one, the wrapper does not stop that server. It records
`arrival-verified-running` when arrival is correlated, or `review-required`
when evidence is incomplete. Legacy terminal events that report a connected
human or unavailable player API still prevent automatic shutdown. Presence
checks and shutdown are not atomic, and this development command does not grant
ordinary-save authority. Do not create a new request identity to bypass an
unresolved journal.
An operation's correlated human/API-loss terminal event overrides a lagging
zero-player exporter sample. New optional `run --expected-operation-id ID
--expected-command-sha256 HASH` pins the bytes consumed by activation;
`stop --expected-run-id ID` refuses a replaced run. The higher-level workflow
always supplies these pins. A per-instance lifecycle lock serializes CLI
starts/stops; contention returns `lifecycle_busy` with exit 75. Operator-owned
direct helper/process commands remain outside that coordination contract.

Repeated completed execution returns the stored result without another start,
even when the original result was a failure. An unfinished journal returns a
review requirement without resuming. Reusing a request for changed inputs or
a reprepared fixture is refused. Inspect it without replaying:

```bash
vragecage --local lab-trial-status "$INSTANCE" \
  --fixture-id "$FIXTURE_ID" --request-id "$REQUEST_ID"
```

`vragecage.lab-trial-status.v1` is the last durable journal state, not current
service readiness or player presence. Successful status reads, including
`not-found`, exit 0. Execution exits 0 only for `success=true`; stopped
non-arrivals and review-required outcomes exit 1. The result preserves its
original `recorded_at_utc`, run/operation IDs and correlated receipt.

Both commands support the existing explicit `--host` transport. The worker
helper must be deployed alongside that CLI; no script or plugin is silently
uploaded. The Voidwright compact MCP profile wraps this same workflow with
operator-owned binding fields, keeping hosts and raw IDs out of model inputs.

## Existing wait and worker contracts

## Passive Driving Lab telemetry retrieval

For a human-driven Magnetar instance, use `stage-passive-telemetry` with the
separate `VoidwrightPassiveTelemetryPlugin` package. It creates a
`read_only_observation` contract bound to the exact plugin tree hash and
instance. It refuses an instance carrying destructive-lab authority and does
not create that authority. At launch, VRageCage verifies the package and issues
a fresh run ID; the observer plugin receives only this run identity and the
read-only scope. The separate assembly contains no movement or actuator
dispatch code. Staging requires the instance to be stopped because it updates
Magnetar's private plugin source/profile and stage receipts.

This path does not install or grant programmable-block control, change world
entity state, alter server access configuration, enroll a vehicle, or begin
capture. It writes only its bounded telemetry output into WorldStorage after
consent. The player must separately type `/vw telemetry consent` while
occupying the target cockpit. Multiplayer access, telemetry consent,
observation, and autonomous control remain separate capabilities.

The player starts capture in game with `/vw telemetry consent` after reviewing
`/vw telemetry help`; `/vw telemetry stop` revokes it. The server binds consent
to that authenticated player, their sole occupied cockpit grid, and the active
destructive-lab run. Capture is observation-only and bounded to 10 Hz, 15
minutes, 100,000 records, and 64 MiB. Keyboard/gamepad state and human intent
are unavailable. Consent expiry, binding changes, and plugin shutdown end the
buffer and write a JSONL file plus summary into the world save.

After the capture has ended, read the run ID issued for the current server
launch, then retrieve that exact run:

```bash
vragecage passive-telemetry-status "$INSTANCE"
vragecage passive-telemetry-export "$INSTANCE" "$RUN_ID" --output-dir ./driving-lab-export
```

The command accepts only an existing destructive-lab Magnetar instance and a
unique completed artifact pair bound to that run. It verifies record identity,
sequence, size limits, read-only labels, and transfer hashes, then writes the
two local files with owner-only permissions. It does not stop a server or
change worker artifacts. The output summary omits the player identity ID; the
consented source summary retains the ID needed to audit the binding.

## Existing wait and worker contracts

`wait` returns promptly with exit status 1 and a `lab_operation_failed` envelope
when the lab operation has ended unsuccessfully, including a simulation timeout,
stall, or attitude limit. The envelope contains the original receipt and its
operation status. This differs from the wait command's own wall-clock `timeout`:
the trial has already ended, and more waiting cannot make that trial succeed.
The server remains running until `stop` is called. `--observations` counts the
mod's contracted observations; `--require-observation` also searches lab
operation events, so an operation event alone does not satisfy a nonzero
`--observations` count.

For destructive-lab runs, readiness and operation evidence must come from logs
updated at or after the activation timestamp. Receipts expose this check as
`logs_updated_since_activation`. Missing or invalid activation timestamps fail
the check. Old log hashes remain visible for diagnosis, but old readiness and
shutdown markers cannot make a freshly launched trial pass.

`stage-mod` does not publish the package. On the official Steam multiplayer server, a local
`PublishedFileId=0` entry is rejected before compilation with `Local mods are not allowed in
multiplayer.` The server receipt treats this as fatal rather than reporting only a generic early
stop. That Proton path therefore needs a real Workshop item for a successful live load. The
Magnetar backend below is the explicit unpublished-loader route. Uploads use a unique temporary
incoming directory and remove it after either success or failure; only the atomically registered
instance copy remains. Staging is refused while the named instance service is active so receipts
cannot bind a newly replaced package to an older DLL already loaded in memory.

`stage-world` requires the checkpoint, world configuration, and sector files, rejects symlinks
and special filesystem entries before upload and again on the worker, refuses to overlay an
existing instance, and records file count, byte count, and a deterministic tree SHA-256 in
`.vragecage-world-import.json`. Uploads are bounded to 100,000 regular files and 4 GiB. The source
save is only read; the worker receives a temporary upload that is removed after import. Magnetar
is the default engine for imported integration fixtures.

With Magnetar, `--destructive-lab` additionally creates a non-transferable universal harness
authority contract for the disposable copy. It does not modify the source save. Every launch
validates the instance contract, import receipt, configured world path, and fresh run ID, then
allocates `Quarantine/<run-id>/`; artifacts
under that directory remain untrusted and there is currently no promotion command.

`stage-plugin` is available only for a stopped destructive-lab Magnetar instance. The source must
contain one root XML manifest and `vragecage.plugin.json`; VRageCage atomically updates the private
plugin source, Magnetar source/profile documents, and a hash receipt. A new Magnetar fixture must be
started and cleanly stopped once so Magnetar can create those source/profile documents; attempting
to stage a first plugin before that initialization returns an actionable refusal rather than a
traceback. `lab-drive` queues a command for a positive controller entity ID. Forward commands allow
0.25-25 meter distance, 0.1-5 m/s speed, and 60-3600 ticks. The optional `--reverse` mode backs
straight with steering held neutral and is deliberately tighter: at most 3 meters, 0.5 m/s, and
1800 ticks. It completes only after measured displacement along the controller nose reaches the
requested reverse distance, and reports wrong-direction motion separately. Launch activation moves
the command into that run's quarantine, so failed or completed commands are never silently replayed
by a later run.

`lab-rotate-stator` accepts only a named stator and camera on its attached top grid. The desired
angle is limited to -360–360 degrees, speed to 0.1–0.5 RPM, and runtime to 60–3600 simulation
ticks. The plugin verifies the block type, exact custom name, attachment, camera identity, enabled
state, and unlocked rotor before calling `RotateToAngle`; it stops the rotor on arrival, timeout,
or plugin disposal. The command is available only inside a destructive-lab fixture.

`lab-play-recorder` calls the game's Path Recorder `Play()` API for one exact recorder and Remote
Control on the same dynamic grid. Its window is bounded to 60-7200 simulation ticks (default 600),
and the plugin calls `StopPlay()` on normal completion and disposal. It rejects recording-in-progress,
missing/empty routes, unsupported controllers, and recorder/controller grid mismatches. This tests the
actual saved recording; it does not establish general autonomous obstacle planning. Playback
operation events and sampled truck positions are included in the Magnetar receipt.

`lab-follow-recorder` is a separate experimental path: it reads the same saved waypoint sequence,
checks the recorder is on the specified wheeled controller's grid, and lets Voidwright's lab-only
wheel adapter follow one traversal. Speed, waypoint arrival radius, total runtime, and stall stop
are bounded; the Path Recorder's vanilla playback flag is not enabled. A successful pass proves
only that this fixture followed this recorded route once. It is not general obstacle avoidance or
production authorization.

`lab-drive-zone` resolves an exact GPS label from the fixture. Its optional
`--offset-left` (bounded to -10 through 10 metres) shifts that target once at
controller acquisition, relative to the selected controller's initial heading
and local gravity frame. The receipt records the offset vector and resolved
target. This supports manually selected waypoint detours; it is not autonomous
obstacle detection or path planning.

For an explicitly experimental, front-camera depth-guided run, add
`--depth-camera-id <camera-id>` to `lab-drive-zone`. That opts into a separate
`drive-to-coordinate-depth` command and defaults to 0.5 m/s for 900 simulation
ticks (the contract hard-caps it at 0.8 m/s and 1,800 ticks). The controller
samples a 13-beam, 120-degree ray fan from that camera pose, steers through
three-beam corridors with at least 3.5 m clearance, and brakes/terminates on a
blocked fan, stale or invalid scan, target outside the fan, excess speed,
attitude excursion, loss of progress, or motion away from the target. It does
not reverse with only forward depth sensing. A terminal status is a handoff for
RGB/HoloView inspection; it never resumes itself. This is a lab-only reactive
steering proof, not general autonomous navigation.

`lab-sensor-frame` is deliberately not a renderer or screenshot tool. Dedicated Space Engineers has
no supported graphical output. The command casts a camera-aligned grid of physics rays and writes a
16-bit PGM depth map, an aligned signed 64-bit little-endian hit-entity ID raster, a signed 16-bit
XYZ surface-normal raster, and JSON metadata inside the run quarantine. A zero ID means no entity ID
was supplied for that ray; misses remain zero in the depth and ID maps and have a zero normal vector.
The metadata reports how many hits had entity IDs and how many named the camera's
own grid, but the sensor does not filter self-hits. It also records a bounded per-entity hit count,
the entity's display name, and runtime type for IDs present in the raster. These labels are diagnostic
metadata, not a semantic classifier or a safety decision. Receipts verify both raster hashes, the
PGM dimensions and actual nonzero-pixel count, per-entity ID count reconciliation, normal-vector
alignment/normalization, and—when present—the finite, internally consistent local gravity/up frame.
This reference is essential on spherical planets, where global Y is not local up. Bounded artifact
sizes and the matching completion log event are checked too. Width is limited to 4-64 pixels,
height to 4-36 pixels, the complete frame to 2,304 rays, range to 1-250 meters, and horizontal FOV to
10-120 degrees. Work is spread across simulation updates at no more than 128 rays per update. Pixel
value zero means no hit; nonzero values encode normalized distance through the
configured range. Receipts preserve the operation and artifact hashes. No game client, player join,
Steam client license, or player identity is involved.

`fixture-inspect` exposes bounded structural metadata from the disposable copy without starting the
engine. `fixture-opt-in` is the only offline sector mutation currently supported: while the service
is stopped, it identifies exactly one Remote Control by positive entity ID and prefixes its copied
grid identity with `[Voidwright]`. The source save is untouched. If VRAGE left an `.sbsB5` binary
sector cache beside the XML sector, the copy's cache is hash-receipted and moved into that edit's
quarantine directory so the engine cannot silently load stale state over the audited XML. The sector
hashes before and after the edit, controller/grid IDs, names, operation ID, cache receipt, and
timestamp are appended to a fixture-bound edit ledger.

`--engine` is selected at `prepare` time (`proton`, the default, or `magnetar`) and persisted in
`.vragecage-engine`. Follow-up commands read that marker. Magnetar stages a name-derived synthetic
Workshop ID in its private cache, adds that ID to its source/profile configuration, and leaves both
world `Mods` lists untouched.

`prepare` defaults to loopback and refuses an existing instance. The tested Steamworks build does
not become ready on loopback, so real-engine runs require `--engine-ready`. That flag selects
`0.0.0.0` behind the worker's explicit non-loopback gate; it does not configure firewall rules,
router forwarding, public visibility, or the Remote API. Use `--reprepare` only when overlaying an
existing instance is intentional.

`wait --observations N` prevents a readiness race when a mod emits evidence after the engine's
`Game ready` checkpoint. It accepts 0-32 and waits until the receipt both passes and contains at
least that many deduplicated observations. Repeat `--require-observation TEXT` to require each
bounded substring to appear in at least one observation, such as separate land and hybrid vehicle
evidence.

## Output and exit codes

Terminal command results are compact JSON with a versioned `schema` field. Progress emitted by
Steam, Proton, or systemd may precede the terminal JSON on long-running mutating commands. Agents
should consume the final JSON object and use the process exit status as the authoritative success
signal.

- `0`: requested operation or check succeeded;
- `1`: runtime check, readiness wait, smoke, or receipt did not pass;
- `2`: invalid CLI syntax;
- `64`: invalid or insufficiently authorized worker input;
- `70`: required worker component is missing;
- `73`: an existing path or instance would be overwritten;
- `75`: shutdown target is ambiguous or did not stop within its bound.

Receipts use `vragecage.server-receipt.v1` and include the engine version, bind endpoint, session
and readiness checkpoints, clean-shutdown evidence, fatal markers, byte length, and SHA-256 of the
source log. A running server can have `passed: true` with `clean_shutdown: false`; after `stop`, a
clean run must report both `passed: true` and `clean_shutdown: true`.

Magnetar receipts use `vragecage.magnetar-receipt.v1`. They bind the game and Magnetar logs and
hashes to the mod-stage receipt, current package tree hash, definition/script loader markers,
readiness, fatal errors, and separately observed shutdown state. A live receipt never claims a
clean shutdown that has not happened yet.

When a destructive-lab run carries a command, the same receipt remains non-passing until correlated
plugin evidence reaches the successful `complete` terminal state. Accepted, acquired, and running
events prove progress only; timeout, controller loss/unavailability, and unsupported-controller are
terminal failures. Server readiness and operation completion are deliberately separate facts.
If an offline fixture edit ledger exists, run activation verifies its identity, operation chain, and
the edited grid/controller identity against the stopped-world sector, then snapshots both ledger and sector
hashes. Receipts bind that activation snapshot and report the current post-run sector hash separately,
because VRAGE legitimately rewrites its own world files while saving.

A staged package may declare an optional bounded `observation_prefix` in
`vragecage.integration.json`. Matching log payloads are deduplicated and returned as at most 32
`observations`, with an explicit omitted count. The full source logs remain hash-bound; the compact
receipt is evidence for automation, not a replacement for forensic logs.

## Current boundary

The CLI controls the proven server substrate and one narrow destructive-lab bridge. It can stage a
contracted Magnetar plugin and queue a bounded, one-shot drive command for a controller already in
an imported fixture. It does not place vehicles, expose a general remote-control surface, promote
quarantined artifacts, or collect a full vehicle telemetry stream. New commands remain subject to
the bridge allowlist and receipt model described in the architecture.
