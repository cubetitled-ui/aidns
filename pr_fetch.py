#!/usr/bin/env python3
"""Fetch DNS-Shop data with sticky-rotation strategy selection.

Adapted from youthub/pr_fetch.py — replaces YouTube fetching with
DNS-Shop API/HTML fetching while keeping the strategy rotation pattern.

Why this exists: DNS-Shop is behind Qrator (JS challenge, bot detection).
A single fixed approach gets blocked. Each "strategy" is a different
fingerprint combination (TLS profile × proxy mode × endpoint).

Modes:
    python3 pr_fetch.py presearch <query>    # search suggestions
    python3 pr_fetch.py product <guid>       # full product info
    python3 pr_fetch.py rsu                  # PC builder categories
    python3 pr_fetch.py --stats              # strategy state dump
    python3 pr_fetch.py --advance            # force next strategy
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from curl_cffi import requests

from utils import (
    setup_logging, ensure_dir, PROJECT_DIR, CACHE_DIR,
    StrategyState,
)

log = setup_logging("fetch")

BASE = "https://www.dns-shop.ru"
RESTAPI = "https://restapi.dns-shop.ru"

# ── User-Agents matching each impersonate profile ───────────────

CHROME_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
CHROME145_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)
CHROME_ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)
FIREFOX_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0"
)
SAFARI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
)
EDGE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)

# ── Endpoint configs ────────────────────────────────────────────

PRESEARCH_URL = f"{RESTAPI}/v1/get-presearch"
PRODUCT_URL = f"{BASE}/pwa/pwa/get-product/"
RSU_URL = f"{BASE}/catalog/category/get-rsu-state/"
SEARCH_URL = f"{BASE}/search/"

# ── Strategies ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Strategy:
    id: str
    impersonate: str
    proxy: str
    ua: str

    def headers(self, extra: Optional[dict] = None) -> dict:
        h = {
            "User-Agent": self.ua,
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
        }
        if extra:
            h.update(extra)
        return h

    def proxies_arg(self):
        if self.proxy == "direct":
            return {"http": None, "https": None}
        return None


def _build_rotation(*specs) -> list[Strategy]:
    """Build interleaved strategy list (same pattern as youthub)."""
    pairs = []
    for sid, imp, ua in specs:
        pairs.append((
            Strategy(f"{sid}-direct", imp, "direct", ua),
            Strategy(f"{sid}-proxy", imp, "env", ua),
        ))
    out = []
    for i, (d, p) in enumerate(pairs):
        out.append(d if i % 2 == 0 else p)
    for i, (d, p) in enumerate(pairs):
        out.append(p if i % 2 == 0 else d)
    return out


STRATEGIES = _build_rotation(
    ("cffi-chrome131",         "chrome131",         CHROME_UA),
    ("cffi-chrome145",         "chrome145",         CHROME145_UA),
    ("cffi-firefox133",        "firefox133",        FIREFOX_UA),
    ("cffi-safari184",         "safari184",         SAFARI_UA),
    ("cffi-chrome131_android", "chrome131_android", CHROME_ANDROID_UA),
    ("cffi-edge101",           "edge101",           EDGE_UA),
)
STRATEGIES_BY_ID = {s.id: s for s in STRATEGIES}
STRATEGY_INDEX = {s.id: i for i, s in enumerate(STRATEGIES)}


# ── State management ────────────────────────────────────────────


def _load_state() -> StrategyState:
    state = StrategyState.load()
    if state.current not in STRATEGIES_BY_ID:
        state.current = STRATEGIES[0].id
    return state


def _advance_to_next_alive(state: StrategyState) -> Strategy:
    n = len(STRATEGIES)
    cur_idx = STRATEGY_INDEX.get(state.current, 0)
    for offset in range(1, n + 1):
        cand = STRATEGIES[(cur_idx + offset) % n]
        if state.is_alive(cand.id):
            state.current = cand.id
            return cand
    state.round += 1
    for rec in state.strategies.values():
        rec["alive"] = True
    best = max(
        STRATEGIES,
        key=lambda s: (
            state.strategies.get(s.id, {}).get("wins", 0),
            state.strategies.get(s.id, {}).get("last_win", 0),
        ),
    )
    if state.strategies.get(best.id, {}).get("wins", 0) == 0:
        best = STRATEGIES[0]
    state.current = best.id
    log.info(f"all strategies dead — starting round {state.round} "
             f"from {best.id}")
    return best


def get_current() -> Strategy:
    state = _load_state()
    cur = STRATEGIES_BY_ID.get(state.current, STRATEGIES[0])
    if not state.is_alive(cur.id):
        cur = _advance_to_next_alive(state)
        state.save()
    return cur


def record_success(sid: str) -> None:
    state = _load_state()
    state.mark_success(sid)
    state.save()


def record_death(sid: str) -> None:
    state = _load_state()
    state.mark_death(sid)
    if state.current == sid:
        _advance_to_next_alive(state)
    state.save()


# ── Fetch primitives ────────────────────────────────────────────


def _new_session() -> requests.Session:
    sess = requests.Session()
    return sess


def _try_strategy(strat: Strategy, url: str,
                  params: Optional[dict] = None,
                  method: str = "GET",
                  json_body: Optional[dict] = None,
                  accept_json: bool = True) -> Optional[Any]:
    t0 = time.time()
    sess = _new_session()
    headers = strat.headers({"Accept": "application/json, text/html, */*"})

    try:
        if method == "GET":
            r = sess.get(
                url, params=params, headers=headers,
                impersonate=strat.impersonate,
                proxies=strat.proxies_arg(),
                timeout=20,
            )
        else:
            r = sess.post(
                url, params=params, json=json_body, headers=headers,
                impersonate=strat.impersonate,
                proxies=strat.proxies_arg(),
                timeout=20,
            )
    except Exception as e:
        log.warning(f"{strat.id}: network error: {e}")
        return None

    if r.status_code != 200:
        log.warning(f"{strat.id}: HTTP {r.status_code}")
        return None

    if accept_json:
        try:
            return r.json()
        except Exception as e:
            log.warning(f"{strat.id}: bad JSON: {e}")
            return None

    return r.text


def _fetch_with_rotation(url: str,
                         params: Optional[dict] = None,
                         method: str = "GET",
                         json_body: Optional[dict] = None,
                         accept_json: bool = True,
                         force_strategy: str = "") -> Optional[Any]:
    if force_strategy:
        strat = STRATEGIES_BY_ID.get(force_strategy)
        if not strat:
            log.error(f"unknown strategy: {force_strategy}")
            return None
        return _try_strategy(strat, url, params, method, json_body, accept_json)

    strat = get_current()
    log.info(f"using {strat.id} (round {_load_state().round})")

    result = _try_strategy(strat, url, params, method, json_body, accept_json)
    if result is None:
        record_death(strat.id)
        new_id = _load_state().current
        log.info(f"died → next: {new_id}")
        return None

    record_success(strat.id)
    return result


# ── Public fetch functions ──────────────────────────────────────


def fetch_presearch(query: str, strategy_id: str = "") -> list[dict]:
    """Get search suggestions from presearch API."""
    data = _fetch_with_rotation(
        PRESEARCH_URL,
        params={"query": query},
        force_strategy=strategy_id,
    )
    if not data:
        return []
    return data.get("data", [])


def fetch_product(guid: str, strategy_id: str = "") -> Optional[dict]:
    """Get full product info via PWA API.

    Returns the 'data' field from the response or None.
    """
    data = _fetch_with_rotation(
        PRODUCT_URL,
        params={"id": guid},
        force_strategy=strategy_id,
    )
    if not data or not data.get("result"):
        return None
    return data.get("data")


def fetch_rsu(strategy_id: str = "") -> list[dict]:
    """Get PC builder category structure."""
    data = _fetch_with_rotation(
        RSU_URL,
        force_strategy=strategy_id,
    )
    if not data or not data.get("result"):
        return []
    return data.get("data", {}).get("categories", [])


def fetch_search_page(query: str, page: int = 1,
                      strategy_id: str = "") -> list[dict]:
    """Get search results page and extract product metadata.

    Returns list of dicts with guid and basic info.
    """
    html = _fetch_with_rotation(
        SEARCH_URL,
        params={"q": query, "p": page},
        accept_json=False,
        force_strategy=strategy_id,
    )
    if not html:
        return []

    # Extract product GUIDs from HTML
    guids = re.findall(r'data-guid="([a-f0-9-]+)"', html)
    seen = set()
    products = []
    for g in guids:
        if g not in seen:
            seen.add(g)
            products.append({"guid": g})
    return products


# ── Stats & advance ─────────────────────────────────────────────


def _stats_dump() -> int:
    state = _load_state()
    now = int(time.time())

    def ago(ts: int) -> str:
        if ts == 0:
            return "never"
        d = now - ts
        if d < 60:
            return f"{d}s"
        if d < 3600:
            return f"{d // 60}m"
        return f"{d // 3600}h"

    print(f"round: {state.round}   current: {state.current}")
    print()
    print(f"{'#':>3} {'strategy':<30} {'state':<6} {'last_win':>10} "
          f"{'last_death':>10} {'wins':>5} {'deaths':>6}")
    for i, s in enumerate(STRATEGIES):
        rec = state.record_for(s.id)
        marker = "→" if state.current == s.id else " "
        st_label = "alive" if rec.alive else "DEAD"
        print(f"{marker:>1}{i:>2} {s.id:<30} {st_label:<6} "
              f"{ago(rec.last_win):>10} "
              f"{ago(rec.last_death):>10} "
              f"{rec.wins:>5} {rec.deaths:>6}")
    return 0


def _advance_main() -> int:
    state = _load_state()
    old = state.current
    new = _advance_to_next_alive(state)
    state.save()
    log.info(f"advance: {old} → {new.id}")
    return 0


# ── Main ────────────────────────────────────────────────────────


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 64

    cmd = sys.argv[1]

    if cmd == "--stats":
        return _stats_dump()
    if cmd == "--advance":
        return _advance_main()

    if cmd == "presearch":
        query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        if not query:
            query = input("query: ")
        data = fetch_presearch(query)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    if cmd == "product":
        if len(sys.argv) < 3:
            guid = input("product guid: ")
        else:
            guid = sys.argv[2]
        data = fetch_product(guid)
        if data:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            log.error("not found")
            return 1
        return 0

    if cmd == "rsu":
        data = fetch_rsu()
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    if cmd == "search":
        query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        if not query:
            query = input("query: ")
        products = fetch_search_page(query)
        print(json.dumps(products, ensure_ascii=False, indent=2))
        return 0

    print(f"unknown command: {cmd}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
