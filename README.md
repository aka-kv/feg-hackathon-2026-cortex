# Cortex — Session Intent Graph for PSK Sportsbook

**Team**: Cortex · **Challenge**: FEG Innovation Hackathon 2026, Challenge 1 — *Session Quality & Session-to-Action Conversion* (PSK / Croatia sportsbook track)

A live, clickable prototype that watches a real sportsbook session in real time and, once it's opened a handful of fixtures without betting, recommends what it's most likely to act on next — using nothing but real historical fixture co-occurrence, validated on held-out real data, served in O(1) time.

> If your team name differs from "Cortex", update this line and the repo name (`feg-hackathon-2026-<team-name>`) before final submission.

## 1. Problem statement

A sportsbook session that browses several fixtures without placing a bet is a session with real, measurable intent that currently goes unacted on. Just showing "what's popular" isn't personalization, and manufacturing urgency isn't an acceptable answer either. The question: can a session's own browsing behavior, *during* the session, tell us what it's likely to bet on next — cheaply, transparently, and safely?

## 2. Solution overview & key innovation

After a session opens ~3 distinct fixtures, map every fixture it has opened onto a real, precomputed **fixture co-occurrence graph** (built from real historical session data: which fixtures tend to get viewed together in the same session) and recommend the fixtures most strongly connected to what it's already looked at. Before that threshold, fall back to real popularity data, restricted to whichever sport(s) the session has shown interest in.

**Key innovation is restraint, not complexity**: this is deliberately *not* a trained ML model. We tested whether one was necessary and confirmed it wasn't — plain counting/co-occurrence, held out properly, beats a naive popularity baseline by **~12.1x** (see §Impact below), with full inspectability (every recommendation carries its source and raw score) and zero training/retraining cost.

## 3. Key features / user journey

- **Login** with two real, verified demo accounts (a returning session with real history, and a genuine cold start) — **or** browse everything without logging in at all; only the final bet-placement step (Checkout) requires signing in, and the exact same session/signal carries through the login gate untouched.
- **Home page**: cross-sport top picks, refreshing live as you bet, with an animated reveal the moment the recommendation actually changes.
- **30 real sports**, each with its own page, own real fixture list, and its own sport-scoped recommendation — driven entirely by real data (`GET /v1/sports`), not a hardcoded list.
- **Bet slip as a real cart**: click odds to stage a bet (this is the real signal the recommender uses immediately), remove it with one click, then **Checkout** to actually place it.
- **Standalone 3D graph explorer** (`/graph.html`): watch a real session's fixture graph grow live as you bet, or search the full 16,692-fixture real graph by sport or fixture ID.

## 4. Technology stack

- **Backend**: Python 3.11, FastAPI, uvicorn, pandas, pydantic.
- **Offline pipeline**: pandas-based batch jobs that build the recommendation graph, popularity baseline, sport map, and real per-sport name pools from the raw data.
- **Frontend**: plain HTML/CSS/JavaScript, no build step, no framework — served as static files by the same FastAPI process. `graph.html` additionally loads `3d-force-graph` from a CDN.
- Full breakdown and rationale: `docs/architecture.md`.

## 5. System requirements & prerequisites

- Python 3.11+ (developed and tested on 3.11.5).
- `pip` for installing dependencies (`requirements.txt`).
- A modern Chromium-based browser (for the 3D graph explorer's WebGL requirement).
- **The raw hackathon-provided data files**, obtained separately (never committed to this repo — see `docs/compliance-note.md` §6):
  - `top_sport_users_event_logs.csv` — **required** for the server to start at all (loaded at startup for demo-account seeding).
  - `SB_Player.csv`, `EPS_Offers.csv` — only required if you intend to *regenerate* `artifacts/sport_name_pools.json` yourself; the pre-built artifact is already committed under `artifacts/`, so this is optional for just running the demo.

## 6. Installation / setup steps

1. Clone this repository.
2. Create and activate a virtual environment (recommended):
   ```
   python -m venv .venv
   .venv\Scripts\activate        # Windows
   source .venv/bin/activate     # macOS/Linux
   ```
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Obtain `top_sport_users_event_logs.csv` (and, optionally, `SB_Player.csv`/`EPS_Offers.csv`) from the hackathon's provided data — these are **not** in this repo — and place them in one directory anywhere on your machine.
5. Point the project at that directory: open `src/graph_pipeline/config.py` and update `DATA_DIR` to the path from step 4. This is currently a hardcoded path (see `.env.example` for why, and the note in `docs/architecture.md` §8 about this being a candidate for env-based config in a real deployment).

## 7. Environment variables & configuration

This prototype has **no required secrets, API keys, or credentials** — see `.env.example` for the full, honest statement of current state. The only machine-specific configuration is the raw-data directory path (`DATA_DIR` in `config.py`, step 5 above). The two demo-login credentials used in the live walkthrough are non-sensitive, hardcoded placeholders in `src/serving/app.py` (`_DEMO_ACCOUNTS`) — not a real auth system.

## 8. How to run the prototype

From the **repository root** (the directory this README is in — e.g. `feg-hackathon-2026-cortex/`, NOT the parent directory that holds the raw CSVs):

```
uvicorn src.serving.app:app --port 8000
```

Wait for `Application startup complete` in the console (loading the raw event log takes a few seconds), then open:

- **Main app**: http://127.0.0.1:8000
- **Graph explorer**: http://127.0.0.1:8000/graph.html

Both pages are served as static files by the same process — no separate frontend server, no build step.

## 9. How to test / validate the prototype

- **Automated**: `pytest tests/` from the repository root runs the test suite.
- **Re-run the held-out accuracy evaluation directly** (regenerates the headline number from real data, not a cached claim):
  ```
  python -m src.graph_pipeline.evaluate
  ```
  Expect `system_hit_rate@3: 0.4214`, `popularity_baseline_hit_rate@3: 0.0348`, `cross_sport_violations: 0`.
- **Manual, live verification**: log in as `marko89` / `psk2026`, go to the **Tennis** tab (not Football — the pinned demo fixtures for this account are real tennis fixtures, verified via `docs/mentor-briefing-three-users.md`), and open the 3 pinned fixtures. The recommendation should visibly flip from "Popular right now" to "Recommended for you" the instant the 3rd one is opened.

## 10. Demo instructions / demo flow

See `docs/mentor-briefing-three-users.md` for the full scripted walkthrough (three real scenarios: new visitor, minimal-record returning session, heavy-record returning session, each traced against raw data). Quick version:

1. Log in as `newplayer` / `psk2026` (genuine cold start) — Home shows real popularity picks immediately.
2. Log in as `marko89` / `psk2026`, go to Tennis, open the 3 pinned fixtures — watch the recommendation flip live, with the animated reveal.
3. Open `/graph.html` in a second tab/window, paste in the active session key (shown in the URL bar after step 2, or via `GET /v1/demo-users`), click "Watch live" — watch the real fixture graph grow as you bet in the other tab.
4. Try the guest flow from the login screen ("Continue browsing without signing in") — browse, stage a bet, click Checkout, and confirm the login modal doesn't reset anything.

Screenshots/video: see `demo/` (add `demo/demo-video-link.md` and `demo/screenshots/` before final submission if not already present).

## 11. Known limitations, assumptions & future improvements

- **No true "brand-new visitor" exists in the sample data** (the provided sample is a curated "top users" set, not a representative population) — the cold-start mechanism is demonstrated honestly as a code path, not attached to a fabricated "new user" identity. See `docs/mentor-briefing-three-users.md` Scenario 1.
- **In-memory session store is single-process** — a multi-instance production deployment needs a shared store (e.g. Redis); not built, since the hackathon demo runs one process (`docs/architecture.md` §8).
- **The eligibility/protection gate is a structural stub** — the real host-side eligibility signal doesn't exist in the hackathon sample; production would wire this to FEG's real system, not a fabricated risk score.
- **Illustrative match names are real-per-sport but not exactly matched to the specific fixture** — verified and disclosed as infeasible to join exactly; see `docs/compliance-note.md` §3.
- **Future improvement identified but not yet built** (see conversation/design log): recency/trending-weighted popularity for the cold-start baseline instead of all-time popularity — flagged as a testable next step for improving new-visitor conversion specifically, not yet validated against held-out data.

## 12. Links to supporting documentation

- Architecture & technical overview: [`docs/architecture.md`](docs/architecture.md)
- Impact case & cost-value analysis: [`docs/impact-case.md`](docs/impact-case.md)
- Compliance note: [`docs/compliance-note.md`](docs/compliance-note.md)
- Third-party dependencies & AI-assistance disclosure: [`docs/dependencies.md`](docs/dependencies.md)
- Full build log, every verification step, every bug found and fixed: [`docs/design-final-sports-graph.md`](docs/design-final-sports-graph.md)
- Scripted mentor/judge walkthrough with real, traceable examples: [`docs/mentor-briefing-three-users.md`](docs/mentor-briefing-three-users.md)
