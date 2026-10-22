# Virtual-camera HoloView proof

Status: **verified, bounded grid-only proof in a disposable Space Engineers world**. This is not yet a general render service or Voidwright MCP `capture_view()` tool. Planet terrain is explicitly rejected. The prototype accepts exactly two 256×256 world-space poses, runs one read-only HoloView worker job per pose, and exports RGBA plus pose/tick metadata without a physical camera or LCD pair for either job. The physical-camera/LCD path is retained.

## Build and runtime dependencies

No required game assembly or runtime component was missing on mpai for this test. The disposable worker has Space Engineers Dedicated Server `01_210_014` with `Sandbox.Common.dll`, `Sandbox.Game.dll`, `Sandbox.Graphics.dll`, `SpaceEngineers.Game.dll`, `VRage.dll`, `VRage.Game.dll`, `VRage.Math.dll`, `VRage.Library.dll`, `VRage.Scripting.dll`, `VRage.Mod.Io.dll`, and the other dedicated-server assemblies under `server/DedicatedServer64/`. Magnetar `2.4.2.4` supplies the mod loader and game-script compiler; its local .NET SDK is `10.0.401`. HoloView's `Data/Scripts/HoloView3D/*.cs` is present in the disposable local-mod copy, including `HoloCamWaterModAPI.cs`, which supplies the `Jakaria.API` namespace used by the worker. No separate downloaded SDK or licensed graphical client was required.

The game runtime is the meaningful build check. An initial compile rejected `System.Security.Cryptography.SHA256` under the mod-script `ProhibitedMemberRule`; the mod now writes bounded base64 RGBA and the external verifier computes SHA-256. On the final run the replacement HoloView module registered and both virtual jobs completed. These are HoloView software renders, **not** native screenshots.

## Extraction implemented

`worker/instrument-holoview-virtual.py` validates a stopped imported destructive-lab copy and strict two-pose request, then stages a unique local mod copy. It adds a virtual job branch to `PortalLcdSession.UpdateAfterSimulation`, overloads stable raster candidate selection to accept a separate dictionary of source-grid proxies, and routes completed virtual jobs to an exporter before LCD presentation. Physical jobs retain their original proxies, camera resolution, scheduling, and text-surface presentation path.

For a virtual job, grids are acquired directly from `MyAPIGateway.Entities`, bounded to 64 nearby grids within the requested range, then cached and frustum-filtered against the requested matrix. That scene is snapshotted into `WorkerRasterJob`; its `Cam` and FOV drive HoloView's existing software rasterizer. No camera or LCD entity ID is supplied. Shared main-thread frustum settings are restored after each snapshot. A planet causes rejection because this proof does not build a view-local terrain lattice.

The request fields are `origin`/`forward`/`up` in Space Engineers world coordinates (meters; orthonormal camera basis), horizontal `fov_degrees`, world session name, request/capture IDs, range, and 256×256 dimensions. Non-finite or non-unit vectors, non-perpendicular axes, out-of-range FOV/origin/range, unexpected fields, and mismatched world name are rejected. This is a lab instrumenter, not a network listener.

## Two-pose test receipt

Fixture: `test_suite_world_v2_r3`, imported as `virtual-holoview-v2r3-liveprobe-20261012`; original source save untouched. Request fixture: `worker/tests/fixtures/virtual-v2r3-two-pose.json`, capture `v2r3_two_pose_003`. Both jobs used origin `(12.231835870180294, 108.66975564371641, 22.139290751900624)` and FOV `39.804°`; the second heading was rotated approximately 25°.

The final clean lab run ID was `b2ce228e93c881479b809117e05b513f`. Its terminal receipt SHA-256 was `9db434fd8d67e39e2e8b634a848e008330589566457dcc719d3deebc3f8a504c`. In-mod frame markers occur in that receipt's hashed game log. `worker/verify-holoview-virtual.py` checks the hashed log, clean terminal state, exact mod/storage identity, world/request/pose/FOV/tick/dimensions, nonblank frames, and pixel hashes. It binds the **actual** run ID externally, because the lab launcher generates it at activation; the mod does not pretend its request ID is a verified run ID.

| Pose | Tick | Geometry primitives | RGBA SHA-256 |
| --- | ---: | ---: | --- |
| 0 | 61 | 1,153 | `d3b7d7e2aa2d99ea91dab0fb3ecbaf503ab66f5bc33b8207fa3f581d82f17e9e` |
| 1 | 71 | 2,355 | `160b95f239e9e1999b2852732191009fe34e649bdaa8150cfd3762d99c2f8253` |

The images differ at 41,932 pixels. Visual inspection shows the platform features shift with the two headings; this is not merely a hash change. The instance was stopped cleanly. Raw frames and receipts remain in the disposable instance's `Saves/test_suite_world_v2_r3/Storage/90072030983701718.sbm_HoloView3D/`; its terminal receipt is `Quarantine/b2ce228e93c881479b809117e05b513f/terminal-receipt.json`.

To reproduce on a **new** stopped disposable world copy: stage HoloView source with `worker/instrument-holoview-virtual.py`, register that staged mod, replace only the copied world's published HoloView entry with `worker/replace-published-mod.py`, run and stop the isolated Magnetar instance, then invoke `worker/verify-holoview-virtual.py FRAME_STORAGE REQUEST_JSON TERMINAL_RECEIPT`. Never point the instrumenter at a production world or Workshop cache.

## Remaining limits

This establishes arbitrary-angle **grid** rendering in the tested space fixture, not all possible world geometry or a public API. The job is a one-shot, two-pose configuration embedded into a staged local mod; it does not accept live MCP requests. It rejects planet worlds rather than render missing terrain. Dynamic entities and environmental effects are not yet independently selected for the virtual scene. Ticks 61 and 71 are not simultaneous. Generalization should extract a typed `RenderView` shared by grid, terrain, dynamic-entity selection, and rasterization before exposing `capture_view()`.
The tested world still contained physical cameras/LCDs for the pre-existing path, which also continued rendering. The virtual branch itself neither resolves nor supplies those IDs, but a separate camera-free fixture test has not yet been run.
