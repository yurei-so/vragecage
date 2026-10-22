# Voidwright on-demand HoloView bridge — implementation receipt

Date: 2026-10-13
Status: **implemented and verified against the isolated V2R3 fixture; bounded repeated capture works, continuous streaming does not exist**

## What is implemented

The local Voidwright MCP adapter now has a read-only `capture_view` tool. It
accepts an existing process-local perspective ID, resolves its world pose from
the current Voidwright snapshot, and publishes one atomic request into the
configured HoloView world-storage directory. HoloView's game-side partial
class polls that mailbox, resolves a bounded saved-grid scene, schedules the
existing software rasterizer with the requested origin/forward/up/FOV, and
writes an RGBA payload followed by a JSON receipt. The MCP side validates the
request ID, perspective/run/world/target, echoed snapshot identity, renderer
pose/FOV, dimensions, visibility, and age, then returns a PNG image block and
provenance JSON.

No game entities, blocks, camera blocks, LCDs, or player controls are changed.
The only file writes are the bounded request and temporary response artifacts
in the operator-configured HoloView world storage; the MCP process removes
those files after each call. One request can be in flight at a time. This is
not a continuous recorder or video stream.

## Time and scene boundaries

Voidwright's `simulationTick` and HoloView's `rendererUpdateCounter` are
reported separately. HoloView currently sources its counter from the
`PortalLcdSession` update loop; it has not been proven to be the engine's shared
simulation tick. The response therefore declares `simultaneous=false` and an
incomparable tick relationship rather than subtracting the two. A frame is
bound to the snapshot captured timestamp and rejected if more than 30 seconds
apart. HoloView independently checks the exact world name; the MCP side
revalidates its snapshot run and perspective after capture.

The renderer supports saved-grid proxies only. Planet terrain is rejected;
characters, voxel geometry, and general dynamic entities are not established.
The rendered target is included among up to 64 in-range saved grids, but the
current rasterizer may also depict other grids in the same bounded range.
Visibility is software-renderer evidence, not native framebuffer capture.

## Verification performed

- `dotnet build AgentMcp/Voidwright.AgentMcp.csproj --configuration Release`:
  passed, 0 warnings and 0 errors.
- `dotnet run --project tests/Voidwright.AgentMcpTests/Voidwright.AgentMcpTests.csproj --configuration Release`:
  passed, 14 checks. Includes a fake mailbox producer and synthetic image; it
  does not prove Space Engineers loaded the new HoloView script.
- `python3 worker/tests/test-holoview-live.py`: passed, 3 checks.
- Existing HoloView virtual, capture, and multi-view worker tests passed in the
  current checkout before this receipt was written.
- The live-capture template preserves the previously verified session and
  worker scheduling hooks. The physical camera/LCD branch remains untouched.

## Live fixture verification

The previously tested disposable fixture is
`test-suite-world-v2-r3-voidwright-unified-20261013-c` (fixture
`19dc9f16e7eaf050e470d3f1624956b2`, world name
`test_suite_world_v2_r3`). The source save and the other V2R3 candidate were
left untouched. The custom HoloView package was staged only in this disposable
fixture under distinct mod ID `90037850608365849` (package tree SHA-256
`b556d3d6c8f4c74222bc3fbd8684a760d8e1ef8d7a6c52418f9c55ebcfa11bfa`). The
Magnetar profile change has a recoverable backup; only the selected HoloView
mod identity changed. Voidwright's exporter and the enrolled grid were not
edited by the capture exercise.

Magnetar run `f419fad2474be11bb163e387fdb2a36a` loaded the custom HoloView
script and produced genuine software-rendered color frames through the actual
Voidwright MCP stdio adapter. The fixture-bound snapshot identified world
`test_suite_world_v2_r3`, run `fa8696ca2c4f4eab807843677bbd8b49`, and world
scope `5b13b64ccdb5212d6ce0c2e56236a6553120ebf1c6d99a5177b8133cbcb4cde3`.
The enrolled target was `[Voidwright] Blue Utility Truck`, root grid
`106313676705820376`, with 5 physical grids and 88 blocks. MCP tool discovery
returned 10 read-only tools including `capture_view`; `VOIDWRIGHT_READ_ONLY_ONLY=true`
remained set.

Three distinct 256x256 PNGs were returned as image blocks and saved for review:

| View | Perspective | Snapshot tick | Renderer update counter | Structural result | PNG SHA-256 |
|---|---|---:|---:|---|---|
| Elevated/top-down | `a9bcbcbe19364a86a8864102eab0bcc1` | 7900 | 7974 | 156 geometry primitives; nonblank | `9c4a2e9e5c4177a43947f6f5eed65c9b5d0653a72f1763c7b6f307b2827e5635` |
| Side, default forward | `cbdd7806141849c8a266a208b0f6698c` | 10000 | 10128 | Structural ray missed the target grid along this direction | `3a17302a51e46e4068e834f18acf159822317608a3aa6c41815c2282fff15c36` |
| Side, aimed at truck | `325412205048400b8c4400e28cba6e89` | 12100 | 12234 | Hit Light Armor Half Panel at cell `[1,3,-1]`, local face `+X`, 7.40936 m | `27c046a5550143eeda159270604c8d2c29c2cadec5696aba7bec35db60e74ca7` |

The third frame's ray hit block entity `135652875266324012` on grid
`106313676705820376`, at world position
`[-32.22371638722172,52.466916039943854,34.031770568495716]`, with world
normal `[0.8608196311719373,0.057234052367317385,0.5056815458750945]`.
An exact-cell inspection at a later snapshot tick returned the same block
entity and cell. These observations support agreement between the rendered
grid scene and structural grid evidence, but do not prove detailed mesh
visibility or same-tick equivalence. The second view's miss is direction-
specific, not evidence that the truck was absent.

Image artifacts are in
`/home/alu52/github/voidwright/artifacts/virtual-live-capture-2026-10-13/`:
`capture-01.png`, `capture-02.png`, and `capture-03.png`. That directory also
contains `cadence-benchmark.json` and `load-sample.json`.

## Repeated-capture measurements

The adapter was exercised serially with short batches at requested cadences of
1, 5, 10, and 30 fps (four calls per cadence); all 16 calls returned valid
frames. Measured inter-capture rates were approximately 1.006, 4.326, 4.262,
and 9.992 fps respectively. A further eight-call batch requested at 30 fps
returned 8/8 frames at 6.112 calls/s end-to-end. Per-frame request-to-capture
latency across these samples was 20.1–186.7 ms. Snapshot-to-frame ages varied
from tens of milliseconds to about five seconds, showing that renderer work
can complete quickly while the underlying snapshot itself is older.

This is repeated on-demand MCP capture, not a continuous video stream. No
frames were rejected in the 24 calls measured; the client allows only one
in-flight request and does not queue work, so a slower consumer naturally
limits request rate. A coarse process sample during the short burst showed
about 14.8 MiB additional server RSS; CPU sampling was too coarse to attribute
render cost or state a stable performance figure. These short batches are not
sustained-throughput evidence and do not establish 30 fps capability.

## Cleanup and remaining boundaries

The disposable Magnetar instance was shut down cleanly at the end of run
`f419fad2474be11bb163e387fdb2a36a`. The terminal receipt hash is
`536e1fc729e9854818bb6db6d54768bb00fa979a4fff50620d831722c74b5606`; the
final server log hash is
`99c1a2039beccd91a37bafa1e871e74e56a67a39720e7f704ecb087d7b4f8bcd`. The
terminal receipt reports `clean_shutdown=true`, scripts loaded, no fatal
markers, zero fixture edits, no pending operation, and zero connected humans.
That last check confirms the operator had already left the server before
shutdown.

The local configuration points to the read-only stdio adapter and the
fixture's exact snapshot and HoloView storage paths, with the read-only guard
enabled. The adapter's direct stdio `tools/list` handshake returned 10 tools
including `capture_view`. However, the active desktop session currently
advertises only eight Voidwright tools and omits `capture_view`; the app's
injected MCP catalog therefore has not refreshed to the configured adapter.
Open a fresh MCP session or restart/refresh the Codex desktop connection, then
verify that `capture_view` appears before relying on it in a normal chat. The
instance is stopped, so new live observations also require starting this same
disposable fixture again. No production instance, original save, public
service, commit, or push was modified or performed for this integration
slice.

No production instance, original save, public service, commit, or push was
modified or performed for this integration slice.
