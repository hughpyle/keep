---
tags:
  category: later
  context: design
  topic: keep-ui
  status: open
---
# Keep UI — Design Document

Status: **drafting** (living doc — updated as decisions are made)
Owner: hugh
Started: 2026-04-07

A local web UI for Keep, served from the existing localhost daemon. Primary
functions: pick a top-level state, fill parameters, run it, explore the
resulting nodes and edges in a graph view, and take actions on selected
nodes.

## Vision

Keep has always been a memory system for agents. Adding a UI makes it
*also* a memory system humans can steer directly: open a browser, pick a
known-good workflow ("deep search", "review supernodes", …), type some
parameters, and get a live graph you can pan, zoom, expand, and act on.
The graph becomes the exploration surface for the store.

Optimise for: **maintainability, modern simplicity, responsiveness.**

## Constraints

- **Localhost only.** The daemon binds `127.0.0.1`. UI is served from the
  same origin and process. No new listener, no cloud, no external build.
- **Single artifact.** The UI is pre-built static assets shipped inside
  the `keep-skill` wheel. No Node at runtime. Users never install Node.
- **Additive.** No breaking changes to the existing `/v1/*` API.
- **Live.** Multiple clients may be updating the store concurrently
  (agents, MCP callers, CLI, other UI tabs). The UI reflects updates
  without user-initiated refresh.

## Conceptual model changes (new)

Two small conventions on state docs. Both are zero-code-change in the
flow runtime; they live as metadata that the UI layer reads.

### 1. "Top-level state" = state doc with a `name` tag

A state doc is a **user-visible entry point** iff its frontmatter
carries a `name` tag. The tag's value is the display label. The UI's
landing page lists exactly these docs. Unnamed state docs remain fully
invocable from other flows (today's behaviour) but are hidden from the
UI control surface. This gives us a curated catalogue without
restricting how states compose internally.

Today: **zero** state docs have a `name` tag. Greenfield.

### 2. `params:` schema — OpenAPI subset

State docs declare their parameters in a new optional frontmatter field
`params:`. The runtime ignores it (parameters are still discovered via
`{params.xxx}` references in the body); the UI reads it to build forms.

**Schema shape — subset of OpenAPI 3.1 Parameter Object.** In practice we
expect almost everything to be `type: string` with a `format`
discriminator. Supported initially:

```yaml
params:
  - name: query
    in: params                # always 'params'; fixed
    required: true
    description: "The search query."
    schema:
      type: string
      title: "Query"          # display label (optional; falls back to name)
  - name: until
    schema:
      type: string
      format: datetime        # rendered as a date/time picker
  - name: window
    schema:
      type: string
      format: duration        # ISO 8601 duration; rendered as a duration picker
  - name: notes
    schema:
      type: string
      format: markdown        # multi-line markdown editor
      default: ""
  - name: limit
    schema:
      type: integer
      default: 10
      minimum: 1
      maximum: 500
```

Supported `format` values on strings (initial set):

| format     | UI control            |
|------------|-----------------------|
| (none)     | single-line text      |
| datetime   | date/time picker      |
| duration   | duration picker (ISO 8601) |
| markdown   | multi-line markdown editor |
| id         | note-ID autocomplete  |
| tag-key    | tag-key picker        |
| tag-value  | tag-value picker      |
| uri        | single-line text w/ URL validation |

Supported base types: `string | integer | number | boolean`. Arrays and
nested objects are out of scope for v1; if a state really needs one it
can split into multiple params.

We'll follow the OpenAPI 3.1 `Schema Object` shape so future extensions
(enums, allOf, etc.) don't need a new grammar.

## Architecture

Three tiers, all inside the existing daemon process.

```
┌─────────────────────────────────────────────────────────────┐
│  Browser (localhost)                                         │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  React SPA                                            │  │
│  │  ┌──────────┐ ┌────────────┐ ┌────────────────────┐   │  │
│  │  │  State   │ │   Graph    │ │  Multi-panel dock  │   │  │
│  │  │  picker  │→│   canvas   │→│  (node inspectors, │   │  │
│  │  │ + form   │ │ (2D / 3D)  │ │   actions, props)  │   │  │
│  │  └──────────┘ └────────────┘ └────────────────────┘   │  │
│  │     ▲                 ▲               ▲               │  │
│  │     │   TanStack      │    SSE        │               │  │
│  │     │   Query         │    stream     │               │  │
│  └─────┼─────────────────┼───────────────┼───────────────┘  │
└────────┼─────────────────┼───────────────┼──────────────────┘
         │                 │               │ HTTP (localhost, same origin)
┌────────┼─────────────────┼───────────────┼──────────────────┐
│  keep daemon (ThreadingHTTPServer)                           │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Existing:  /v1/notes, /v1/flow, /v1/tag, ...         │  │
│  │  New:                                                 │  │
│  │    GET  /ui/            → index.html                  │  │
│  │    GET  /ui/*           → static assets               │  │
│  │    GET  /ui/bootstrap   → { auth_token, daemon_info } │  │
│  │    GET  /v1/ui/states   → named state docs + schemas  │  │
│  │    POST /v1/ui/flow     → start async flow run        │  │
│  │    GET  /v1/ui/flow/:id → SSE stream for run output   │  │
│  │    POST /v1/ui/graph    → hydrate items → nodes/edges │  │
│  │    GET  /v1/ui/events   → SSE: store change events    │  │
│  └───────────────────────────────────────────────────────┘  │
│                                │                            │
│  ┌─────────────────────────────▼────────────────────────┐   │
│  │  Keeper: flow runner, doc store, vector store,       │   │
│  │  state docs, edge-tags, **event bus (new)**          │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

**Why this shape.** The daemon already owns the store, the auth token,
and a ThreadingHTTPServer. Piggybacking the UI means zero new process
lifecycle, zero new auth surface, zero new deployment. `/v1/ui/*` is a
parallel versioned namespace to the existing `/v1/*` API; `/ui/*` is
the static bundle. The two namespaces are completely separable and can
be removed or versioned without affecting agent clients.

## Async flows and notifications

**Decided: SSE**, not WebSockets.

Rationale:

- Flow → UI and store → UI are both **server-push** dominant. The UI
  pushes back via regular `/v1/*` REST endpoints. We don't need the
  bidirectional channel a WebSocket gives you.
- SSE is native `EventSource` in the browser. Auto-reconnect, event IDs,
  "last-event-id" replay — all built in.
- SSE is plain HTTP. Fits cleanly into the existing `ThreadingHTTPServer`
  without adding a framework dependency. The handler keeps the socket
  open, writes `data: …\n\n` chunks, and flushes after each event. One
  thread per subscriber is fine at our scale.
- Upgrading a single route to WebSockets later is straightforward if
  a use-case demands it. Starting with WebSockets would be premature.

### Two SSE streams

**1. Flow-run stream: `GET /v1/ui/flow/:run_id`**

- Client calls `POST /v1/ui/flow { state, params }` which enqueues the
  flow and returns a `run_id` immediately (no waiting).
- Client opens an `EventSource` on `/v1/ui/flow/:run_id`.
- Server emits events as the flow progresses:

  ```
  event: tick
  data: {"step":"search","results":42}

  event: result
  data: {"nodes":[...], "edges":[...]}    # graph delta

  event: log
  data: {"level":"info","message":"..."}

  event: done
  data: {"status":"success","summary":{...}}
  ```

- `done` closes the stream; `error` closes with the failure payload.
- The run_id is scoped to the daemon process (not persisted). If the
  daemon restarts mid-run, the client reconnects, gets a 404, and the
  UI surfaces "run lost — please re-submit".

**2. Store-change stream: `GET /v1/ui/events`**

- A single long-lived SSE connection per browser tab.
- Server emits events on any store mutation, regardless of who caused
  it (this tab, another tab, the CLI, an agent, a watch tick):

  ```
  event: item_put
  data: {"id":"file:///…/doc.md","version":3,"tags":{...}}

  event: item_deleted
  data: {"id":"…"}

  event: item_tagged
  data: {"id":"…","added":{...},"removed":[...]}

  event: item_moved
  data: {"old_id":"…","new_id":"…"}

  event: watch_added
  data: {"source":"…","kind":"directory"}
  ```

- The UI uses these to live-update any visible nodes, toasts for other
  clients' changes, and to trigger graph re-hydration when a visible
  node moves.
- Backend implementation: a small in-process **event bus** in the
  Keeper (publish on each mutation). SSE handler subscribes to the bus
  per connection. No persistence, no replay history — clients that
  disconnect miss events and must reconcile on reconnect (refetch).
  We can add persistence later if the use-case emerges.

### Event bus scope

Keep the bus minimal and in-memory:

- A `threading.Lock`-guarded list of subscriber callables.
- `keeper.events.publish(event_type, payload)` called from the existing
  mutation methods: `put`, `delete`, `tag`, `move` (etc.).
- Subscribers are the SSE handlers; each one owns a `queue.Queue` it
  drains in its request thread.
- No cross-process concern (single daemon). No backpressure beyond a
  bounded queue (drop oldest + log if a subscriber falls behind).

This is ~60 lines of Python.

## New API surface

All endpoints are new. Existing `/v1/*` endpoints are untouched.

| Method | Path | Purpose |
|---|---|---|
| GET | `/ui/` | Serve `index.html` |
| GET | `/ui/*` | Serve static asset (fall back to `index.html` for SPA routes) |
| GET | `/ui/bootstrap` | Returns `{auth_token, daemon_port, version}` |
| GET | `/v1/ui/states` | List top-level state docs (those with a `name` tag) with their `params:` schemas |
| POST | `/v1/ui/flow` | Start an async flow run. Body: `{state, params}`. Returns `{run_id}` |
| GET | `/v1/ui/flow/:run_id` | SSE stream for the run's events (tick, result, log, done, error) |
| POST | `/v1/ui/graph` | Hydrate a set of items into `{nodes, edges}`. Parameterless — backend decides the projection |
| GET | `/v1/ui/events` | SSE stream of store-change events (item_put / item_deleted / item_tagged / item_moved / …) |

**Graph hydrator** (`POST /v1/ui/graph`) is intentionally parameterless.
The backend owns the logic for "which items become nodes" and "which
tags become edges" (references, cites, informs, parts, versions,
similar). The UI never has to know. Means graph semantics can evolve
without shipping a new UI build.

Existing `/v1/*` endpoints cover all write actions the UI needs:
`put` (tag set/remove), `delete`, `tag`, and a `move` action via
`/v1/flow` running `state-move`. The UI doesn't need any new write
endpoints.

## Component selection (locked)

| Concern | Choice | Why |
|---|---|---|
| Framework | **React 19 + TypeScript** | Ecosystem, long-term maintainable |
| Build | **Vite** | Fast dev, predictable static output |
| Styling | **Tailwind CSS** | Utility-first, zero runtime |
| Primitives | **shadcn/ui** (Radix + Tailwind, copy-paste) | No runtime component-lib upgrade treadmill; accessible |
| Icons | **lucide-react** | shadcn default |
| Routing | **React Router v6** | Lazy routes for code-splitting |
| Server state | **TanStack Query** | Cache, invalidation, devtools |
| Client state | **Zustand** | Tiny; fits the graph/panel model |
| Forms | **React Hook Form + Zod** | Generated from `params:` schema; runtime validation |
| Tables | **TanStack Table + Virtual** | Virtualised result lists |
| Graph 2D/3D | **react-force-graph** (canvas + Three.js) | Single API for both |
| Docking | **dockview** | True multi-panel dock with drag to rearrange |
| SSE | native `EventSource` | Standard browser API |
| Theme | Tailwind dark mode + CSS vars | shadcn convention |

Deliberate non-choices:

- No Next.js — we want a static SPA, not a Node server.
- No Redux / RTK / MobX — TanStack Query + Zustand cover it.
- No Material UI / Chakra / Mantine — runtime bloat and upgrade treadmill.
- No Cytoscape / sigma for v1 — `react-force-graph` covers 2D+3D under
  one API. Kept behind a `GraphRenderer` interface so a swap is additive.
- No WebSockets for v1 — SSE is enough.

## Data flow — happy path

```
1.  GET /ui/              → index.html + bundle
2.  GET /ui/bootstrap     → { auth_token, daemon_port, version }
3.  GET /v1/ui/states     → [ {id, name, description, params} … ]
4.  User picks a state  → app renders a form from the params schema
5.  User submits
      POST /v1/ui/flow { state, params } → { run_id }
6.  EventSource /v1/ui/flow/:run_id
      streams tick, result, log events
      on 'result' events, app POSTs /v1/ui/graph and merges deltas
      on 'done', stream closes, flow card shows summary
7.  Node click    → GET /v1/notes/{id} → dock opens a NodePanel tab
8.  Node actions  → POST /v1/notes (tag), POST /v1/flow (move),
                    DELETE /v1/notes/{id}, etc.
9.  Background    → EventSource /v1/ui/events (single per tab)
                    live-updates any visible node / triggers re-hydrate
```

## Graph model

```ts
type NodeId = string                           // = keep item ID

interface GraphNode {
  id: NodeId
  label: string                                // `name` → `title` → summary excerpt
  kind: 'note' | 'system' | 'stub' | 'state' | 'tag'
  tags: Record<string, string | string[]>
  summary: string
  color?: string
  size?: number
  x?: number; y?: number; z?: number           // optional fixed layout
}

interface GraphEdge {
  id: string                                    // `${src}:${key}:${tgt}`
  source: NodeId
  target: NodeId
  kind: 'edge-tag' | 'reference' | 'similar' | 'part-of' | 'version-of'
  key?: string
  weight?: number
}

interface GraphDelta {
  nodes?: GraphNode[]
  edges?: GraphEdge[]
  removed?: { nodeIds?: NodeId[]; edgeIds?: string[] }
}

interface GraphModel {
  nodes: Map<NodeId, GraphNode>
  edges: Map<string, GraphEdge>
}
```

### Renderer abstraction

```ts
interface GraphRenderer {
  mount(container: HTMLElement, model: GraphModel): void
  applyDelta(delta: GraphDelta): void
  onNodeClick(handler: (id: NodeId) => void): void
  onNodeHover(handler: (id: NodeId | null) => void): void
  setLayout(layout: '2d-force' | '3d-force' | '2d-radial'): void
  destroy(): void
}
```

Initial implementations: `ForceGraph2DRenderer`, `ForceGraph3DRenderer`
(both from `react-force-graph`). A later `CosmographRenderer` plugs in
for graphs > ~5k nodes without touching the UI shell.

The Zustand store owns the canonical `GraphModel` (Map-backed for O(1)
updates). The store dispatches deltas to the mounted renderer. Decoupling
data from display is the single most important property for
maintainability.

## Multi-panel dock

Decided: **true multi-panel dock** (dockview), not an inspector stack.

- Users can open several NodePanel tabs, drag them to split horizontally
  or vertically, and stack them.
- Each NodePanel shows one item and offers the four node actions (view
  / tag / untag / move). Tabs persist until closed.
- Dock layout is local state only (not persisted across sessions in v1);
  we can add URL-state serialisation later.

## Node actions (v1)

Exactly four:

| Action | Backend call | Notes |
|---|---|---|
| **View** | `GET /v1/notes/{id}` | Show tags, summary, content, edges |
| **Tag** | `POST /v1/notes/{id}` with `tags` | Add a key=value tag |
| **Untag** | `POST /v1/notes/{id}` with `remove_values` | Remove a value from a key, or the whole key |
| **Move (rename)** | `POST /v1/flow` running `state-move` | Uses the existing move action (`name`, `source`, `tags`, `only_current`) |

Edit/delete/re-analyze are explicitly out of scope for v1. They fit in
Phase 2.

## Directory layout

```
keep/
  ui/                            # new Python-side package
    __init__.py                  # ASSETS_DIR helper
    events.py                    # in-process event bus
    app/                         # JS source tree
      package.json
      vite.config.ts
      tsconfig.json
      tailwind.config.ts
      index.html
      src/
        main.tsx
        App.tsx
        routes/
          index.tsx              # state picker
          run.tsx                # param form → submit → flow stream
          graph.tsx              # graph canvas + dock
        lib/
          api.ts                 # fetch wrappers, bearer token
          sse.ts                 # EventSource helpers
          auth.ts                # /ui/bootstrap
          types.ts               # GraphNode / GraphEdge / ParamSchema
          store.ts               # Zustand: graph, selection, dock layout
          query.ts               # TanStack Query setup
        components/
          states/
            StatePicker.tsx
            ParameterForm.tsx    # generated from params schema
            FieldRenderers/      # one per format (datetime, duration, markdown, …)
          graph/
            GraphCanvas.tsx      # renderer-agnostic shell
            ForceGraph2D.tsx
            ForceGraph3D.tsx
            GraphToolbar.tsx
          dock/
            NodeDock.tsx         # dockview wrapper
            NodePanel.tsx        # per-node tab content
            actions/
              TagForm.tsx
              UntagForm.tsx
              MoveForm.tsx
          ui/                    # shadcn primitives, checked in
    dist/                        # built assets; gitignored; built for release
keep/daemon_server.py            # + routes for /ui/*, /ui/bootstrap, /v1/ui/*
keep/api.py                      # + event bus integration
scripts/release.sh               # + (cd keep/ui/app && npm ci && npm run build)
```

## Packaging, build, dev workflow

**Dev:**

- `cd keep/ui/app && npm run dev` → Vite dev server on `localhost:5173`
- Vite proxies `/v1/*`, `/ui/bootstrap` to the running daemon
- HMR on every save; no backend restart
- Dev-only: bearer token read from `~/.keep/.daemon.token` via a tiny
  Vite middleware; injected into the dev bundle

**Build:**

- `npm ci && npm run build` in `keep/ui/app/` → `keep/ui/dist/`
- `pyproject.toml` includes `keep/ui/dist/**` in the wheel (package-data)
- `scripts/release.sh` gains a build step before `uv build`; fails fast
  if `npm ci` or `npm run build` fails

**Runtime:**

- Daemon imports `keep.ui` which exposes `ASSETS_DIR`
- `_handle_ui_asset` in `daemon_server.py`: path-sanitise, serve file
  with correct MIME, fall back to `index.html` for unknown SPA routes
- First-load target: ≤ 500 KB gzipped (React + Tailwind + force-graph +
  dockview tree-shaken)

**No Node at runtime.** Once built, the wheel is pure Python + static
files.

## Security

- **127.0.0.1 bind verified.** Already the case; add a startup assertion
  that rejects non-loopback binds. Covered by a test.
- **Same-origin UI and API.** No CORS headers needed.
- **Auth.** UI bootstraps via `GET /ui/bootstrap`, which returns the
  bearer token. Anyone with read access to `~/.keep/.daemon.token` has
  the token already; `/ui/bootstrap` is not a new leak. Token lives in
  memory only — never `localStorage`.
- **CSP.** Responses on `/ui/*` carry:
  ```
  Content-Security-Policy: default-src 'self';
    script-src 'self'; style-src 'self' 'unsafe-inline';
    img-src 'self' data:; connect-src 'self';
  ```
- **No cookies, no sessions.** Bearer header only.
- **SSE over bearer.** `EventSource` can't set headers, so we pass the
  token as a query parameter on `/v1/ui/flow/:id?token=…` and
  `/v1/ui/events?token=…`. The token never leaves localhost; query-param
  leakage via Referer doesn't apply on same-origin fetches. Alternative
  if we want cleaner URLs: `fetch` + `ReadableStream` manually —
  slightly more code, no token in URL. We default to the query-param
  form for v1; revisit if needed.

## Phased plan

### Phase 1 — MVP

Backend:

- [ ] Event bus (`keep/ui/events.py`) — publish on put/delete/tag/move
- [ ] `GET /v1/ui/states` — list state docs with a `name` tag
- [ ] `POST /v1/ui/flow` + `GET /v1/ui/flow/:run_id` (SSE) — async run
- [ ] `POST /v1/ui/graph` — parameterless hydrator
- [ ] `GET /v1/ui/events` (SSE) — store change events
- [ ] `GET /ui/bootstrap` + static asset serving
- [ ] Add `name` tag and `params:` schema to a seed set of state docs
  (propose: `find-deep`, `memory-search`, `query-explore`,
  `review-supernodes`, plus a new `browse-recent` and `open-item`)

Frontend:

- [ ] Vite + React + Tailwind + shadcn scaffold
- [ ] StatePicker + ParameterForm (generated from `params:` schema)
- [ ] Flow submit + SSE result stream
- [ ] 2D graph view (react-force-graph-2d)
- [ ] Multi-panel dock (dockview)
- [ ] NodePanel with view/tag/untag/move actions
- [ ] Live store-event subscription

Packaging:

- [ ] npm build integrated into `scripts/release.sh`
- [ ] Wheel includes `keep/ui/dist/**`

**Success criterion:** user opens `http://localhost:<port>/ui/`, picks
"Deep search", types a query, submits, and sees a live graph of ~100
nodes. Clicks a node, opens it in a dock panel, adds a tag, sees the
change reflected. Another CLI client adds a note; the UI reflects it
without a page reload.

### Phase 2 — depth

- 3D view (`react-force-graph-3d`) behind a 2D/3D toggle
- Graph expansion: "load neighbours" on a node
- Saved graph view in URL state (shareable as a localhost link)
- Node actions: edit, delete, re-analyze
- Dock layout persisted across sessions

### Phase 3 — scale and polish

- Cosmograph backend for graphs > ~5k nodes
- Graph filtering by tag / edge kind
- Clustering / centrality overlays
- Search within the graph
- Export/import graph snapshots
- SSE replay (event ID / last-event-id) for reliability across reconnects

## Decisions

| # | Decision | Status |
|---|---|---|
| 1 | Top-level state = state doc with a `name` tag | **Locked** |
| 2 | `params:` schema = OpenAPI subset (mostly `type: string` + `format: datetime/duration/markdown/…`) | **Locked** |
| 3 | Async flows + server push: **SSE** (not WebSockets) | **Locked** |
| 4 | Store-change notifications via a second SSE stream + in-process event bus | **Locked** |
| 5 | Node actions in v1: view, tag, untag, move (rename) | **Locked** |
| 6 | Multi-panel **dock** (dockview), not an inspector stack | **Locked** |
| 7 | Graph hydrator is **parameterless** — projection logic lives in backend code | **Locked** |
| 8 | Graph renderer behind a `GraphRenderer` interface; v1 ships 2D only; 3D in Phase 2 | **Locked** |
| 9 | Framework: React + Vite + Tailwind + shadcn/ui + TanStack Query + Zustand + React Hook Form + react-force-graph + dockview | **Locked** |
| 10 | Monorepo layout (`keep/ui/app/`), not a sibling repo | **Locked** |
| 11 | SSE auth via `?token=` query param (EventSource can't set headers) | **Locked; revisit if needed** |

## Open questions

- **Seed set of named states.** Proposed: `find-deep`, `memory-search`,
  `query-explore`, `review-supernodes`, plus two new helpers
  `browse-recent` (list recent notes) and `open-item` (by ID → graph
  neighbourhood). Confirm or replace.
- **Run IDs — format and lifetime.** In-memory UUID with a soft TTL of
  one hour after completion, so the UI can reconnect shortly after. No
  persistence.
- **Event bus replay.** v1: no replay, reconcile on reconnect. Phase 3:
  bounded ring buffer with last-event-id support.
- **3D in Phase 1 vs Phase 2.** Current plan is 2D first, 3D in Phase 2.
  If the 3D view is a headline feature, we can promote it to Phase 1 at
  the cost of a bit more layout tuning upfront.
- **Store-event granularity.** Publish every mutation, or coalesce
  within a small window (100 ms) before emitting? Coalescing reduces
  event chatter on bulk operations (a 1000-file directory put). Lean
  towards "coalesce in the bus, emit every 100 ms" for v1.

## Changelog

- 2026-04-07 — initial draft captured; decisions 1-11 locked after
  answers on async (SSE), multi-panel dock, v1 node actions
  (view/tag/untag/move), parameterless hydrator, OpenAPI-subset params
  schema.
