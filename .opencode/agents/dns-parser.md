---
description: >
  DNS-Shop parser project (aidns). Use when working on the DNS-Shop scraper,
  API endpoints, strategy rotation, or product parsing code.
  파이썬, DNS, парсер 관련 작업 시 사용.
mode: all
---

# DNS-Shop Parser (aidns)

Проект парсера DNS-Shop с ротацией стратегий и системой подбора ПК.

## Project structure

```
aidns/
├── api.dns.json          # All discovered DNS-Shop API endpoints
├── utils.py              # Shared: StrategyState, logging, helpers
├── pr_fetch.py           # Fetch with strategy rotation (curl_cffi + Playwright)
├── parserdns.py          # High-level parser: search, product, rsu
├── cache/
│   └── strategy_state.json  # Sticky strategy persistence
└── .opencode/agents/
    └── dns-parser.md
```

## API endpoints (see api.dns.json for full docs)

| Endpoint | What it returns |
|---|---|
| `GET /pwa/pwa/get-product/?id={guid}` | Full product info (name, price, specs, description, characteristics, rating, image) |
| `GET /v1/get-presearch?query=` | Search suggestions |
| `GET /v2/get-city` | City detection |
| `GET /catalog/category/get-rsu-state/` | PC builder categories (CPU, GPU, RAM, etc.) |
| `POST /ajax-state/product-buy/` | Prices for products on listing page |
| `GET /catalog/category/get-virtual-categories-list/?guid=` | Subcategories |

## Known issues

- Qrator (JS challenge) blocks all direct HTTP clients
- curl_cffi gets 403 (JS challenge not executed)
- Playwright Firefox gets detected as bot (403)
- undetected-chromedriver might work but not yet tested

## Strategy rotation pattern (from youthub/pr_fetch.py)

Each strategy combines: TLS fingerprint (chrome/firefox/safari/edge) ×
proxy mode (direct/env) × transport (curl_cffi / Playwright browser).

State is persisted in `cache/strategy_state.json`. Sticky: stays on
working strategy until it dies, then advances.
