# Cortex — Dependencies & Third-Party Components

FEG Innovation Hackathon 2026 · Challenge 1: Session Quality & Session-to-Action Conversion

## Runtime dependencies (Python, backend + offline pipeline)

Exact versions verified from the working environment, not assumed.

| Package | Version used | Licence | Purpose |
|---|---|---|---|
| `pandas` | 2.1.4 | BSD-3-Clause | Offline graph pipeline: cleaning, aggregation, co-occurrence/popularity computation |
| `fastapi` | 0.120.0 | MIT | Serving layer HTTP framework |
| `uvicorn` | 0.38.0 | BSD-3-Clause | ASGI server running the FastAPI app |
| `pydantic` | 1.10.8 | MIT | Request/response schema validation in the serving layer |
| `openpyxl` | 3.0.10 | MIT | Writes the `.xlsx` proof/verification exports used for demo material |
| `pytest` | 7.4.0 | MIT | Test runner for `tests/test_rank.py` |

Python version: 3.11.5.

Install with:
```
pip install -r requirements.txt
```

## Frontend dependencies

| Component | Source | Licence | Purpose |
|---|---|---|---|
| `3d-force-graph` | `https://unpkg.com/3d-force-graph` (CDN, no local install/build step) | MIT | WebGL 3D force-directed graph rendering, used only by the standalone `graph.html` session/graph explorer |

No other frontend dependencies — `src/frontend/index.html` is plain HTML/CSS/JavaScript with no build step, no bundler, and no framework, by deliberate choice (see `architecture.md` §6).

## Data sources

All three are FEG/hackathon-provided sample files, read from outside this repository (`src/graph_pipeline/config.py: DATA_DIR`) and never committed — see `docs/compliance-note.md` §6 for exactly what derived output *is* committed.

- `top_sport_users_event_logs.csv` — the primary raw sportsbook interaction event log (~1.39M rows, 92 players).
- `SB_Player.csv` — real settled-bet records, used only to source real per-sport match-name pools (`build_sport_name_pools.py`) and to seed verified demo-account histories; never used as a training signal for the recommendation algorithm itself.
- `EPS_Offers.csv` — real market/offer records, used as a supplementary source for the same per-sport name pools (5 overlapping sports).

## AI-assisted development disclosure

This project's code and documentation were built with substantial assistance from **Claude Code** (Anthropic), used as a code-generation and pair-programming tool. No paid AI API usage beyond the development environment itself; no confidential FEG information or real player data was submitted to any AI system outside this development environment. See `docs/compliance-note.md` §7 for the full disclosure statement.

## No other third-party components

No paid third-party APIs, no proprietary/licensed datasets beyond the hackathon-provided sample, no ML model weights (trained or pretrained) — the recommendation mechanism is pure counting/lookup over the artifacts described in `architecture.md`, not a trained model.
