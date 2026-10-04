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
| `stage-world NAME PATH [--engine ENGINE] [--destructive-lab]` | Copy a local save into a new guarded instance with a content receipt | Yes |
| `lab-drive NAME CONTROLLER_ID` | Queue one bounded, one-shot drive command for the next lab run | Yes |

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
plugin source, Magnetar source/profile documents, and a hash receipt. `lab-drive` queues a command
for a positive controller entity ID, 0.25-25 meter distance, 0.1-5 m/s speed, and 60-3600 tick
timeout. Launch activation moves the command into that run's quarantine, so failed or completed
commands are never silently replayed by a later run.

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
