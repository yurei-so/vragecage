# Isolated Proton worker

The mpai proof-of-concept is user-scoped beneath
`~/.local/share/vragecage-worker`. It deliberately does not add i386 packages to the host. Valve's
32-bit SteamCMD bootstrap runs against a privately extracted Debian i386 glibc through a
Bubblewrap mount namespace created by `steamcmd-user.sh`. The private loader is mounted at the
interpreter path expected by Valve's unmodified binary; the host filesystem remains unchanged and
SteamCMD's self-verification remains valid.

UMU supplies the managed Proton build and Steam Linux Runtime. The Space Engineers prefix, server
files, instance data, and logs remain separate. Never point an instance at Blake's desktop Space
Engineers saves or mod directory.

## Proven runtime

The mpai worker has completed a real-engine smoke on Space Engineers Dedicated Server
`01_210_014`, UMU Launcher `1.4.4`, and `UMU-Proton-10.0-4`. The smoke loaded Keen's bundled Empty
World, reached `Game ready...`, and shut down through `SIGINT` with a world save and clean Steam
logout.

SteamCMD is intentionally isolated, so `link-steam-sdk.sh` creates the conventional per-user
`~/.steam/sdk32` and `sdk64` links without replacing anything already present. Those links are
required for Steamworks server initialization; without them the process exits immediately after
`Bind IP` with no managed exception.

## Lifecycle

The normal interface is the top-level `vragecage` CLI; these scripts remain the auditable worker
implementation. Run `./scripts/install-cli.sh` from the repository, then use `vragecage doctor`,
`smoke`, `prepare`, `run`, `wait`, `status`, `stop`, or `receipt` from any checkout.
The full command contract and exit codes are documented in [`docs/cli.md`](../docs/cli.md).

On mpai, with the user-scoped dependencies already provisioned:

```bash
export VRAGECAGE_WORKER_ROOT="$HOME/.local/share/vragecage-worker"
worker/link-steam-sdk.sh

# Safe default: prepares loopback only. Steamworks reaches bind but does not become ready on the
# tested build when bound to loopback.
worker/prepare-instance.sh empty-world

# Real-engine smoke. The opt-in is intentionally explicit because Steamworks requires a
# non-loopback bind. The generated server is PRIVATE and Remote API remains disabled.
VRAGECAGE_ALLOW_NON_LOOPBACK=1 worker/smoke-test.sh empty-world
```

`smoke-test.sh` owns start, readiness detection, graceful stop, and a machine-readable JSON
receipt. It never force-kills a stuck server. `stop-server.sh` refuses ambiguous matches and sends
`SIGINT`; the current Proton path translates that into VRAGE's normal exit/save sequence.
Instance preparation also refuses to overwrite an existing config by default. Use a fresh instance
name for disposable tests; `VRAGECAGE_REPREPARE_INSTANCE=1` is an explicit opt-in when preserving
and overlaying that instance is intentional.

## Network boundary

`prepare-instance.sh` and `run-server.sh` reject non-loopback configuration unless
`VRAGECAGE_ALLOW_NON_LOOPBACK=1` is present. The proven Steamworks path needs `0.0.0.0`, so the full
smoke sets that opt-in locally. The fixture is `PRIVATE`, has one player, no mods, no remote API,
and does not configure router forwarding or host firewall rules. A public deployment is outside
this worker contract.

## Known warning

UMU currently warns that its runtime cannot execute the i386 `capsule-capture-libs` helper. The
64-bit dedicated-server smoke still passes. Keep the warning visible; do not claim it is fixed or
weaken the readiness receipt around it.

## Provisioning boundary

These checked-in scripts document and operate the proven worker, but they are not yet a universal
host installer. The current mpai provisioning is user-scoped and intentionally preserves the
downloaded SteamCMD, UMU runtime, Proton prefix, dedicated-server files, and immutable test logs
outside Git. `vragecage doctor` verifies that substrate before use. Reproducing it on another host
should become a separate, checksum-pinned provisioning slice rather than being hidden inside
`prepare` or `run`.
