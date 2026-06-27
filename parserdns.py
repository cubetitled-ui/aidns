#!/usr/bin/env python3
"""High-level DNS-Shop parser.

Uses endpoints from api.dns.json to search, get product details,
browse categories, and build PC configs.

Usage:
    python3 parserdns.py search "видеокарта"
    python3 parserdns.py product <guid>
    python3 parserdns.py rsu
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

from utils import setup_logging, ensure_dir, PROJECT_DIR, CACHE_DIR

log = setup_logging("parser")

BASE = "https://www.dns-shop.ru"
RESTAPI = "https://restapi.dns-shop.ru"


@dataclass
class Product:
    guid: str
    code: str
    name: str
    price: int
    specs: str
    description: str
    image_url: str
    rating: float
    warranty_months: int
    country: str
    characteristics: dict
    url: str

    @classmethod
    def from_pwa(cls, data: dict) -> Product:
        return cls(
            guid=data["guid"],
            code=data["code"],
            name=data["name"],
            price=data["price"],
            specs=data.get("specs", ""),
            description=data.get("description", ""),
            image_url=data.get("imageUrl", ""),
            rating=data.get("rating", 0),
            warranty_months=data.get("monthWarranty", 0),
            country=data.get("manufacturerCountry", ""),
            characteristics=data.get("characteristics", {}),
            url=f"{BASE}/product/{data['guid']}/{data['code']}/",
        )


def search(query: str, strategy_id: str = "") -> list[dict]:
    """Search DNS-Shop via presearch API."""
    from pr_fetch import fetch_presearch
    data = fetch_presearch(query, strategy_id)
    return data


def get_product(guid: str, strategy_id: str = "") -> Optional[Product]:
    """Get full product info via PWA API."""
    from pr_fetch import fetch_product
    data = fetch_product(guid, strategy_id)
    if not data:
        return None
    return Product.from_pwa(data)


def get_rsu_categories(strategy_id: str = "") -> list[dict]:
    """Get PC builder category structure (RSU)."""
    from pr_fetch import fetch_rsu
    return fetch_rsu(strategy_id)


def search_products(query: str, max_pages: int = 1,
                    strategy_id: str = "") -> list[dict]:
    """Search products with full details from search results page.

    Parses the search results HTML and gets product details.
    """
    from pr_fetch import fetch_search_page
    products_meta = fetch_search_page(query, 1, strategy_id)
    results = []
    for meta in products_meta:
        guid = meta.get("guid")
        if guid:
            prod = get_product(guid, strategy_id)
            if prod:
                results.append(prod.__dict__)
    return results


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 0

    cmd = sys.argv[1]

    if cmd == "search":
        query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        if not query:
            query = input("query: ")
        results = search(query)
        print(json.dumps(results, ensure_ascii=False, indent=2))

    elif cmd == "product":
        if len(sys.argv) < 3:
            guid = input("product guid: ")
        else:
            guid = sys.argv[2]
        prod = get_product(guid)
        if prod:
            print(json.dumps(prod.__dict__, ensure_ascii=False, indent=2))
        else:
            log.error("product not found")
            return 1

    elif cmd == "rsu":
        data = get_rsu_categories()
        print(json.dumps(data, ensure_ascii=False, indent=2))

    else:
        print(f"unknown command: {cmd}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
