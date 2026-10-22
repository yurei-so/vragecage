# Voidwright unified perception field receipt — 2026-10-13

## Outcome

The highest-value end-to-end slice now works in a disposable V2R3 copy: Voidwright and HoloView loaded as separate mods in the same Space Engineers server run; HoloView produced three nonblank, pose-matched color renders; a fresh Voidwright snapshot was queried through the real MCP stdio tools; and a perspective/cell-targeted raycast was confirmed by exact-cell inspection. The test copy was stopped cleanly.

This is not yet a live `capture_view()` bridge. Captures are still scheduled by a bounded static HoloView request, and HoloView tick values do not share a verified tick clock with the snapshot exporter.

## Fixture and provenance

- Disposable instance: `test-suite-world-v2-r3-voidwright-unified-20261013-c`
- Fixture ID: `19dc9f16e7eaf050e470d3f1624956b2`
- Source: `test-suite-world-v2-r3-agent-core-20261013-a`; source tree SHA-256 `ebfab78cc9b17731e21b1ccdbd66ea2c238e915f395b12345bf58d6d3a11e177`
- World: `test_suite_world_v2_r3`
- Space Engineers Dedicated Server: `01_210_014`, Magnetar
- Final verified server run: `c5f276c89ee8c421f44b1a79e94ff91f`
- Terminal receipt SHA-256: `256f3ca146b66704ab663e90aab7e0a2a0b2308bc8df8678b0946a2d36d6bc4f`
- Final state: clean shutdown; no player connected; no fixture block edits (`fixture_edit_count=0`).
- Mods were staged separately, not merged: `HoloViewVirtualThreeC` (`90003880915450851`, tree SHA-256 `457eb9a87cf6f6bd903074dc8c6c590e15e835cf106cd77abf20b33a28f8e877`) and `VoidwrightAgentCoreUnifiedC` (`90069022856692149`, tree SHA-256 `a343547813fc168db1333fac04cdc595a9ec50def4e9dc6563e63268c3277ea9`). Both appeared enabled in the Magnetar configuration. The server receipt verifies the HoloView package hash; the hashed game log independently verifies Voidwright registration and its observation-only load marker.
- The disposable copy's dedicated-server IP was changed to MPAI's private LAN address `10.0.0.182` (`ServerPort=27017`, `SteamPort=8767`); no public listener was added. These are configured values, not a verified successful client connection.

## Three real virtual renders

The accepted request was `blue_truck_elevated_three_views_002` / `vw_truck_views_20261013_002`, 256×256 RGBA, 55° FOV, range 64 m. Jobs used explicit world-space origins and orientation; no physical camera or LCD entity IDs were supplied. The independent HoloView verifier accepted all three frames and confirmed that each pair differed substantially.

| View | HoloView update counter | Geometry primitives | Visible pixels | Unique visible colors | RGBA SHA-256 |
|---|---:|---:|---:|---:|---|
| 0 | 60 | 255 | 53,984 | 81 | `b9d608a848b3b71d6aecc053fdd7e89106a9cf7a7f015a672c5adfdee4b9228d` |
| 1 | 66 | 1,125 | 52,183 | 40 | `c9da071c0be6222373bdca38d17247cc59712ebafbe43f73548c1d7f04b298de` |
| 2 | 73 | 170 | 64,880 | 46 | `f99c2b9819424b8bd0bc9e02af43b8de33cc7487a38645b021fdc51f8c53df8b` |

Changed pixels: views 0–1: 49,113; 0–2: 58,068; 1–2: 38,665. This is genuine HoloView software-rendered grid imagery, not a native framebuffer screenshot. The renderer currently rejects planet terrain and only renders supported saved grids.

The same run also logged one separate legacy HoloView capture rejection (`invalid or over-limit raster dimensions`) before the three bounded virtual jobs succeeded. It was not counted as a virtual frame.

## Real MCP observation

The .NET 8 adapter built locally with zero warnings/errors and was invoked as a real line-oriented MCP stdio process using a copy of the snapshot pulled from the active isolated instance. The exposed surface had eight read-only tools; `VOIDWRIGHT_READ_ONLY_ONLY=true` excluded lab/actuation tools.

- Snapshot exporter run ID: `127786eefa9d4af9b52e09af8fcf61c7`
- Snapshot tick: `700`; captured at `2026-10-13T10:01:32.9240610Z`; SHA-256 `29b1b8ef3c93d2d07830c2d11d4fa3a817e2ade56588edce076c943f59b5dacd`
- Enrolled body: Agent Core `94124964145704784`; root grid `[Voidwright] Blue Utility Truck`, entity `106313676705820376`; 5 physical grids / 88 blocks, complete coverage.
- HoloView view 0 and MCP perspective were aligned to within 1.3 µm position error; forward dot product `0.99999996`, up dot product `0.99999926`.
- Perspective ID: `960bfcd169d74212b9ebf2a8c3bd5551`; world-scope path hash `7e63a6ca9f00bf81f3bf550dffd787b5bcc94216031b6e250ad8d56a24e04568`.
- The same-view forward ray returned a complete-coverage miss. A grid-cell-targeted ray from the same pose hit an earlier occluding `Neon Tubes Corner` block at cell `[0,3,2]`, 11.032 m away, while aiming through requested cell `[0,2,-2]`. Exact-cell inspection returned the same entity and confirmed it was occupied, functional, and working at tick 700.

This verifies the shared viewpoint and a real same-run structural raycast, including a useful negative result and an occlusion. It does not prove that the requested cell itself was visible: the ray hit the nearer Neon Tubes block first. Raycasts use exported oriented cell envelopes, not live physics or detailed block meshes.

## Time and run identity caveat

The HoloView JSON records a field named `snapshot_tick`, but the code sets it from the `PortalLcdSession` update counter. It is not proven to be the game's shared simulation tick. The Voidwright snapshot tick is 700. Do not subtract these as if they were the same clock.

The hashed game log places HoloView frame 0 at host-local `05:01:07.323` and the snapshot timestamp is `10:01:32.924Z`; the capture preceded the queried snapshot by about 25.6 seconds after converting the host time to UTC. The other frame markers were at `05:01:07.407` and `05:01:07.507`. No simultaneous-observation claim is made. The world was static and no player was connected, but movement latency was not benchmarked. HoloView metadata itself currently omits a UTC timestamp and shared VRageCage run ID; association is established by the terminal receipt, exact fixture path, hashed run log, and matching world name, with distinct run IDs preserved separately.

## MCP setup and readiness

The adapter was deployed to MPAI at `~/.local/share/voidwright-mcp/readonly-20261013/net8.0/` and successfully handshook over trusted SSH stdio with the host-key pinned (`StrictHostKeyChecking=yes`) and .NET 10 major roll-forward. A read-only `[mcp_servers.voidwright]` entry is now in `/home/alu52/.codex/config.toml`; `codex mcp get voidwright` confirms the eight observation tools. The configured path is this isolated fixture's snapshot file, so it reports stale while the fixture is stopped and becomes usable when that exact fixture runs and refreshes the file. Restart Codex or begin a fresh MCP session to load the new server into the tool catalog. A LAN client would target the configured private server address/port above, but no client join was attempted.

`capture_view()` is not exposed; Codex cannot yet ask a perspective ID to enqueue a new render. The images came from the static HoloView request. The LAN server reports multiplayer support and is bound to the private MPAI address, but a human client joining while Codex queried was not tested. Therefore cooperative gameplay is not yet verified.

## Changes and tests

- Source compatibility fix: replace prohibited `Uri.IsHexDigit` use in the Agent Core wireframe renderer with a local ASCII-hex check. This was the game-side mod compiler blocker; the next live load registered Voidwright and emitted the observation-only marker.
- Deployed artifacts: read-only Agent MCP build on MPAI and local Codex MCP config entry. No public services, production worlds, or original save were changed.
- No commit, push, publication, or deployment to a production server.
- Tests: `Voidwright.AgentMcpTests` 11/11; HoloView virtual tests 8/8; HoloView physical capture tests passed; VRageCage worker script tests passed; live three-frame verifier passed; separate-mod server run cleanly stopped.

Evidence lives under `artifacts/voidwright-unified-2026-10-13/`, especially `run5/` (snapshot, game log, terminal receipt, frame data, rendered PNGs, request, and `mcp-observation.json`).

## Next smallest step

Add a bounded request/response mailbox between the already-working Voidwright perspective store and HoloView's virtual job scheduler. The request must carry the perspective's exact pose, world/run identity, dimensions and timeout; the response must carry captured UTC, an actual game tick source (or explicitly unavailable), artifact hash, and a run-bound receipt. Keep snapshot tick and HoloView capture time separate until a common clock is proven. Then test two client-requested captures in a disposable run before attempting the human-join smoke.
