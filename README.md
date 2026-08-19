# NIFTY 50 XGBoost Engine

Modular XGBoost engine for NIFTY 50 with project constitution, brain/config separation, tests and Dhan API credentials via env.

> **Status: WIP / work-in-progress.** The package layout and build metadata are
> in place, but the core implementation (`engine`, `execution`, `bars`,
> `labels`, `providers`, `validation`, `tuning`, `inference`) is not yet
> implemented. Empty placeholder modules were removed from tracking. This is an
> architecture skeleton, not a runnable trading system.

## Highlights

- `brain/` — model logic
- `config/` — configuration
- `src/` + `tests/` — clean package layout with test coverage
- `pyproject.toml` — modern packaging
- Dhan credentials via `.env` (never committed)

## Implemented vs planned

| Area | Status |
|---|---|
| Package layout / `pyproject.toml` | Implemented |
| `src/providers/` (Dhan, replay, sensex) | Planned |
| `src/bars/`, `src/labels/` | Planned |
| `src/backtest/`, `src/validation/` | Planned |
| `src/tuning/`, `src/inference/` | Planned |
| `src/guardian/`, `src/dashboard/`, `src/hermes_bridge/` | Planned |

See `PROJECT_CONSTITUTION.md` and `AGENTS.md`.

## About the Author

**[Shakti Tiwari](https://dev.to/shaktitiwari)** — Nifty Option Trader · XGBoost Expert · Author of
[*Option Trading with AI*](https://www.amazon.in/dp/B0H9ZNTBPK) and
[*The AI Opportunity*](https://www.amazon.in/dp/B0HBBFKDQF).

- Site: https://optiontradingwithai.in
- Dev.to articles: https://dev.to/shaktitiwari
- Active GitHub: [github.com/monikerprivacy-byte](https://github.com/monikerprivacy-byte)

> **Disclaimer:** Educational/research software. Not investment advice. Backtested
> results are not indicative of future performance.
