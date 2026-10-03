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
load a disposable world, verify readiness, stop cleanly, and emit a hashed receipt. It does not yet
install the bridge mod or drive a vehicle inside VRAGE.

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

The client defaults to the provisioned mpai worker. Override it with `--host`,
`VRAGECAGE_HOST`, or use `--local` on the worker itself.

```bash
# Disposable full lifecycle, ending in a hashed JSON receipt.
vragecage smoke my-test-run

# Persistent lifecycle for a prepared fixture.
vragecage prepare my-run --engine-ready
vragecage run my-run
vragecage wait my-run
vragecage status my-run
vragecage stop my-run
vragecage receipt my-run
```

Instance names are bounded and validated. Existing instances are not overlaid unless
`--reprepare` is explicit. Persistent starts use a per-instance user service; stops target the
named instance and refuse ambiguous process matches.

The command is reusable from any local repository, but the remote worker is currently provisioned
specifically on mpai. Provisioning a second host remains an operator task; the CLI does not install
SteamCMD, UMU/Proton, Space Engineers, or host packages.
