# VRageCage

Deterministic simulation and automated integration testing for Space Engineers.

> PUT VRAGE IN THE CAGE. MAKE THE COMPUTER DRIVE THE ROVER.

VRageCage moves repetitive QA from human-hours into compute-hours without pretending a small
simulator is the VRAGE engine. It has two intended tiers:

1. a fast deterministic micro-world for controller, planner, fuzz, and regression tests;
2. an isolated Space Engineers Dedicated Server worker for real physics and engine integration.

The first executable slice provides a bounded fixed-timestep vehicle simulation, structured JSONL
telemetry, deterministic run receipts, and versioned bridge envelopes. The separately gated worker
slice can now boot an isolated Space Engineers Dedicated Server through user-scoped UMU/Proton,
load a disposable world, verify readiness, stop cleanly, and emit a hashed receipt. A second,
explicit Magnetar backend can inject an unpublished local mod into an isolated native-Linux server
without changing the world's vanilla mod list. In an explicitly destructive copied-world lab, a
separately staged plugin can execute one bounded drive-distance command and emit correlated evidence.

## Run

```bash
dotnet run --project tests/VrageCage.CoreTests/VrageCage.CoreTests.csproj
dotnet run --project src/VrageCage.Cli/VrageCage.Cli.csproj -- scenarios/flat-seek.json
```

The CLI writes one JSON object per line and ends with a receipt containing scenario and trace
SHA-256 hashes. A non-successful outcome exits nonzero.

See [the architecture note](docs/architecture.md) for boundaries and the staged implementation
plan, [the CLI contract](docs/cli.md) for commands and exit semantics, and
[the worker guide](worker/README.md) for the real-engine smoke contract.

## Real-engine CLI

Install the repository-independent command once:

```bash
./scripts/install-cli.sh
vragecage doctor
```

Select a provisioned worker with `--host`, `VRAGECAGE_HOST`, or
`~/.config/vragecage/config.json`. Use `--local` while operating on the worker itself.

```bash
# Disposable full lifecycle, ending in a hashed JSON receipt.
vragecage smoke my-test-run

# Persistent lifecycle for a prepared fixture.
vragecage prepare my-run --engine-ready
vragecage stage-mod my-run /path/to/ModPackage --name MyMod
vragecage run my-run
vragecage wait my-run
vragecage status my-run
vragecage stop my-run
vragecage receipt my-run
```

For unpublished integration tests, select Magnetar when the fixture is created. Later commands
discover the recorded engine automatically:

```bash
vragecage doctor --engine magnetar
vragecage prepare voidwright-native --engine magnetar --engine-ready
vragecage stage-world voidwright-rover "/path/to/Space Engineers Save" --engine magnetar --engine-ready
vragecage stage-world voidwright-lab "/path/to/Space Engineers Save" \
  --engine magnetar --engine-ready --destructive-lab
vragecage stage-plugin voidwright-lab /path/to/Voidwright/ServerPlugin \
  --id voidwright-server-plugin
vragecage lab-drive voidwright-lab CONTROLLER_ENTITY_ID --distance 2 --max-speed 1
vragecage stage-mod voidwright-native /path/to/Voidwright --name Voidwright
vragecage run voidwright-native
vragecage wait voidwright-native
vragecage stop voidwright-native
```

The Proton backend registers local packages as `PublishedFileId=0`; the official multiplayer
server rejects those before compilation, and its receipt reports that policy failure. The Magnetar
backend instead stages the package under a deterministic synthetic ID and loads it through
Magnetar/Pulsar. Empty fixtures keep both world `Mods` lists empty; imported saves preserve their
published dependencies while a matching unpublished duplicate is removed from the isolated copy.
Receipts require definitions, scripts, an unchanged staged-package hash, world readiness, and no
target-mod or engine-fatal markers. The CLI never publishes a package implicitly.

Instance names are bounded and validated. Existing instances are not overlaid unless
`--reprepare` is explicit. Persistent starts use a per-instance user service; stops target the
named instance and refuse ambiguous process matches.

The command is reusable from any local repository, but worker provisioning remains an operator
task. The CLI does not install SteamCMD, UMU/Proton, Space Engineers, or host packages.
