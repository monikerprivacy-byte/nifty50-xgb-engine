# Brain Index — Research, Issues & Audit Trail

This folder is the project's persistent memory. Every research finding, issue, solution, and audit record lives here.

## Structure

```
brain/
├── INDEX.md                     ← You are here
├── research/                    ← Deep research on each topic
│   ├── dhan_api.md             ← Dhan API capabilities, limitations, endpoints
│   ├── market_microstructure.md ← NSE microstructure, spreads, patterns
│   ├── xgboost_finance.md      ← XGBoost best practices for finance
│   └── validation.md           ← CPCV, purging, embargo, DSR
├── issues/                      ← Every issue discovered + resolution
│   ├── rolling_strike_confirmed.md
│   ├── intraday_options_empty.md
│   └── template.md
├── solutions/                   ← Verified solutions and workarounds
│   └── fixed_strike_data_strategy.md
├── audit/                       ← Audit trail for every decision
│   ├── dhan_api_test_results.md
│   └── design_decisions.md
└── experiments/                 ← Experiment tracking
    └── template.md
```

## Quick Links
- [Dhan API Research](research/dhan_api.md)
- [Market Microstructure](research/market_microstructure.md)
- [Rolling Strike Issue](issues/rolling_strike_confirmed.md)
- [Fixed-Strike Data Strategy](solutions/fixed_strike_data_strategy.md)
- [API Test Results](audit/dhan_api_test_results.md)
