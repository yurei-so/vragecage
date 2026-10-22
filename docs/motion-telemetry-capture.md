# Bounded motion telemetry capture

VRageCage can optionally ask the Voidwright server plugin to emit read-only
motion samples more often during a depth-guided run. The default remains one
sample every 10 simulation ticks. Set `--motion-telemetry-interval-ticks 1`
on `vragecage run` to sample on each plugin update (up to the simulation's
actual update rate), or choose any interval from 1 to 600 ticks. This changes
telemetry frequency only; it does not change movement settings or safety
checks.

For an independently authorized lesson, set the optional sampling interval
when starting its VRageCage instance:

```sh
python3 bin/vragecage --host alu52@10.0.0.182 run <instance> \
  --motion-telemetry-interval-ticks 1
```

Run the collector on the worker host, against the active dedicated-server log,
so it can read lines as they arrive. By default it starts at the current end;
use `--from-start` for a log known to belong to the exact active run to recover
samples written before the collector started. Bind capture to the exact run,
operation, controller, and grid. The output path must be new; existing files
are never overwritten.

```sh
python3 worker/capture_motion_telemetry.py \
  --source-log <game-log-path> --output <new-capture.ndjson> \
  --run-id <32-hex-run-id> --operation-id <32-hex-operation-id> \
  --controller-id <decimal-id> --grid-id <decimal-id> \
  --from-start --duration-seconds 60 --poll-hz 20 --capacity 2048
```

The capture tool is read-only with respect to the game log and world. It reads
from the log's current end, accepts only samples with all four identities
matching, and stores a bounded number of raw evidence lines. Polling is capped
at 60 Hz, capture duration at five minutes, queue capacity at 8,192 samples,
source reads at 256 MiB per capture, and retained sample data at 64 MiB. On
overflow it drops the oldest retained sample and records `droppedSamples` and
`droppedBytes` in the final NDJSON
record. It also reports filtered, malformed, identity-mismatched, and
oversized lines. Source rotation or truncation ends the capture with an
explicit reason.

Each source sample carries UTC capture time, run ID, operation ID, controller
ID, grid ID, and a monotonically increasing per-run sequence. The dedicated
server log's own timestamp is retained separately as an unqualified source
timestamp because its timezone is not guaranteed. The capture's `observedAtUtc`
is when the collector read the line, not the physics sample time.

## Limits and integration gaps

- The plugin's high-rate source exists only for its current depth-navigation
  operation. This does not create passive multiplayer vehicle telemetry for
  arbitrary grids or lessons without a separately authorized run.
- A one-tick interval requests a sample on each plugin update; it does not
  guarantee a 60 Hz simulation or equal wall-clock sample spacing. Compare the
  embedded UTC timestamp, simulation tick, and server simulation ratio.
- The collector's drop accounting covers its own bounded in-memory queue. The
  game logger exposes no write-acceptance or source-side drop counter, so this
  tool cannot prove that the server log itself lost no lines.
- VRageCage receipts do not yet include a dedicated telemetry-capture artifact
  reference or digest. The capture is a separate NDJSON file and should be
  associated with the run using the identity fields in its header and samples.
- The collector is currently a standalone worker utility rather than a
  `vragecage` subcommand; an operator supplies the source log, active run,
  operation, controller, grid, and output path explicitly.
- No game process, fixture, movement command, or brake setting is changed by
  this tooling. A future lesson still requires its own explicit control and
  supervision authorization.
