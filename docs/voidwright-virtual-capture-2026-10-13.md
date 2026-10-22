# Voidwright virtual HoloView capture attempt

Date: 2026-10-13
Result: **partial real-render proof; not an MCP capture workflow**

## Receipt

Three virtual HoloView jobs ran in an isolated disposable V2R3 copy. They were
software-rasterized color frames, with no physical camera or LCD IDs supplied
to the jobs. The first two frames contain visible color geometry and differ by
49,298 RGBA pixels. The third frame is fully transparent/blank; the old
byte-difference check incorrectly accepted that case, so the verifier now
rejects blank images. This does **not** meet the requested three-view success
criterion.

The input poses were calculated from Blue Utility Truck grid pose data in a
genuine Voidwright snapshot. They were compiled into a one-shot mod job; no
MCP perspective ID was created or resolved during the capture run. The
snapshot came from exporter run `f34bacbdd44b4efaa426f41c30793156` at tick
`30400` (snapshot SHA-256
`62e7298eb8f310ca88a9b4b81663963ab25ac60f3efa8eda98b951a470b95472`). The
HoloView frames came from a separate server run, so their ticks `61`, `66`,
and `72` cannot be paired with that snapshot as simultaneous evidence. For
that reason no matching raycast or structural/visual agreement is claimed.

## Fixture and run

- Source: preserved isolated V2R3 Agent Core fixture
  `test-suite-world-v2-r3-agent-core-20261013-a`, fixture
  `b973b5dc085ff84ec466885dd992cb31`.
- Render copy: `test-suite-world-v2-r3-voidwright-virtualviews-20261013-b`,
  fixture `3cc464903e3b3b2b0f51b8a0d1634181`.
- Source world hash: `ebfab78cc9b17731e21b1ccdbd66ea2c238e915f395b12345bf58d6d3a11e177`.
- Space Engineers Dedicated Server `01_210_014`, Magnetar engine. The copy
  bound only to MPAI's private LAN address `10.0.0.182`; no public listener was
  enabled and no player joined.
- Render run: `d6ad96f13fac0f181e65c92cd3fc2546`.
- Terminal receipt SHA-256:
  `c2c6839cd9d6e1070bbcbdbb011e4c9dc3909022b2090c73711b7294f92d732a`.
- Hashed game log SHA-256:
  `77f7041b9bfc022403699016c6619cfbd48287019c7460692383bf4ee996aba6`.
- Server was stopped cleanly; receipt says `clean_shutdown=true`,
  `game_ready=true`, `scripts_loaded=true`, `fixture_edit_count=0`.
- The combined mod tree was unchanged during the run
  (`7491ab3d81d5d5d4a8bf733ab98579b04837a5c9ee77e629bb0c35fabc2e15f5`).
  The receipt did **not** pass its Voidwright expected-log marker; no fresh
  exporter snapshot was produced in this run.

The untouched source fixture was separately checked after the render run and
was inactive with its prior clean receipt. The unrelated V2R3 candidate was
not touched.

## Frames

All requests were `256x256`, FOV `55°`, source kind `virtual`; metadata pose
matched the request to floating-point tolerance. The renderer observed world
name `test_suite_world_v2_r3` and reported no camera/LCD IDs. The renderer
currently selects all in-range saved grids (up to 64), not a requested target
grid specifically.

| View | Tick | Geometry primitives | Visible pixels | RGBA SHA-256 | Outcome |
| --- | ---: | ---: | ---: | --- | --- |
| Front-proximal | 61 | 255 | 53,984 | `11d980ac295e195cf79483e08ae7ec03b20bf437f97388de39a588eb3153764c` | Visible color; scene includes truck geometry |
| Side | 66 | 1,125 | 52,176 | `bf57a7287985523c8a17ff688122c3ddf047bbe56c836b6e4ee9dfff8e2391ae` | Visible color; scene includes truck geometry |
| Rear candidate | 72 | 178 | 0 | `b7997c60612a8f60f104b19583162aaac7dde1f1ab2e8ef7c9b935550e2b0aa8` | Rejected: blank/transparent |

The first two color frames differ at 49,298 pixel locations. The rejected
third pose was not repaired in this run. PNG previews are saved beside this
receipt as `../artifacts/virtual-capture-2026-10-13/view-0.png` through
`view-2.png`; raw RGBA, request, frame metadata, terminal receipt, and log
hash are preserved in the same artifact directory (frame metadata and base64
payloads are under `raw/`).

![Virtual HoloView frame 0](/home/alu52/github/vragecage/artifacts/virtual-capture-2026-10-13/view-0.png)

![Virtual HoloView frame 1](/home/alu52/github/vragecage/artifacts/virtual-capture-2026-10-13/view-1.png)

![Rejected blank virtual HoloView frame 2](/home/alu52/github/vragecage/artifacts/virtual-capture-2026-10-13/view-2.png)

## Integration boundary

The current renderer patch accepts explicit world-space origin/forward/up/FOV
in a static JSON request that is compiled into a staged mod. It schedules the
existing HoloView software rasterizer and stores RGBA plus per-frame metadata
in world storage. It is not a live request listener, and it does not accept a
Voidwright perspective ID. Its receipt records world name, tick, pose,
dimensions, and primitive count, but has no authoritative run ID, target grid
ID, frame hash, or snapshot linkage. The outer VRageCage receipt provides the
server run ID separately.

The regular Voidwright MCP adapter exposes snapshot/body/perspective/cell and
structural-raycast tools, but no `capture_view()`. This Codex session has no
configured Voidwright MCP tool surface or trusted SSH stdio adapter, so it
cannot request virtual captures through MCP yet.

## Code and tests

Task-specific changes in this repository:

- `worker/instrument-holoview-virtual.py`: supports bounded 2–4 pose requests.
- `worker/verify-holoview-virtual.py`: validates every frame has at least 100
  visible pixels and four distinct visible colors, in addition to provenance
  and pairwise image-difference checks.
- `worker/tests/test-holoview-virtual.py`: exercises three poses and rejects
  a transparent-black false positive.
- `worker/tests/fixtures/virtual-v2r3-blue-truck-three-poses.json` and
  `worker/tests/fixtures/virtual-v2r3-blue-truck-elevated-three-poses.json`:
  bounded pose request fixtures.
- `artifacts/virtual-capture-2026-10-13/`: captured images and evidence.

`python3 worker/tests/test-holoview-virtual.py` passed 8/8 tests. The live
renderer compiled and executed its virtual jobs, but the full lab receipt has
`passed=false` because the Voidwright exporter marker was absent. Existing
changes in both repositories were preserved; nothing was staged, committed,
or pushed.

## Smallest next step

First make the combined disposable mod load the Voidwright session component
and emit a fresh run-bound snapshot alongside HoloView. Then replace the
compiled pose list with a bounded world-storage request/response mailbox that
resolves an existing perspective ID, records target-grid/run/tick/hash
provenance, and returns an artifact reference. At that point, run fresh
perspective raycasts from the same exporter run and compare only what the
renderer actually depicts; cell-envelope hits must remain distinct from
detailed mesh visibility. Finally, retry the blank third view using a known
in-frame elevated pose before claiming three-view support.

Until those steps land: **Codex cannot invoke virtual captures through MCP,
and Anna cannot yet open the game and ask Codex for fresh visual inspection in
the same running world.** The live cooperative blocker is the missing
request-response bridge plus fresh same-run Voidwright/HoloView provenance.
