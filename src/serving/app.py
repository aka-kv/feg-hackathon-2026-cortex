"""Phase D serving layer.

Assumes upstream event routing already sends only sportsbook-relevant
events to this service (this API does not re-run the casino-exclusion
filter from clean.py — that's a batch-ingestion concern; a live client
instrumented for the sports-only scope in docs §1 shouldn't be emitting
casino events here in the first place).

Run: uvicorn src.serving.app:app --reload --port 8000
"""

from __future__ import annotations

import json
import time
import uuid
from collections import Counter
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..graph_pipeline import config, nodes, rank
from .session_store import SessionStore

ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "artifacts"
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"

# Real sport codes found in the raw data (sport_segmentation.py's
# build_fixture_sport_map, lowercased Croatian sport_name values) -> English
# display labels. This is display-only: filtering/recommendation logic
# always operates on the raw code, never the translated label, so an
# unmapped code (falls back to a title-cased version of itself below) still
# works correctly, just with a less polished label.
SPORT_LABELS = {
    "nogomet": "Football",
    "tenis": "Tennis",
    "enogomet": "E-Football",
    "ekošarka": "E-Basketball",
    "košarka": "Basketball",
    "stolni tenis": "Table Tennis",
    "rukomet": "Handball",
    "ehokej": "E-Hockey",
    "hokej": "Hockey",
    "odbojka": "Volleyball",
    "baseball": "Baseball",
    "borilački sportovi": "Combat Sports",
    "esport counter strike": "Esports: CS",
    "vaterpolo": "Water Polo",
    "top ponuda": "Featured",
    "pikado": "Darts",
    "odbojka na pijesku": "Beach Volleyball",
    "američki nogomet": "American Football",
    "rugbi": "Rugby",
    "badminton": "Badminton",
    "futsal": "Futsal",
    "hokej na travi": "Field Hockey",
    "australski nogomet": "Australian Football",
    "atletika": "Athletics",
    "boks": "Boxing",
    "plivanje": "Swimming",
    "ostali sportovi": "Other Sports",
    "nogomet na pijesku": "Beach Football",
    "duel": "Duel",
    "formula 1": "Formula 1",
}

app = FastAPI(title="Threshold — Sportsbook Session Graph (Phase D)")
session_store = SessionStore()

# Demo-only: the frontend is a static page served from a different dev
# origin during development. Not a production CORS policy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Loaded once at process start — this is the whole point of persisting
# build_artifacts.py's output rather than rebuilding per request or per
# process restart (docs §6: heavy aggregation offline, O(1) lookups online).
_fixture_adjacency: dict = {}
_popularity_df = None
_sport_map: dict = {}
# Real raw event history, indexed by PlayerID, used ONLY to seed a login
# demo with a real returning user's actual recent fixtures -- never used to
# fabricate history for an anonymous/new session. Loaded once at startup,
# same reasoning as the other artifacts.
_raw_events_by_player: dict = {}
# Keyed by the exact (session, PlayerID) composite -- needed because two of
# the 10 verified demo users share the same PlayerID across two DIFFERENT
# sessions; player_id alone would mix their histories together.
_raw_events_by_session_player: dict = {}
_demo_users: list = []
# Real per-sport match-name pools (build_sport_name_pools.py), sourced from
# SB_Player.csv/EPS_Offers.csv -- see that module's docstring for exactly
# why this is a per-sport pool and not an exact fixture_id -> name join.
_sport_name_pools: dict = {}


@app.on_event("startup")
def load_artifacts() -> None:
    global _fixture_adjacency, _popularity_df, _raw_events_by_player, _sport_map
    global _raw_events_by_session_player, _demo_users, _sport_name_pools
    with open(ARTIFACTS_DIR / "fixture_adjacency.json", encoding="utf-8") as f:
        _fixture_adjacency = json.load(f)

    _popularity_df = pd.read_csv(ARTIFACTS_DIR / "fixture_popularity.csv", index_col=0)

    with open(ARTIFACTS_DIR / "fixture_sport_map.json", encoding="utf-8") as f:
        _sport_map = json.load(f)

    sport_name_pools_path = ARTIFACTS_DIR / "sport_name_pools.json"
    if sport_name_pools_path.exists():
        with open(sport_name_pools_path, encoding="utf-8") as f:
            _sport_name_pools = json.load(f)

    raw = pd.read_csv(config.EVENT_LOG_PATH, dtype=str, keep_default_na=False)
    raw["_ts"] = pd.to_datetime(raw["timestamp"], errors="coerce", utc=True)
    raw = raw.sort_values("_ts")
    _raw_events_by_player = {pid: g for pid, g in raw.groupby("PlayerID")}
    _raw_events_by_session_player = {
        f"{s}::{pid}": g for (s, pid), g in raw.groupby(["session", "PlayerID"])
    }

    demo_users_path = ARTIFACTS_DIR / "ten_users_source_of_truth.json"
    if demo_users_path.exists():
        with open(demo_users_path, encoding="utf-8") as f:
            _demo_users = json.load(f)


def _compute_and_cache_recommendation(session_key: str, top_n: int = 3) -> tuple[dict, float]:
    """The write-time computation: called immediately after any event is
    ingested (or a login seed), so a later read is O(1). Returns the result
    plus how long it actually took, in milliseconds -- measured, not
    asserted."""
    t0 = time.perf_counter()
    recent_nodes = session_store.get_recent_nodes(session_key)
    protection_state = session_store.get_protection_state(session_key)
    result = rank.recommend(recent_nodes, _fixture_adjacency, _popularity_df, protection_state, top_n, sport_map=_sport_map)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    session_store.set_cached_recommendation(session_key, result)
    return result, elapsed_ms


class EventIn(BaseModel):
    session_key: str  # must already be the (session, PlayerID) composite — see docs §3.6 rule 1
    event_name: str
    fortuna_screen_name: str = ""
    platform: str = ""
    added_from: str = ""
    fixture_id: str = ""
    page_location: str = ""
    # Demo stand-in only: production sources this from FEG's real
    # eligibility/host system, never a client-supplied field.
    protection_state: str = "allowed"


class RecommendQuery(BaseModel):
    session_key: str
    protection_state: str  # required, no default — fail-closed if the caller doesn't supply it
    top_n: int = 3


class LoginRequest(BaseModel):
    player_id: str = ""  # a real PlayerID from top_sport_users_event_logs.csv, or "" for a brand-new anonymous session
    seed_recent_fixtures: int = 0  # how many of this player's real recent distinct fixtures to seed the session with


@app.post("/v1/events")
def post_event(event: EventIn):
    interaction_node, content_node = nodes.resolve_single_event(
        event_name=event.event_name,
        fortuna_screen_name=event.fortuna_screen_name,
        platform=event.platform,
        added_from=event.added_from,
        fixture_id=event.fixture_id,
        page_location=event.page_location,
    )
    new_nodes = [interaction_node] + ([content_node] if content_node else [])
    state = session_store.add_nodes(event.session_key, new_nodes, protection_state=event.protection_state)

    # Write-time computation: the recommendation is (re)computed and cached
    # right here, not deferred to whenever the home page happens to ask for
    # it. A later read is then O(1) -- see /v1/recommend/cached below.
    _, elapsed_ms = _compute_and_cache_recommendation(event.session_key)

    return {
        "session_key": event.session_key,
        "nodes_added": new_nodes,
        "distinct_fixtures_seen": len(rank.viewed_fixtures(state.nodes)),
        "recommendation_computed_and_cached_in_ms": round(elapsed_ms, 3),
    }


@app.post("/v1/recommend")
def post_recommend(query: RecommendQuery):
    """On-demand computation path -- kept for direct testing/debugging.
    The demo's actual home-page read should use /v1/recommend/cached."""
    recent_nodes = session_store.get_recent_nodes(query.session_key)
    result = rank.recommend(
        recent_nodes,
        _fixture_adjacency,
        _popularity_df,
        protection_state=query.protection_state,
        top_n=query.top_n,
        sport_map=_sport_map,
    )
    return result


@app.get("/v1/recommend/cached/{session_key}")
def get_cached_recommendation(session_key: str):
    """The real read path for a home-page load: a single dict lookup,
    zero graph computation on this request."""
    t0 = time.perf_counter()
    result = session_store.get_cached_recommendation(session_key)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    if result is None:
        return {"allowed": False, "reason": "no_session_or_expired", "candidates": [], "read_time_ms": round(elapsed_ms, 4)}
    return {**result, "read_time_ms": round(elapsed_ms, 4)}


@app.post("/v1/login")
def login(req: LoginRequest):
    """Demo-only convenience: simulate a known user returning, seeded with
    THEIR OWN real historical fixture views (never fabricated) pulled from
    the raw event log. A brand-new/anonymous user should call this with
    player_id="" and seed_recent_fixtures=0, equivalent to just starting
    fresh with no seed at all."""
    session_key = f"login-{uuid.uuid4().hex[:8]}::{req.player_id or 'anonymous'}"
    session_store.ensure_session(session_key, protection_state="allowed")

    seeded_fixtures: list[str] = []
    if req.player_id and req.seed_recent_fixtures > 0:
        player_rows = _raw_events_by_player.get(req.player_id)
        if player_rows is None:
            raise HTTPException(404, f"No history found for player_id={req.player_id}")
        distinct_recent = []
        for fid in reversed(player_rows["fixture_id"].tolist()):
            if fid and fid not in distinct_recent:
                distinct_recent.append(fid)
            if len(distinct_recent) >= req.seed_recent_fixtures:
                break
        seeded_fixtures = list(reversed(distinct_recent))
        seed_nodes = []
        for fid in seeded_fixtures:
            seed_nodes.append("SCREEN:prematchDetail")
            seed_nodes.append(f"FIXTURE:{fid}")
        session_store.add_nodes(session_key, seed_nodes, protection_state="allowed")

    _, elapsed_ms = _compute_and_cache_recommendation(session_key)

    return {
        "session_key": session_key,
        "player_id": req.player_id or None,
        "seeded_real_fixtures": seeded_fixtures,
        "recommendation_computed_and_cached_in_ms": round(elapsed_ms, 3),
    }


class VerifiedLoginRequest(BaseModel):
    session_key: str  # one of the 10 verified real (session, PlayerID) keys from /v1/demo-users
    seed_count: int = 3  # how many of THIS session's own real distinct fixtures to seed with


# Two fixed demo accounts for the live judge walkthrough. "marko" is a real,
# held-out, verified session (1785708852) -- deliberately started with NO
# pre-seeded history, so the 3 fixtures are opened live, on screen, by
# whoever is doing the demo, not invisibly at login. The exact real fixture
# IDs are pinned to the front of that account's fixture list so they're
# always in the same place during a live walkthrough. "guest" is a genuine
# cold start with no target sequence at all.
_DEMO_ACCOUNTS = {
    "marko89": {
        "password": "psk2026",
        "display_name": "Marko K.",
        "target_session_key": "1785708852::56fd153546543a43546431026270d46e67652947be5e9c17eda3fee4d970938f",
    },
    "newplayer": {
        "password": "psk2026",
        "display_name": "Guest",
        "target_session_key": None,
    },
}


class AuthLoginRequest(BaseModel):
    username: str
    password: str


HISTORY_SEED_MAX_FIXTURES = 10


@app.post("/v1/auth/login")
def auth_login(req: AuthLoginRequest):
    account = _DEMO_ACCOUNTS.get(req.username)
    if account is None or account["password"] != req.password:
        raise HTTPException(401, "Invalid username or password")

    session_key = f"user-{uuid.uuid4().hex[:8]}::{req.username}"
    target_session_key = account["target_session_key"]
    target_fixtures: list[str] = []
    if target_session_key:
        session_rows = _raw_events_by_session_player.get(target_session_key)
        if session_rows is not None:
            for _, row in session_rows.sort_values("_ts").iterrows():
                fid = row["fixture_id"]
                if fid and fid not in target_fixtures:
                    target_fixtures.append(fid)
                if len(target_fixtures) >= 3:
                    break

    # A returning, identified customer with real account history has no
    # reason to see generic cold-start popularity -- that's exactly the
    # thing that should distinguish an existing customer from a brand-new
    # one. Seed the session with the player's OWN real fixtures from their
    # OTHER sessions (never other players' data), so Home is already
    # personalized the instant they log in, before any click in this new
    # session. Deliberately excludes target_session_key's own rows -- the 3
    # pinned live-demo fixtures stay unopened, so the live walkthrough (open
    # them one by one, watch the recommendation refine further) still works
    # on top of this. A genuinely new visitor (target_session_key=None, e.g.
    # "newplayer") has no history to seed from and correctly stays cold-start
    # -- this isn't a special case, it's the same rule with no data to act on.
    history_seeded_fixtures: list[str] = []
    if target_session_key:
        player_id = target_session_key.split("::")[1]
        player_rows = _raw_events_by_player.get(player_id)
        if player_rows is not None:
            raw_session = target_session_key.split("::")[0]
            other_rows = player_rows[player_rows["session"] != raw_session]
            for _, row in other_rows.sort_values("_ts", ascending=False).iterrows():
                fid = row["fixture_id"]
                if fid and fid not in history_seeded_fixtures and fid not in target_fixtures:
                    history_seeded_fixtures.append(fid)
                if len(history_seeded_fixtures) >= HISTORY_SEED_MAX_FIXTURES:
                    break
            history_seeded_fixtures.reverse()  # chronological order, oldest of the recent window first

    # ensure_session must run before caching -- a session with zero nodes is
    # a legitimate cold-start state, but the cache write silently no-ops
    # against a session key that was never created (found by running this
    # exact login -> immediate Home-page-view sequence end to end).
    session_store.ensure_session(session_key, protection_state="allowed")
    if history_seeded_fixtures:
        seed_nodes = []
        for fid in history_seeded_fixtures:
            seed_nodes.append("SCREEN:prematchDetail")
            seed_nodes.append(f"FIXTURE:{fid}")
        session_store.add_nodes(session_key, seed_nodes, protection_state="allowed")
    _, elapsed_ms = _compute_and_cache_recommendation(session_key)

    # Which sport the pinned target fixtures actually belong to -- found by
    # checking the real sport_map rather than assumed (they turned out to be
    # tenis, not football). The frontend uses this to know which sport tab
    # to pin the 3 targets into; every other sport tab is fetched normally
    # via /v1/browse-fixtures, sport-scoped, with no mixed-sport filler list.
    target_sport = None
    if target_fixtures:
        target_sport = _sport_map.get(f"FIXTURE:{target_fixtures[0]}")

    return {
        "session_key": session_key,
        "display_name": account["display_name"],
        "history_seeded_fixtures": history_seeded_fixtures,
        "target_fixtures": target_fixtures,
        "target_sport": target_sport,
        "recommendation_computed_and_cached_in_ms": round(elapsed_ms, 3),
    }


@app.get("/v1/browse-fixtures")
def browse_fixtures(limit: int = 12, exclude: str = "", sport: str = ""):
    """Real fixture IDs to render as clickable browse cards -- pulled from
    the actual popularity artifact, never invented placeholder IDs. `exclude`
    is a comma-separated list of fixture IDs (without the FIXTURE: prefix)
    already shown/seeded, so the browse list doesn't repeat them. `sport`,
    when given, is one of the raw codes from /v1/sports (e.g. "kosarka")
    and restricts the list to that sport only -- the per-sport page's
    fixture list, not a client-side filter of an unrelated set."""
    exclude_set = {f"FIXTURE:{x}" for x in exclude.split(",") if x}
    candidates = _popularity_df.index
    if sport:
        candidates = [idx for idx in candidates if _sport_map.get(idx) == sport]
    fixtures = [idx for idx in candidates if idx not in exclude_set][:limit]
    return {"fixtures": [f.replace("FIXTURE:", "") for f in fixtures]}


@app.get("/v1/sports")
def list_sports():
    """Real distinct sports present in the raw data (sport_segmentation.py),
    with how many distinct fixtures each has -- drives the nav tabs, not a
    hardcoded sport list. Sorted by volume, most-covered sport first."""
    counts = Counter(_sport_map.values())
    return {
        "sports": [
            {"code": code, "label": SPORT_LABELS.get(code, code.title()), "fixture_count": n}
            for code, n in counts.most_common()
        ]
    }


@app.get("/v1/fixture-sport-map")
def fixture_sport_map():
    """The full fixture -> sport lookup (raw codes), served once so the
    frontend can label/filter picks locally without a round trip per
    fixture. ~650KB -- small enough to fetch once at login and cache in a
    JS Map, same pattern already used for the illustrative name lookup."""
    return {f.replace("FIXTURE:", ""): sport for f, sport in _sport_map.items()}


@app.get("/v1/sport-name-pools")
def sport_name_pools():
    """Real per-sport match-name pools (build_sport_name_pools.py), sourced
    from SB_Player.csv (real settled bets) and EPS_Offers.csv (real market
    offers) -- NOT an exact fixture_id -> name join (verified infeasible:
    no shared key between our fixture_id and either source file, and a
    single player places bets on 50-100+ distinct fixtures per day so
    (PlayerID, date) doesn't disambiguate to one fixture). Every name in
    every pool is a real match/fixture name that existed in the real data
    for that real sport; the frontend deterministically assigns one to
    each fixture_id, same as before, just no longer drawing every sport
    from the same football-club pool."""
    return _sport_name_pools


@app.get("/v1/recommend/sport/{session_key}/{sport_code}")
def recommend_for_sport(session_key: str, sport_code: str, top_n: int = 3):
    """On-demand (not the O(1) cached path) -- this answers "what would you
    recommend if I explicitly stayed within THIS sport", which the write-time
    cache (one recommendation per session, sport inferred from history) was
    never designed to answer. Cheap enough to compute per request: the
    underlying candidate generation runs in low-single-digit milliseconds
    (measured in the same session_store cache path), so this is still
    effectively instant for a live demo, just not the same O(1) guarantee
    documented for the whole-session cache."""
    recent_nodes = session_store.get_recent_nodes(session_key)
    protection_state = session_store.get_protection_state(session_key)
    t0 = time.perf_counter()
    result = rank.recommend(
        recent_nodes, _fixture_adjacency, _popularity_df, protection_state,
        top_n=top_n, sport_map=_sport_map, forced_sport=sport_code,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return {**result, "computed_on_demand_in_ms": round(elapsed_ms, 3)}


def _top_edges(fixture_node: str, k: int) -> list[dict]:
    edges = _fixture_adjacency.get(fixture_node, [])
    return sorted(edges, key=lambda e: (-e["count"], e["next_node"]))[:k]


def _display_id(node: str) -> str:
    # Fixture nodes show their bare fixture_id (matches what the main app
    # displays); interaction nodes keep their ACTION:/SCREEN: prefix, which
    # is already a short, readable label and doubles as a visible node-type
    # marker in the 3D view.
    return node[len("FIXTURE:"):] if node.startswith("FIXTURE:") else node


def _node_kind(node: str) -> str:
    if node.startswith("FIXTURE:"):
        return "fixture"
    if node.startswith("ACTION:"):
        return "action"
    if node.startswith("SCREEN:"):
        return "screen"
    return "other"


@app.get("/v1/graph/session/{session_key}")
def graph_session(session_key: str, neighbor_limit: int = 6):
    """A small, bounded subgraph for the 3D graph explorer's "live session"
    mode -- NOT the full 16,692-fixture graph (that would be an unusable
    hairball and slow to render).

    Nodes are BOTH real node types from the graph schema, not fixtures
    only: every interaction node (SCREEN:/ACTION:) and content/fixture node
    (FIXTURE:) this session has actually touched, in real order ("seen" /
    "interaction"), plus the fixtures currently recommended next
    ("recommended"), plus each seen fixture's top co-occurrence neighbors
    ("neighbor", for context on WHY a recommendation was made). An earlier
    version of this endpoint filtered to FIXTURE: nodes before building the
    walk, which silently dropped every interaction node from the live
    visualization even though the graph has always had two node types.

    Two DIFFERENT kinds of edge, and both are real, not fabricated:
    - "session_path": this exact session's own real walk (interaction AND
      fixture nodes together), connected in the real order they happened.
      This can't come from the precomputed fixture_adjacency artifact --
      that was built before this live session existed -- so it's derived
      directly from the session's own event order instead.
    - "cooccurrence": the real historical fixture-to-fixture edges from
      fixture_adjacency.json, i.e. why the system is considering a given
      neighbor/recommendation at all."""
    recent_nodes = session_store.get_recent_nodes(session_key)
    # Order-preserving distinct WALK -- every real node this session
    # touched, interaction and content alike, in the real order they
    # happened (not just the fixture-only set rank.viewed_fixtures() returns).
    walk_ordered: list[str] = []
    walk_set: set[str] = set()
    for n in recent_nodes:
        if n not in walk_set:
            walk_set.add(n)
            walk_ordered.append(n)

    seen_fixtures = {n for n in walk_ordered if n.startswith("FIXTURE:")}

    cached = session_store.get_cached_recommendation(session_key) or {}
    recommended = {c["fixture"] for c in cached.get("candidates", [])}

    node_roles: dict[str, str] = {}
    for n in walk_ordered:
        node_roles[n] = "seen" if n.startswith("FIXTURE:") else "interaction"
    for f in recommended:
        node_roles.setdefault(f, "recommended")

    edges: list[dict] = []
    for a, b in zip(walk_ordered, walk_ordered[1:]):
        edges.append({"source": a, "target": b, "weight": 1, "kind": "session_path"})
    for f in seen_fixtures:
        for edge in _top_edges(f, neighbor_limit):
            neighbor = edge["next_node"]
            node_roles.setdefault(neighbor, "neighbor")
            edges.append({"source": f, "target": neighbor, "weight": edge["count"], "kind": "cooccurrence"})

    nodes_out = [
        {
            "id": _display_id(node),
            "role": role,
            "kind": _node_kind(node),
            "sport": _sport_map.get(node),
            "sport_label": SPORT_LABELS.get(_sport_map.get(node, ""), _sport_map.get(node)),
        }
        for node, role in node_roles.items()
    ]
    edges_out = [
        {
            "source": _display_id(e["source"]),
            "target": _display_id(e["target"]),
            "weight": e["weight"],
            "kind": e["kind"],
        }
        for e in edges
    ]
    return {
        "session_key": session_key,
        "distinct_fixtures_seen": len(seen_fixtures),
        "recommendation_reason": cached.get("reason"),
        "nodes": nodes_out,
        "edges": edges_out,
    }


@app.get("/v1/graph/search")
def graph_search(q: str = "", limit: int = 20, neighbor_limit: int = 5):
    """Search across the FULL real graph (16,692 fixtures) by fixture-id
    substring or sport name, bounded to a legible ego-graph rather than
    trying to render the whole thing at once: matched fixtures plus each
    one's top co-occurrence neighbors."""
    q_norm = q.strip().lower()
    if not q_norm:
        return {"query": q, "matched_count": 0, "nodes": [], "edges": []}

    matched = [
        node for node in _sport_map
        if q_norm in node.replace("FIXTURE:", "").lower() or q_norm in (_sport_map.get(node) or "")
    ]
    # Rank matches by real popularity (bet_intent_count) where known, so the
    # capped `limit` keeps the most-relevant matches, not an arbitrary slice.
    pop_rank = {idx: i for i, idx in enumerate(_popularity_df.index)}
    matched.sort(key=lambda n: pop_rank.get(n, len(pop_rank)))
    matched = matched[:limit]

    node_roles: dict[str, str] = {m: "match" for m in matched}
    edges: list[dict] = []
    for m in matched:
        for edge in _top_edges(m, neighbor_limit):
            neighbor = edge["next_node"]
            node_roles.setdefault(neighbor, "neighbor")
            edges.append({"source": m, "target": neighbor, "weight": edge["count"], "kind": "cooccurrence"})

    nodes_out = [
        {
            "id": node.replace("FIXTURE:", ""),
            "role": role,
            "sport": _sport_map.get(node),
            "sport_label": SPORT_LABELS.get(_sport_map.get(node, ""), _sport_map.get(node)),
        }
        for node, role in node_roles.items()
    ]
    edges_out = [
        {
            "source": e["source"].replace("FIXTURE:", ""),
            "target": e["target"].replace("FIXTURE:", ""),
            "weight": e["weight"],
            "kind": e["kind"],
        }
        for e in edges
    ]
    return {"query": q, "matched_count": len(matched), "nodes": nodes_out, "edges": edges_out}


@app.get("/v1/demo-users")
def demo_users():
    """The 10 real, held-out, verified sessions from
    docs/design-final-sports-graph.md's held-out evaluation — source of
    truth for the login picker, not invented demo accounts."""
    return {"users": _demo_users}


@app.post("/v1/login-verified")
def login_verified(req: VerifiedLoginRequest):
    """Seed a fresh demo session key with ONE of the 10 verified real
    sessions' own real, chronologically-ordered fixture history — scoped to
    the exact (session, PlayerID) pair, not the player's entire history
    (two of the 10 share a PlayerID across two different real sessions)."""
    session_rows = _raw_events_by_session_player.get(req.session_key)
    if session_rows is None:
        raise HTTPException(404, f"Unknown verified session_key={req.session_key}")

    new_session_key = f"verified-{uuid.uuid4().hex[:8]}::{req.session_key.split('::')[1]}"
    session_store.ensure_session(new_session_key, protection_state="allowed")

    seeded_fixtures: list[str] = []
    seed_nodes: list[str] = []
    for _, row in session_rows.sort_values("_ts").iterrows():
        fid = row["fixture_id"]
        if fid and fid not in seeded_fixtures:
            seeded_fixtures.append(fid)
            seed_nodes.append("SCREEN:prematchDetail")
            seed_nodes.append(f"FIXTURE:{fid}")
        if len(seeded_fixtures) >= req.seed_count:
            break

    if seed_nodes:
        session_store.add_nodes(new_session_key, seed_nodes, protection_state="allowed")

    _, elapsed_ms = _compute_and_cache_recommendation(new_session_key)

    return {
        "session_key": new_session_key,
        "source_session_key": req.session_key,
        "seeded_real_fixtures": seeded_fixtures,
        "recommendation_computed_and_cached_in_ms": round(elapsed_ms, 3),
    }


@app.get("/v1/sessions/{session_key}")
def get_session(session_key: str):
    nodes_list = session_store.get_recent_nodes(session_key)
    return {
        "session_key": session_key,
        "node_count": len(nodes_list),
        "distinct_fixtures_seen": len(rank.viewed_fixtures(nodes_list)),
        "recent_nodes": nodes_list[-20:],  # tail only — this is a debug view, not a full dump
    }


@app.get("/v1/health")
def health():
    return {
        "status": "ok",
        "active_sessions": session_store.active_session_count(),
        "fixture_adjacency_nodes_loaded": len(_fixture_adjacency),
        "sport_map_entries_loaded": len(_sport_map),
    }


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
