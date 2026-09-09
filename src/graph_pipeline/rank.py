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

# Stage 1 (retrieval) pulls this many candidates -- more than we ever show
# -- so stage 2 (reranking, below) has real material to work with instead
# of reranking an already-truncated top-3. Tuned empirically against
# evaluate.py's held-out harness alongside RERANK_SHRINKAGE_K; see rank.py's
# module docstring / design-final-sports-graph.md for the sweep results.
RETRIEVAL_POOL_SIZE = 15

# How much weight the reranking stage's popularity-smoothing prior gets,
# on a 0..1 scale (0 = pure stage-1 co-occurrence ranking; 1 = pure
# popularity ranking). Swept against the held-out evaluation before
# shipping, not guessed -- and the honest result was that ANY nonzero
# weight regresses accuracy sharply and immediately (hit@3 0.4214 -> 0.3711
# at just k=0.01, continuing to fall as k increases further; see
# design-final-sports-graph.md's verification log for the full sweep).
# Same root cause as the earlier, separately-rejected PMI/lift experiment:
# co-occurrence counts are sparse enough that the argmax is often decisive,
# and nudging it with ANY popularity weight costs real hits far more often
# than it fixes ties. Default is therefore 0 -- the reranking STAGE is real,
# tested, and available (see rerank_candidates()), but this particular
# signal is shipped disabled because it measurably makes things worse, not
# because it wasn't tried.
RERANK_SHRINKAGE_K = 0.0


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


def _popularity_lookup(popularity_df, fixture: str) -> int:
    if popularity_df is None or fixture not in popularity_df.index:
        return 0
    return int(popularity_df.at[fixture, "bet_intent_count"])


def rerank_candidates(
    candidates: list[dict],
    popularity_df,
    shrinkage_k: float = RERANK_SHRINKAGE_K,
) -> list[dict]:
    """Stage 2: reranking. graph_candidates()/popularity_candidates() are
    STAGE 1 (retrieval) -- they pull a wider pool than we show, ranked
    purely by their own single raw signal. This stage blends in a SECOND,
    independent signal (global popularity, as a smoothing prior) before the
    final cut to top_n.

    Why: co-occurrence evidence is sparse (median ~2 observations per
    fixture pair -- see sport_segmentation.py's docstring), so when two or
    more retrieved candidates are close in raw score, that's often noise,
    not a real preference signal. A small popularity-aware nudge makes the
    final ranking more robust to that sparsity without letting popularity
    override a clear stage-1 leader.

    Both signals are RANK-NORMALIZED to [0, 1] within this candidate pool
    before blending (not used at their raw magnitude) -- co-occurrence
    scores are small integers while popularity counts can be orders of
    magnitude larger, so blending raw values would let popularity swamp the
    primary signal entirely. This is deliberately NOT the earlier, REJECTED
    PMI/lift experiment (see design-final-sports-graph.md): that approach
    DIVIDED a candidate's score by its own popularity, which punished
    exactly the candidates with the strongest raw evidence, and it measurably
    hurt accuracy (0.4162 -> 0.2642). This approach only ADDS a bounded,
    rank-normalized prior -- at shrinkage_k=0 it is mathematically identical
    to the original, already-validated ranking.

    shrinkage_k was chosen by sweeping candidate values against evaluate.py's
    held-out harness, not guessed -- see that module's docstring for the
    sweep result this value came from."""
    if len(candidates) <= 1 or shrinkage_k <= 0:
        return candidates

    max_score = max(c["score"] for c in candidates) or 1
    pops = [_popularity_lookup(popularity_df, c["fixture"]) for c in candidates]
    max_pop = max(pops) or 1

    reranked = []
    for c, pop in zip(candidates, pops):
        score_norm = c["score"] / max_score
        pop_norm = pop / max_pop
        composite = (1 - shrinkage_k) * score_norm + shrinkage_k * pop_norm
        reranked.append({**c, "composite_score": round(composite, 6)})

    # Same deterministic tie-break rule as stage 1: score desc, fixture_id asc.
    reranked.sort(key=lambda c: (-c["composite_score"], c["fixture"]))
    return reranked


def recommend(
    recent_nodes: list[str],
    fixture_adjacency_index: dict,
    popularity_df,
    protection_state: str,
    top_n: int = 3,
    sport_map: dict | None = None,
    forced_sport: str | None = None,
    rerank_shrinkage_k: float = RERANK_SHRINKAGE_K,
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
        # Stage 1 (retrieval): pull a wider pool than top_n so stage 2 has
        # real material to rerank, rather than reranking an already-cut
        # top-3 (which would just relabel the existing ranking).
        pool_size = max(top_n, RETRIEVAL_POOL_SIZE)
        pool = graph_candidates(seen, fixture_adjacency_index, seen, pool_size, sport_map, relevant_sports)
        if len(pool) < pool_size:
            already = seen | {c["fixture"] for c in pool}
            pool += popularity_candidates(popularity_df, already, pool_size - len(pool), relevant_sports, sport_map)
        # Stage 2 (reranking): blend in the popularity smoothing prior, then
        # cut to top_n. See rerank_candidates()'s docstring for why.
        candidates = rerank_candidates(pool, popularity_df, rerank_shrinkage_k)[:top_n]
        basis = f"fixture_cooccurrence+rerank ({len(seen)} fixtures viewed this session)"

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
