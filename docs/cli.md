# CLI contract

`vragecage` is the stable cross-repository interface to the real-engine worker. It is a small
Python standard-library client; callers do not need the .NET projects or Proton details in their
own repository.

## Addressing the worker

The default SSH target is the provisioned mpai host, `alu52@10.0.0.182`. Select a different target
with `--host` or `VRAGECAGE_HOST`. Use `--local` only while operating directly on a provisioned
worker.

The client deliberately passes `-F /dev/null` to SSH. This avoids depending on workstation SSH
configuration and keeps the worker address explicit.

## Commands

| Command | Purpose | Mutates worker state |
| --- | --- | --- |
| `doctor` | Check the provisioned server, prefix, launcher, Steam SDK, and worker root | No |
| `prepare NAME` | Copy the bundled Empty World and render a guarded instance config | Yes |
| `run NAME` | Start a prepared instance as a transient user service | Yes |
| `wait NAME` | Wait for a passing real-engine readiness receipt | No |
| `status NAME` | Return user-service state and the latest receipt, if one exists | No |
| `stop NAME` | Signal the named server, wait for its clean exit, and stop its user service | Yes |
| `receipt NAME` | Return the latest hashed server receipt | No |
| `smoke NAME` | Prepare, start, verify, stop, and receipt a disposable instance | Yes |

`prepare` defaults to loopback and refuses an existing instance. The tested Steamworks build does
not become ready on loopback, so real-engine runs require `--engine-ready`. That flag selects
`0.0.0.0` behind the worker's explicit non-loopback gate; it does not configure firewall rules,
router forwarding, public visibility, or the Remote API. Use `--reprepare` only when overlaying an
existing instance is intentional.

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

## Current boundary

The CLI controls the proven server substrate. It does not yet install a bridge mod, place a vehicle,
submit an in-engine command, or collect vehicle telemetry. Those become available only after the
narrow bridge described in the architecture is implemented and tested.
