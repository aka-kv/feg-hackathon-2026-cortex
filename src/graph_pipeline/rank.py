"""Phase C/D: candidate generation + cold-start blending + the compliance
gate, wired as the mentor's actual scenario — after a session has viewed a
few fixtures, surface one it hasn't seen yet as a "top picks" candidate.

Every recommendation returned by this module carries its source (graph vs.
cold-start popularity) and the raw evidence behind it — this is the
"inspectable, not a black box" requirement from docs §6/§7, enforced in
code, not just asserted in prose.

Hardening pass: every ranking step below has an explicit, deterministic
tie-break. Without one, iterating a Python `set` (hash-order dependent,
varies across process restarts under hash randomization) or relying on
pandas' default non-stable sort means two equally-scored fixtures could
silently swap which one wins between runs — undesirable for something
claimed to be reproducible. Fixed by sorting inputs before iterating and
using (score desc, fixture_id asc) as the sort key everywhere a tie is
possible, so the same input always produces the same output, full stop.
"""

from __future__ import annotations

MIN_FIXTURES_FOR_GRAPH_SIGNAL = 3  # mentor's own example threshold


def eligibility_gate(protection_state: str) -> bool:
    """Unconditional first check, per docs §7 — this must run before any
    candidate is generated, not filter results after the fact.

    STUB: the hackathon sample has no real self-exclusion/eligibility feed.
    In production this consumes the host's authoritative protection signal
    (blocked / self_excluded / unknown -> False; allowed -> True). Wiring a
    fake "risk score" here would be exactly the kind of unverifiable claim
    docs §7/§9 already rules out — leave this as an explicit integration
    point, not a guess.
    """
    return protection_state == "allowed"


def viewed_fixtures(recent_nodes: list[str]) -> set[str]:
    return {n for n in recent_nodes if n.startswith("FIXTURE:")}


def _relevant_sports(seen_fixtures: set[str], sport_map: dict | None) -> set[str] | None:
    """None means "no restriction" (no sport_map supplied, or none of the
    seen fixtures have a known sport — fall back open rather than return
    zero candidates)."""
    if not sport_map:
        return None
    sports = {sport_map[f] for f in seen_fixtures if f in sport_map}
    return sports or None


def graph_candidates(
    seen_fixtures: set[str],
    fixture_adjacency_index: dict,
    exclude: set[str],
    top_n: int,
    sport_map: dict | None = None,
    relevant_sports: set[str] | None = None,
) -> list[dict]:
    """Aggregate fixture-co-occurrence candidates across every fixture the
    session has already viewed — not a 1-hop interaction-node lookup.
    Direct FIXTURE->FIXTURE edges in the raw event graph are almost
    nonexistent (there's almost always a screen/action in between two
    fixture views), and aggregating through common interaction nodes just
    regresses to overall popularity, not session-specific signal —
    confirmed by replaying a real session and finding zero match against
    global-popularity-dominated candidates. See cooccurrence.py.

    relevant_sports, when supplied by the caller (recommend(), below),
    restricts candidates to that sport set — either inferred from the
    session's own seen fixtures, or an explicit user-chosen sport when
    forced_sport is set. None means no restriction. Tested against the
    held-out evaluation: sport-restriction eliminated 100% of cross-sport
    recommendations (a popular football match being recommended to a tennis
    session) at no cost to hit rate, and sharply reduced how often any
    single fixture dominates (top-1 share of all rank-1 picks: 6.8% ->
    2.2%)."""
    if top_n <= 0 or not fixture_adjacency_index:
        return []

    scores: dict[str, int] = {}
    # Sorted, not raw set iteration -- a Python set's iteration order for
    # strings depends on hash randomization and can vary across process
    # restarts. Doesn't change the final ranking (scores still sum the
    # same regardless of visit order) but keeps this loop itself
    # deterministic and easy to reason about/debug.
    for fixture in sorted(seen_fixtures):
        for edge in fixture_adjacency_index.get(fixture, []):
            next_node = edge["next_node"]
            if next_node in exclude:
                continue
            if relevant_sports is not None and sport_map.get(next_node) not in relevant_sports:
                continue
            scores[next_node] = scores.get(next_node, 0) + edge["count"]

    # Explicit tie-break: score descending, then fixture_id ascending.
    # Without the second key, two fixtures tied on score could swap order
    # between runs depending on dict insertion order.
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    return [
        {
            "fixture": node,
            "score": score,
            "source": "fixture_cooccurrence",
            # Display-only metadata for multi-sport UIs -- never affects
            # scoring or the exclude/relevant_sports filtering above, which
            # already happened before this dict is built.
            "sport": (sport_map or {}).get(node),
        }
        for node, score in ranked
    ]


def popularity_candidates(
    popularity_df,
    exclude: set[str],
    top_n: int,
    relevant_sports: set[str] | None = None,
    sport_map: dict | None = None,
) -> list[dict]:
    """popularity_df is expected pre-sorted by bet_intent_count descending
    with a deterministic tie-break already applied at construction time
    (see popularity.py) — this function trusts that ordering rather than
    re-sorting per call, since re-sorting on every recommendation request
    would be wasted, repeated work for a value that never changes between
    requests against the same artifact.

    Take a small head slice before filtering, not a full `.iterrows()` scan
    of the whole population table — that scan is exactly the kind of thing
    that's cheap once in a notebook and a real production bug at
    serving-request volume.

    If sport-restricted and the head slice doesn't yield enough matches
    (rare — a niche sport with few popular fixtures), widen the slice
    rather than silently return fewer than top_n candidates."""
    if top_n <= 0 or popularity_df is None or len(popularity_df) == 0:
        return []

    limit = top_n + len(exclude) + 10
    out: list[dict] = []
    while True:
        head = popularity_df.head(limit)
        out = []
        for node, row in head.iterrows():
            if node in exclude:
                continue
            if relevant_sports is not None and sport_map.get(node) not in relevant_sports:
                continue
            out.append({
                "fixture": node,
                "score": int(row.bet_intent_count),
                "source": "cold_start_popularity",
                "sport": (sport_map or {}).get(node),
            })
            if len(out) >= top_n:
                break
        if len(out) >= top_n or limit >= len(popularity_df):
            break
        limit = min(limit * 4, len(popularity_df))
    return out


def recommend(
    recent_nodes: list[str],
    fixture_adjacency_index: dict,
    popularity_df,
    protection_state: str,
    top_n: int = 3,
    sport_map: dict | None = None,
    forced_sport: str | None = None,
) -> dict:
    """The single entry point the serving layer calls. Returns a dict with
    an explicit `allowed` flag and, if allowed, a ranked candidate list with
    each candidate's source — this is what the reasoning-trace / "why am I
    seeing this" surface reads from directly.

    forced_sport: when a user has explicitly navigated to one sport's page
    (e.g. "Basketball"), restrict candidates to that sport regardless of
    which sport(s) the session has actually engaged with so far -- an
    explicit user choice of context, not an inferred one. None (the
    default) preserves the original behaviour: infer relevant sports from
    the session's own seen fixtures. The cold-start-vs-graph THRESHOLD
    itself is still based on total fixtures seen across all sports, not
    just the forced one -- forcing a sport only narrows WHICH candidates
    are eligible, it doesn't change when personalisation kicks in."""
    if not eligibility_gate(protection_state):
        return {"allowed": False, "reason": "protection_gate", "candidates": []}

    if top_n <= 0:
        return {"allowed": True, "reason": "top_n<=0 requested", "candidates": []}

    seen = viewed_fixtures(recent_nodes)
    relevant_sports = {forced_sport} if forced_sport else _relevant_sports(seen, sport_map)

    if len(seen) < MIN_FIXTURES_FOR_GRAPH_SIGNAL:
        candidates = popularity_candidates(popularity_df, seen, top_n, relevant_sports, sport_map)
        basis = f"cold_start (fewer than {MIN_FIXTURES_FOR_GRAPH_SIGNAL} fixtures viewed this session)"
    else:
        candidates = graph_candidates(seen, fixture_adjacency_index, seen, top_n, sport_map, relevant_sports)
        if len(candidates) < top_n:
            already = seen | {c["fixture"] for c in candidates}
            candidates += popularity_candidates(popularity_df, already, top_n - len(candidates), relevant_sports, sport_map)
        basis = f"fixture_cooccurrence ({len(seen)} fixtures viewed this session)"

    # Belt-and-suspenders: the exclude-set logic above already guarantees
    # no duplicates and no already-seen fixture can appear, but assert it
    # explicitly rather than trust that silently -- a future edit to either
    # candidate-generation path that broke this invariant should fail loudly
    # here, not surface as a confusing "same match recommended twice" bug
    # discovered by a user clicking through the demo.
    fixtures_returned = [c["fixture"] for c in candidates]
    assert len(fixtures_returned) == len(set(fixtures_returned)), "duplicate candidate fixture returned"
    assert not (set(fixtures_returned) & seen), "recommended a fixture the session already opened"

    return {"allowed": True, "reason": basis, "candidates": candidates}
