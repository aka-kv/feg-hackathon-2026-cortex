"""Cleaning rules for top_sport_users_event_logs.csv.

Every function here implements one numbered rule from
docs/design-final-sports-graph.md §3.6. Do not add a cleaning step that
isn't backed by a verification-log entry in that document — if you find a
new issue, verify it, log it in §10, then add the rule here.
"""

from __future__ import annotations

import pandas as pd

from . import config


def load_raw(path=config.EVENT_LOG_PATH) -> pd.DataFrame:
    """Read the event log with empty-string nulls, never NaN — the design
    doc's audit queries all assumed this convention, so downstream code must
    match it."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def drop_casino_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """§3.6 rules 1 & 7: casino contamination is not just event_name ==
    'casino_game_launch' (552 rows) — 7,351 additional page_view rows point
    to casino.psk.hr via page_location. Total: 7,903 rows dropped."""
    is_launch = df["event_name"] == config.CASINO_EVENT_NAME
    is_casino_page = df["page_location"].str.contains(
        config.CASINO_DOMAIN_MARKER, case=False, na=False
    )
    casino_mask = is_launch | is_casino_page
    dropped = int(casino_mask.sum())
    return df.loc[~casino_mask].copy(), dropped


def parse_timestamps(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """§3.6 rule 5: two-pass parse. The default parser misses ~78 rows with
    a whole-second (no microsecond) format; an explicit fallback format
    recovers 100% of them — verified, nothing actually needs quarantining.
    Only rows that fail *both* passes get quarantined (count, don't drop
    silently)."""
    primary = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    still_bad = primary.isna()
    if still_bad.any():
        fallback = pd.to_datetime(
            df.loc[still_bad, "timestamp"],
            format="%Y-%m-%d %H:%M:%S UTC",
            errors="coerce",
            utc=True,
        )
        primary.loc[still_bad] = fallback

    df = df.copy()
    df["_ts"] = primary
    unparseable = int(df["_ts"].isna().sum())
    if unparseable:
        # Quarantine, don't drop: keep them out of the graph build but make
        # the count visible to whoever runs this.
        df = df.loc[df["_ts"].notna()].copy()
    return df, unparseable


def add_session_key(df: pd.DataFrame) -> pd.DataFrame:
    """§3.6 rule 1: `session` alone collides across different PlayerIDs
    (0.42% of sessions, confirmed genuinely time-interleaved, not sequential
    reuse — session looks like a second-granularity epoch timestamp). The
    true session key is the (session, PlayerID) pair."""
    df = df.copy()
    df["session_key"] = df["session"] + "::" + df["PlayerID"]
    return df


def clean(path=config.EVENT_LOG_PATH) -> tuple[pd.DataFrame, dict]:
    """Run the full cleaning pipeline. Returns the cleaned frame plus a
    stats dict — always inspect the stats, don't just trust silence."""
    stats = {}
    df = load_raw(path)
    stats["rows_raw"] = len(df)

    df, casino_dropped = drop_casino_rows(df)
    stats["casino_rows_dropped"] = casino_dropped

    df, unparseable = parse_timestamps(df)
    stats["timestamp_unparseable_quarantined"] = unparseable

    df = add_session_key(df)
    df = df.sort_values(["session_key", "_ts"], kind="stable").reset_index(drop=True)

    stats["rows_clean"] = len(df)
    stats["distinct_session_keys"] = df["session_key"].nunique()
    return df, stats


if __name__ == "__main__":
    _, stats = clean()
    for k, v in stats.items():
        print(f"{k}: {v}")
