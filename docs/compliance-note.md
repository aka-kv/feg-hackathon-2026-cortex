# Cortex — Compliance Note (D4)

FEG Innovation Hackathon 2026 · Challenge 1: Session Quality & Session-to-Action Conversion

Current, non-superseded version — see `architecture.md` for why this replaces the original casino-era draft.

## 1. Data minimization

The live serving system holds **no persistent user profile**. `src/serving/session_store.py`'s `SessionStore` is entirely in-memory, TTL'd at 30 minutes, and stores nothing beyond: the list of graph nodes (screens/fixtures/actions) touched this session, and the last computed recommendation. Nothing is written to disk by the live system; nothing survives past the TTL, even in memory. This is a deliberate architectural choice, not an incidental one — see `architecture.md` §5.

## 2. Eligibility / protection gate — structural, not a filter

`rank.eligibility_gate(protection_state)` is the unconditional first check in `recommend()` — if a session isn't in an "allowed" state, **no candidate is generated at all**, before any ranking logic runs. This is a fail-closed structural block, not a post-hoc filter applied to an already-computed list. The hackathon sample has no real self-exclusion/eligibility feed, so `protection_state` is a stubbed field; production would wire this to FEG's authoritative host-side eligibility signal. We deliberately did not fabricate a fake "risk score" here — an unverifiable synthetic signal would be exactly the kind of unsupported claim this note exists to rule out.

## 3. Illustrative content, clearly disclosed

- **Match names are illustrative, not the fixture's real name**, and this is disclosed in the frontend's footer on every page ("Match names shown are illustrative"). Names are drawn from real per-sport match-name pools built from `SB_Player.csv` (real settled bets) and `EPS_Offers.csv` (real market offers) — every individual name is a genuine match/player/team name that existed in the real data for that real sport — but assignment to a specific `fixture_id` is deterministic, not a verified 1:1 identity.
- **We checked whether an exact fixture_id → real-name join was possible and confirmed it is not**, rather than assuming either way: `SB_Player.csv` has no `fixture_id` column, no bet ID, and only a placed-*date* (not a precise timestamp); a single player places bets on 50–100+ distinct fixtures on a given day, so `(PlayerID, date)` does not disambiguate to one fixture. Forcing that join would silently produce wrong name assignments most of the time — worse than an honestly-labeled illustrative name.
- **Odds shown are synthetic** (deterministically derived from the fixture ID for visual variety), not real market odds.

## 4. Fairness — sport segmentation

Verified before shipping, not assumed: an unsegmented recommendation graph surfaced one popular football match as the #1 pick for 53 held-out sessions, 42 of which weren't watching football at all. Fixed by restricting candidates to the sport(s) a session has actually engaged with. Measured result: **zero cross-sport violations** in the held-out evaluation (down from 53), at no cost to accuracy (0.4214 hit rate, unchanged), and reduced concentration (top-1 fixture's share of all rank-1 recommendations: 6.8% → 2.2%). Full writeup in `architecture.md` §4 and `design-final-sports-graph.md`.

## 5. No dark patterns

- No manufactured urgency, no countdown pressure, no autoplay.
- The live-graph explorer's "session path" edges and the Home page's animated pick refresh are informational (showing the mechanism working), not styled as urgency cues.
- A persistent, small, non-dismissible disclosure footer is present on every page of the live demo: *"Prototype demo · No real money is used · Match names shown are illustrative."*
- The guest-browsing checkout gate exists for a legitimate reason (real bet placement requires an identified account) — it is not used to create artificial friction or pressure toward signing up.

## 6. Real (provided) data — handling boundaries

- **Raw provided data files are never committed to this repository.** `src/graph_pipeline/config.py`'s `DATA_DIR` points outside the repo directory entirely; the raw event log, `SB_Player.csv`, `EPS_Offers.csv`, and `SB_MOM.csv` all live one level above this repo on disk and are read at pipeline-build time only.
- **What *is* committed under `artifacts/`** is, for the most part, fully aggregated derived output with no per-user content: co-occurrence edge counts, popularity counts, and a fixture→sport lookup — none of these expose an individual player's identity or behavior.
- **Known, disclosed exception**: `artifacts/ten_users_source_of_truth.xlsx/.json` and `artifacts/session_proof_*.xlsx` contain real (SHA-hashed, not raw) `PlayerID`s and real per-session fixture sequences, extracted from the provided sample. These exist to let a reviewer/mentor verify the held-out accuracy claim (42.1% hit rate) against real, traceable rows rather than a synthetic example — the project's own stated principle throughout has been "verify against real data, don't assert." This is a conscious, team-accepted deviation from the guidelines' "never upload real player data" instruction, made for demo-verifiability rather than convenience; the team was made aware of the conflict before committing these files. IDs are pseudonymized hashes as provided in the original sample, never raw account identifiers, and no financial/PII fields (stake amounts, payment details) are included.
- **`GET /v1/demo-users` and `GET /v1/login-verified`** expose this same 10-session verified set through the live API, for the same reason (a judge can pick a real verified session and watch the prediction happen live) — scoped to exactly those 10 sessions, not the full raw dataset.

## 7. AI-assisted development disclosure

This project's implementation (backend, frontend, offline pipeline, and this documentation) was built with substantial assistance from Claude Code (Anthropic), used as a code-generation and pair-programming tool throughout. The team reviewed, tested, and is responsible for all resulting code, its correctness, licensing, and originality — consistent with the guidelines' §6 requirement that the team remains responsible for AI-assisted output. No confidential FEG information, credentials, or real player data was submitted to any AI system beyond what was necessary for local development inside this environment; no such content was sent to an external/public AI system outside this development environment.

## 8. Third-party dependencies

See `docs/dependencies.md` for the full list, versions, and licenses. Summary: all dependencies are open-source (pandas, FastAPI, uvicorn, pydantic, openpyxl — all permissively licensed) or a CDN-served open-source library (`3d-force-graph`, MIT). No paid third-party APIs, no proprietary datasets beyond the hackathon-provided sample.
