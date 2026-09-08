"""Phase E: replay a real historical session against the LIVE running API
(not an in-process function call — this is what actually gets demoed).

Paces playback using the session's real inter-event timestamps, scaled down
so a demo is watchable — a session can span up to 16 hours in the real
data (§3.6 rule 8), which obviously can't be replayed at real speed. This
compresses *how long we wait between showing events*, not *what happened* —
the sequence and content of every event is exactly what the real session
did. That distinction is stated explicitly in the printed output so it's
never ambiguous to someone watching the demo.

Requires the API to already be running (see src/serving/app.py docstring).
"""

from __future__ import annotations

import argparse
import time

import requests

from ..graph_pipeline import clean, nodes

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
MAX_INTER_EVENT_WAIT_SECONDS = 2.0  # demo-watchability cap, not a data change


def pick_session(df, min_fixture_views: int = 5) -> str:
    annotated = nodes.annotate(df)
    is_fixture = annotated["content_node"].notna()
    counts = annotated.loc[is_fixture].groupby("session_key")["content_node"].nunique()
    candidates = counts[counts >= min_fixture_views].index
    if len(candidates) == 0:
        raise ValueError("No session meets the minimum fixture-view threshold")
    return candidates[0]


def replay_session(session_key: str, df, base_url: str, speed_factor: float, protection_state: str):
    session_df = df[df["session_key"] == session_key].sort_values("_ts").reset_index(drop=True)
    print(f"=== Live-replaying session {session_key} ({len(session_df)} real events) against {base_url} ===")
    print(f"(pacing compressed by {speed_factor}x, capped at {MAX_INTER_EVENT_WAIT_SECONDS}s between events -- sequence and content are exactly as recorded, only wait time is compressed)\n")

    prev_ts = None
    distinct_fixtures_seen = 0
    trigger_fired = False

    for _, row in session_df.iterrows():
        if prev_ts is not None:
            real_gap = (row["_ts"] - prev_ts).total_seconds()
            wait = min(real_gap / speed_factor, MAX_INTER_EVENT_WAIT_SECONDS)
            if wait > 0:
                time.sleep(wait)
        prev_ts = row["_ts"]

        resp = requests.post(
            f"{base_url}/v1/events",
            json={
                "session_key": session_key,
                "event_name": row["event_name"],
                "fortuna_screen_name": row["fortuna_screen_name"],
                "platform": row["platform"],
                "added_from": row["added_from"],
                "fixture_id": row["fixture_id"],
                "page_location": row["page_location"],
            },
            timeout=5,
        )
        body = resp.json()
        distinct_fixtures_seen = body["distinct_fixtures_seen"]

        if row["fixture_id"]:
            print(f"[{row['_ts']}] event={row['event_name']} fixture={row['fixture_id']}  (distinct fixtures seen: {distinct_fixtures_seen})")

        if distinct_fixtures_seen >= 3 and not trigger_fired:
            trigger_fired = True
            rec = requests.post(
                f"{base_url}/v1/recommend",
                json={"session_key": session_key, "protection_state": protection_state, "top_n": 3},
                timeout=5,
            ).json()
            print(f"  -> LIVE RECOMMENDATION TRIGGER: {rec['reason']}")
            for c in rec.get("candidates", []):
                print(f"     top pick: {c['fixture']}  score={c['score']}  source={c['source']}")

    future_fixtures = (
        session_df.loc[session_df["fixture_id"] != "", "fixture_id"]
        .drop_duplicates()
        .tolist()[3:]
    )
    print(f"\nFixtures this real session actually went on to view/bet on after the trigger: {future_fixtures}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--speed-factor", type=float, default=100.0)
    parser.add_argument("--protection-state", default="allowed")
    parser.add_argument(
        "--session-key",
        default="1785577599::ead389ee47756074cf06848b059b7ced9e80569c829f29a3ee3813926f920b0c",
        help=(
            "(session,PlayerID) key to replay. Defaults to a confirmed, non-leaked "
            "held-out hit (see docs §Phase C / artifacts/session_proof.xlsx) rather "
            "than auto-picking, since auto-picking has no leakage control -- an "
            "earlier auto-picked example failed a strict leave-one-out check."
        ),
    )
    args = parser.parse_args()

    df, _ = clean.clean()
    replay_session(args.session_key, df, args.base_url, args.speed_factor, args.protection_state)
