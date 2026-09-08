"""Phase C: cold-start popularity priors.

Design correction made while building this (see docs §5 and the Phase C
entry in the build tracker): the design doc originally named `SB_MOM.csv`
as "the cold-start answer." That's only half right. `SB_MOM.csv` is keyed
by human-readable fixture *name*, not `fixture_id` — the exact same
ID-to-name bridge gap already flagged for `EPS_Offers` (§3.4) applies here
too, and it isn't solved in-sample. It cannot directly rank the graph's
`FIXTURE:ufo:mtch:...` nodes.

The graph's own node-visit counts (Phase B output, already computed, no
join needed) are the honest, available fixture-level popularity source.
`SB_MOM.csv` is demoted to a coarser, sport-level sanity signal — useful as
a fallback-of-the-fallback and as a check that the sample isn't wildly
unrepresentative, not as the primary cold-start ranking.
"""

from __future__ import annotations

import pandas as pd

from . import config

# Croatian (event log) -> English (SB_MOM) sport-name translation. Small and
# finite, unlike the EPS_Offers fixture-name join, which is why this one is
# actually solvable — built from the distinct values seen in both files
# during the data audit, not guessed.
SPORT_NAME_HR_TO_EN = {
    "nogomet": "Football",
    "tenis": "Tennis",
    "košarka": "Basketball",
    "enogomet": "eFootball",
    "ekošarka": "eBasketball",
    "rukomet": "Handball",
    "stolni tenis": "Table tennis",
    "hokej": "Ice Hockey",
    "ehokej": "eIce hockey",
    "odbojka": "Volleyball",
    "borilački sportovi": "Martial Arts",
    "futsal": "Futsal",
    "pikado": "Darts",
    "esport counter strike": "Esport Counter Strike",
}


def fixture_popularity_from_graph(steps: pd.DataFrame, df_annotated: pd.DataFrame) -> pd.DataFrame:
    """The real, available cold-start prior: rank FIXTURE nodes by two cuts
    — total engagement (viewed or bet on) and bet-intent specifically
    (appeared as the content_node of an ACTION event). Bet-intent is the
    more relevant ranking for "what's likely to be bet on next."""
    engagement = (
        steps[steps["node"].str.startswith("FIXTURE:")]
        .groupby("node")
        .size()
        .rename("engagement_count")
    )

    is_action = df_annotated["event_name"].isin(config.ACTION_EVENTS)
    bet_intent = (
        df_annotated.loc[is_action & df_annotated["content_node"].notna()]
        .groupby("content_node")
        .size()
        .rename("bet_intent_count")
    )

    pop = pd.concat([engagement, bet_intent], axis=1).fillna(0)
    pop["bet_intent_count"] = pop["bet_intent_count"].astype(int)
    pop["engagement_count"] = pop["engagement_count"].astype(int)
    # Sort by fixture ID first (so ties land in a known order), then a
    # STABLE sort by bet_intent_count preserves that ordering within each
    # tied score group. pandas' default sort algorithm (quicksort) is not
    # stable -- relying on it silently would make tie order depend on
    # implementation details rather than being a deliberate, documented
    # choice. rank.py's popularity_candidates() trusts this ordering rather
    # than re-sorting per request, so it has to actually hold here.
    return pop.sort_index().sort_values("bet_intent_count", ascending=False, kind="mergesort")


def sport_popularity_from_sbmom(path=config.SB_MOM_PATH) -> pd.Series:
    """Coarse, sport-level cross-check — not fixture-level, and named per
    the translation table above, not joined on fixture_id (that bridge
    doesn't exist in-sample). Weighted by ticket count."""
    mom = pd.read_csv(path, dtype=str, keep_default_na=False)
    mom["no_of_tickets"] = pd.to_numeric(mom["no_of_tickets"], errors="coerce").fillna(0)
    return mom.groupby("Sport_name_english")["no_of_tickets"].sum().sort_values(ascending=False)


def cross_check_sample_representativeness(df_annotated: pd.DataFrame, sport_pop_en: pd.Series) -> pd.DataFrame:
    """Sanity check only: is our small sample's sport mix directionally
    consistent with the full population's, once names are translated? Not
    used for ranking — just to catch if the sample is wildly skewed."""
    sample_sport = (
        df_annotated["sport_name"]
        .str.strip()
        .str.lower()
        .map(SPORT_NAME_HR_TO_EN)
        .dropna()
        .value_counts()
    )
    combined = pd.concat(
        {"sample_event_count": sample_sport, "population_ticket_count": sport_pop_en}, axis=1
    ).dropna()
    combined["sample_rank"] = combined["sample_event_count"].rank(ascending=False)
    combined["population_rank"] = combined["population_ticket_count"].rank(ascending=False)
    return combined.sort_values("population_rank")
