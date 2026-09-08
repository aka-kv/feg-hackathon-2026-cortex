# Cortex — Architecture & Technical Overview

FEG Innovation Hackathon 2026 · Challenge 1: Session Quality & Session-to-Action Conversion (PSK / Croatia sportsbook track)

Current, non-superseded design doc. Supersedes `architecture.md`'s original casino-focused draft, which was written before a mentor round redirected the project to a sportsbook-only, graph-based next-fixture recommendation system. The full build log, every verification step, and every bug found along the way live in `design-final-sports-graph.md`; this document is the condensed technical reference for a reviewer.

## 1. One-liner

After a session opens roughly 3 distinct fixtures without betting, recommend the next fixture it's most likely to act on — using nothing but real historical fixture co-occurrence (which fixtures get viewed together in the same session), computed offline and served in O(1) time. No trained model, no manufactured urgency, validated on a held-out 20% of real sessions the graph never saw.

## 2. System overview

Three layers, cleanly separated:

1. **Offline graph pipeline** (`src/graph_pipeline/`) — reads the raw event log once, cleans it, builds a fixture co-occurrence graph and a popularity baseline, writes both to disk as static artifacts. Runs whenever the underlying data changes, not per request.
2. **Serving layer** (`src/serving/`) — a FastAPI process that loads those artifacts once at startup and answers every request from them plus a small in-memory per-session state. No database.
3. **Frontend** (`src/frontend/`) — a static, dependency-free HTML/CSS/JS single-page app served by the same FastAPI process, plus a standalone graph-visualization page.

```
raw event log (CSV, outside repo)
        │  clean.py            -- casino-exclusion, malformed-row rules (§3.6 of design doc)
        ▼
nodes.py                        -- resolves each raw event row into a graph node
        │
        ▼
cooccurrence.py                 -- session-level "first seen before" fixture pairs -> weighted edges
sport_segmentation.py           -- fixture -> sport (for cross-sport contamination guard)
popularity.py                   -- global bet-intent popularity baseline (cold-start fallback)
build_sport_name_pools.py       -- real per-sport match-name pools (SB_Player.csv / EPS_Offers.csv)
        │
        ▼
artifacts/*.json, *.csv         -- fixture_adjacency, fixture_popularity, fixture_sport_map,
                                    sport_name_pools -- static, versioned, loaded once
        │
        ▼
src/serving/app.py (FastAPI)    -- loads artifacts at startup; SessionStore holds live session state
        │  POST /v1/events      -- write-time: recompute + cache this session's recommendation
        │  GET  /v1/recommend/cached/{session_key}  -- O(1) read, no computation
        ▼
src/frontend/index.html         -- login/guest browsing, Home + per-sport views, bet slip, checkout
src/frontend/graph.html         -- standalone 3D graph explorer (3d-force-graph)
```

## 3. Core recommendation algorithm (`src/graph_pipeline/rank.py`)

Deliberately *not* a trained model — tested and confirmed a trained model isn't necessary for this problem (see §7). The algorithm:

1. **Eligibility/protection gate runs first, unconditionally.** `eligibility_gate(protection_state)` returns `False` before any candidate is generated if the session isn't in an "allowed" state — a structural block, not a post-hoc filter. Production would wire this to FEG's real protection/eligibility signal; the hackathon sample has none, so this is an explicit stub, not a fabricated risk score.
2. **Cold start (fewer than 3 distinct fixtures viewed this session):** fall back to `popularity_candidates()` — the most-bet-on fixtures overall, restricted to whichever sport(s) the session has shown any interest in (or unrestricted for a genuine zero-signal visitor).
3. **Warm (3+ distinct fixtures viewed):** `graph_candidates()` aggregates real co-occurrence edges from every fixture the session has seen, ranked by summed edge weight, restricted to the sport(s) already engaged with (see §4). Backfilled with popularity candidates if the graph doesn't produce enough.
4. **Deterministic tie-breaking throughout** — `(score desc, fixture_id asc)`, explicit sorts before any set/dict iteration. Found and fixed a real non-determinism bug here (Python's hash-randomized set iteration order and pandas' non-stable default sort could silently flip which of two equally-scored fixtures won between runs); see `design-final-sports-graph.md` Phase E for the full incident writeup.
5. **Two assertions on every call**: no duplicate candidate fixture, no candidate the session has already opened. Belt-and-suspenders — the exclude-set logic already guarantees this, but a future edit that broke it should fail loudly here, not surface as a confusing UI bug.

`recommend()` also accepts an optional `forced_sport` — when a user has explicitly navigated to one sport's page, candidates are restricted to that sport regardless of what else the session has clicked, independent of the Home page's cross-sport inferred view.

## 4. Sport segmentation (fairness/diversity guard)

Verified failure mode before the fix: an unsegmented graph recommended one popular football match as the #1 pick for 53 held-out test sessions — 42 of which weren't even watching football. Fix: restrict candidates to the sport(s) the session has already engaged with, using the real `sport_name` field already in the data (99.7% coverage). Measured result: hit rate unchanged (0.4214, no accuracy cost), cross-sport violations `53 → 0`, top-1 fixture's share of all rank-1 picks `6.8% → 2.2%`. A more "principled" PMI/lift-normalization alternative was tried and rejected — it nearly halved hit rate (sparse co-occurrence counts mean normalizing by popularity mostly amplifies noise).

## 5. Serving layer (`src/serving/app.py`, `session_store.py`)

- **Write-time caching, not read-time computation.** Every `POST /v1/events` immediately recomputes and caches that session's recommendation. A Home-page or fixture-page read (`GET /v1/recommend/cached/{session_key}`) is then a single dict lookup — no graph traversal on the read path. Measured: recommendation computation is sub-10ms even on a warm (3+ fixture) session; cached reads are sub-millisecond.
- **`SessionStore`** is in-memory, TTL'd (30 minutes), and holds nothing beyond a list of graph nodes touched this session plus the cached recommendation — no persistent user profile, nothing written to disk, nothing that survives past the TTL. This is a data-minimization choice, not an accident (see `compliance-note.md`).
- **On-demand endpoints exist alongside the cached path** for cases the O(1) cache wasn't designed for: `/v1/recommend/sport/{session_key}/{sport_code}` (forced single-sport view) and `/v1/graph/session/{session_key}` / `/v1/graph/search` (bounded subgraphs for the 3D explorer) are computed per request, not cached — still low-single-digit milliseconds, just without the O(1) guarantee.
- **Two edge types in the live graph view**, both real: `cooccurrence` (from the precomputed artifact) and `session_path` (this exact session's own fixtures, connected in the real order they were opened — derived live from session state, since the precomputed artifact has no way to know about a session that didn't exist when it was built).

## 6. Frontend (`src/frontend/`)

Deliberate choice: vanilla HTML/CSS/JS, no build step, no framework — a pragmatic subset of a separately-produced full design spec (`frontend-design.md`, React/Vite), chosen explicitly over the full spec for hackathon time constraints.

- **Login**: two fixed demo accounts (a real, held-out, verified returning session; a genuine cold start) plus a **guest-browse path** — full browsing, sport-switching, and bet-staging works with zero login; only the final "Checkout" (real bet placement) is gated behind a login modal, which reuses the same session/signal that was already accumulating as a guest, so nothing resets.
- **Home + per-sport pages**, all sports (30 real sport codes surfaced via `/v1/sports`, not hardcoded) driven by the same shared render/animate engine, with an explicit "add to cart, then checkout" bet-slip model matching real Fortuna event semantics: staging a bet fires the real `betslip_add_bet` event immediately (the actual signal the recommender keys off), checkout fires `betslip_placed_bet`.
- **Illustrative match names**: real per-sport name pools (`sport_name_pools.json`, sourced from `SB_Player.csv`/`EPS_Offers.csv`), deterministically and collision-free assigned per fixture_id — not an exact fixture-to-name join (verified infeasible; see `compliance-note.md` §3), and disclosed as illustrative in the footer.
- **Standalone 3D graph explorer** (`graph.html`), using `3d-force-graph` (WebGL/three.js, CDN, no build step): a "live session" mode that polls the session subgraph and only redraws on genuine change (an earlier version redrew every poll regardless, which reheated the physics simulation and looked like blinking — fixed), and a "search full graph" mode across all 16,692 real fixtures.

## 7. Why not a trained ML model

Tested and confirmed unnecessary for this problem: plain counting/co-occurrence, held out properly, beats a naive popularity baseline by ~12.1x (0.4214 vs 0.0348 hit rate @ top-3, out of 16,692 real distinct fixtures) with zero training cost, full inspectability (every recommendation carries its source and raw score), and no cold-start problem beyond the same one any recommender has. A trained model would add serving latency, retraining cost, and a black-box surface with no demonstrated accuracy gain over this baseline — not adopted for novelty's sake where a simpler, provably-working approach exists.

## 8. Known limitations & deployment assumptions

- In-memory `SessionStore` is single-process — a real deployment behind multiple server instances needs a shared store (Redis or similar); not built, since the hackathon demo runs one process.
- CORS is wildcard-open (`allow_origins=["*"]`) — a dev-only convenience for the frontend/backend split origin, not a production policy.
- The eligibility/protection gate is a structural stub (`protection_state == "allowed"`) — production wires this to FEG's real eligibility system; no such feed exists in the hackathon sample.
- The two demo accounts' credentials are hardcoded in `app.py` for the live judge walkthrough — not a real auth system, and not real credentials for any real account.
- Raw provided data files are read from a path outside this repository (`src/graph_pipeline/config.py: DATA_DIR`) and are never committed — see `compliance-note.md` for exactly what derived data *is* committed and why.
