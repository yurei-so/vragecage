# Render trials

VRageCage's dedicated worker is an authoritative simulation witness, not a native-game visual
witness. Space Engineers deliberately initializes a null renderer in dedicated mode. HoloView 3D
provides a separate headless-capable software rasterizer, however, and retains its completed LCD RGBA
buffer with the camera pose, FOV, and source simulation tick. A bounded export from that buffer can
therefore produce a useful camera-view video without a graphical client, Steam player session, or
native framebuffer access.

These are explicitly **HoloView software renders**, not native Space Engineers screenshots and not
simulator ground truth. Earlier V2R3 captures demonstrated the RGBA buffer and pose metadata; periodic
capture extends that same path by sampling a selected camera/LCD output every N simulation ticks.
A bounded, LCD-free **grid-only** virtual-pose path is implemented: the worker can stage a VRageCage
spectator view from one operator-supplied world position and orientation, or a one-to-four-pose
comparison. The separate Voidwright adapter can request a one-shot `capture_view`. Neither is a
continuous stream, general scene renderer, or planet-terrain capture. See
[virtual-camera-render.md](virtual-camera-render.md) for the on-demand path, proof receipt, and limits.
Every exported frame is separately hashed and correlated with its simulation tick and camera pose.
The run is bounded by frame count, byte count, raster dimensions, and simulation duration.

For a stopped, disposable Magnetar instance only, `worker/instrument-holoview-capture.py` adds the
periodic exporter to a clean, instance-local HoloView source copy. It accepts one to four explicit
camera/LCD-output pairs, so views are sampled by the same simulation run. Each pair keeps its own
cadence and frame-count budget; a shared byte budget bounds the combined export. Magnetar refreshes
published Workshop content at load, so do not instrument its downloaded Workshop cache and expect
the patch to survive. Instead, stage the instrumented tree under a unique local-mod name, then use
`worker/replace-published-mod.py` to remove only the matching published HoloView dependency from the
copied world's checkpoint/config. That helper backs up both files, writes a receipt, and has a
hash-checked restore operation. The original Workshop tree, source save, and other instances remain
untouched. The capture instrumenter refuses non-lab or running instances and records source hashes in
`.vragecage-holoview-capture-<capture-id>.json`. Use repeated `--pair CAMERA_ID:OUTPUT_ID`; the older
`--camera-id` plus `--output-id` form remains available for one view.

After the run has stopped, collect the emitted `vragecage-hv-*.hvraw.txt` files from that world's mod
storage. For one view, run `worker/holoview-frame-video.py INPUT_DIR --output OUTPUT.mp4`. For a
synchronized comparison, run `worker/holoview-multiview-video.py INPUT_DIR --left-pair CAMERA:OUTPUT
--right-pair CAMERA:OUTPUT --output OUTPUT.mp4`. Optional `--instance`, `--run-id`, and
`--server-receipt-sha256` arguments add explicit correlation to either manifest. The multiview
assembler joins frames only when their `snapshot_tick` values match exactly, omits unmatched frames,
and does not interpolate. It labels both panels, records per-view camera pose and source hashes, and
creates a variable-frame-rate MP4 from the shared tick sequence. Both assemblers validate raw RGBA
lengths and capture identity, emit frame manifests, and never delete or overwrite raw inputs or
existing outputs.

The native client path remains intentionally deferred: the toolkit must not require its operator to
own a game client or join as a player merely to exercise the free dedicated server. The separate
server-only `lab-sensor-frame` command produces a low-resolution camera-aligned depth map from bounded
physics rays. That depth artifact and the HoloView software-render artifact are different modalities;
neither should be mislabeled as a native screenshot or simulator ground truth.

## Periodic HoloView software capture

The retained-frame export hook samples only the configured LCD entity and only when the render job's
camera entity also matches the requested camera. It copies the completed RGBA array before the worker
can reuse its buffers, then writes one `HVRAW1` record into mod world storage. The record contains the
actual native raster dimensions and the larger LCD display dimensions separately, plus
camera/output/surface IDs, FOV, camera origin/forward vector, and the job's source tick. This
distinction matters because HoloView can scale a 256x256 retained buffer onto a 512x512 LCD. It does
not issue camera commands or control the vehicle.

Each sampled frame also gets a sibling `.scene.json` sidecar. Its stable join key is the capture ID,
frame sequence, source tick, camera entity ID, LCD output entity ID, and surface index. The bounded
payload records HoloView's own renderer-frustum candidate grids/entities and world AABBs; it is useful
scene context, but it is **not** a detection made from pixels, a claim that every listed object was
visible, or simulator ground truth. The extra metadata pass runs only at the configured sample cadence,
scans at most 4,096 grid-block candidates, and serializes at most 64 objects by default (tunable with
`--scene-max-objects`, hard maximum 512). The sidecar says when the inventory was truncated. The video
manifest validates that each sidecar matches its raw frame and records the sidecar hash.

This makes camera identity explicit in the observation contract and preserves per-camera streams in a
multiview run. The synchronized assembler keeps each feed's camera/output identity and joins only
equal source ticks; it never flattens the views into an anonymous feed or claims synchronization when
the render jobs did not publish matching ticks.

For the V2R3 overhead-drone fixture, the previously observed camera/output pair is:

```text
camera entity: 119411052923483830
LCD output:    142523868888083493
```

The V2R3 Red obstacle-course truck has a known onboard pair that can be compared with that overhead
tracker in one capture:

```text
onboard truck camera: 96346417539617834
onboard LCD output:   94419542428808580
overhead camera:      119411052923483830
overhead LCD output:  142523868888083493
```

The default interval is 120 simulation ticks (nominally two seconds at 60 ticks/second), with at most
60 frames, 64 MiB of estimated exported text, 512x512 native pixels per frame, and 7,200 ticks (two
minutes) from first capture. These are upper bounds, not a promise of fixed wall-clock cadence:
HoloView must finish and publish a render before a sample exists. The output video preserves
source-tick spacing; it does not invent intermediate views or interpolate motion.

Verified V2R3 run `379025977c4081868ddc869f33eda520` exported capture
`v2r3-20261007-d`: 30 frames from ticks 16 through 3517, 256x256 native raster displayed at 512x512,
sampled at least 120 ticks apart. The capture manifest carries the run ID and terminal server receipt
hash. This is a time-lapse of one selected HoloView camera feed, not a claim that the camera moved or
that the dedicated server captured a native framebuffer.

Before instrumentation, verify the instance is stopped and disposable and that the IDs identify the
intended output and camera in that copy. Do not instrument the original Workshop tree or a source save.
The mod compiles at world load; capture failures are logged and terminate the bounded exporter without
affecting simulation control. Inspect the resulting frame manifest alongside the server run receipt.

## Native framebuffer client witness (deferred)

The first implementation should run the render client on the workstation that already owns and runs
Space Engineers. The simulation server remains isolated on the worker. This is a LAN client/server
trial, despite the client process itself being locally supervised. Moving the client to a separate
render worker later requires a licensed Steam client installation, an authenticated Steam session,
a Vulkan-capable graphical session, and the full game assets; the dedicated-server installation is
not sufficient.

1. Start a stopped, copied-world Magnetar fixture through the existing destructive-lab gate.
2. Create a bounded render request naming one camera-block entity ID, a run nonce, resolution, settle
   frame count, and maximum lifetime. Bind the request hash to the lab run receipt.
3. Launch an ordinary Space Engineers client through Steam/Proton and join only the configured lab
   endpoint. Do not reuse the server's universal harness identity for the client.
4. Load a client-only render-witness bridge. After the client has joined the expected run, it locates
   the exact camera entity, verifies that it is functional and belongs to the expected grid, and
   requests that camera's normal in-game view on the client thread.
5. Wait a fixed number of rendered frames. Prefer the game's native screenshot path and have the
   supervisor claim exactly one newly created file; a game-window capture is a bounded fallback if
   the installed build exposes no stable screenshot call. The client resolution supplies the cheap
   low-resolution render, so the bridge never implements a graphics pipeline or reads arbitrary GPU
   memory. The bridge emits camera entity ID, grid ID, pose, FOV, run nonce, and frame/time evidence;
   it does not drive blocks or accept general input.
6. Stop the client and server cleanly. The terminal receipt binds the server receipt, client log,
   render request, plugin evidence, PNG hash/dimensions, and shutdown outcomes separately.

The camera block is a suitable anchor because the game implements it as a camera controller and its
normal `RequestSetView` path installs the block as the current local camera. This is client-side
presentation state; attaching the view does not need to grant grid-control authority.

A normal sandboxed session mod can identify cameras and switch ordinary game state, but the public
ModAPI does not expose camera pixels as an image buffer. The witness should therefore be a narrowly
scoped client plugin/bridge around the game's existing camera and screenshot paths, not a replacement
renderer and not a server mod pretending the dedicated null renderer has frames.

## Boundaries

- Never render an active human save. The server world must retain the copied-world and disposable-lab
  contracts.
- A screenshot is not proof that physics or control succeeded. It is correlated evidence attached to
  the authoritative server observation/operation receipt.
- The client may connect only to the exact configured lab endpoint and run nonce. A generic join or
  browser automation surface is out of scope.
- The render plugin may select one camera and report/capture presentation state. It may not actuate a
  grid, synthesize gameplay input, edit the world, or expose arbitrary plugin calls.
- The client process gets a bounded deadline and must be terminated if join, camera discovery,
  attachment, capture, or receipt construction stalls.
- Credentials, Steam session material, Proton prefixes, and machine-specific paths never enter the
  repository or receipts.
- Capture failure must remain distinct from server/test failure. Neither side may invent evidence for
  the other.

## Receipt sketch

```json
{
  "schema": "vragecage.render-receipt.v1",
  "run_id": "...",
  "request_sha256": "...",
  "server_receipt_sha256": "...",
  "client_joined": true,
  "camera": {
    "entity_id": 123,
    "grid_id": 456,
    "position": [0, 0, 0],
    "forward": [0, 0, -1],
    "fov": 1.0472
  },
  "settled_frames": 30,
  "image": {
    "width": 1280,
    "height": 720,
    "sha256": "..."
  },
  "client_log_sha256": "...",
  "clean_client_shutdown": true,
  "passed": true
}
```

`passed` means the requested client joined the expected disposable run, attached to the exact camera,
and produced internally consistent evidence. It does not mean the rendered scene is visually correct;
that is a later oracle or human-review decision.

## Staged validation

1. **Manual join trial:** start the fixture, join using the normal workstation client, and verify the
   unpublished client definitions/mod scripts load correctly. This is the next human-click gate.
2. **Camera witness trial:** add the bounded client plugin and prove it can attach to the fixture's
   camera without gameplay input.
3. **Single-frame receipt:** capture one 1280x720 frame and bind it to both logs and the server receipt.
4. **Determinism study:** repeat a stationary scene and measure pixel/perceptual variance before using
   image comparisons as an automated oracle.
5. **Remote render worker:** only after the local path is reliable, provision a licensed graphical
   client on a GPU worker and preserve the same request/receipt contract.
