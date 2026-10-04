# Render trials

VRageCage's dedicated worker is an authoritative simulation witness, not a visual witness. Space
Engineers deliberately initializes a null renderer in dedicated mode. A render trial therefore uses
one ordinary, non-dedicated game client connected to the disposable server and treats every image as
supplemental evidence rather than simulation truth.

## Proposed first slice

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
4. Load a client-only render-witness plugin. After the client has joined the expected run, it locates
   the exact camera entity, verifies that it is functional and belongs to the expected grid, and
   requests that camera's normal in-game view on the client thread.
5. Wait a fixed number of rendered frames. The external supervisor captures only the game window,
   writes the image atomically, and hashes it. The plugin emits camera entity ID, grid ID, pose, FOV,
   run nonce, and frame/time evidence; it does not drive blocks or accept general input.
6. Stop the client and server cleanly. The terminal receipt binds the server receipt, client log,
   render request, plugin evidence, PNG hash/dimensions, and shutdown outcomes separately.

The camera block is a suitable anchor because the game implements it as a camera controller and its
normal `RequestSetView` path installs the block as the current local camera. This is client-side
presentation state; attaching the view does not need to grant grid-control authority.

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

