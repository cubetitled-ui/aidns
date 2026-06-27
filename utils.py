"""Shared utilities for DNS-Shop parser.

Sticky strategy state (from youthub/pr_fetch.py pattern),
logging, helpers.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parent
CACHE_DIR = PROJECT_DIR / "cache"
STATE_FILE = CACHE_DIR / "strategy_state.json"


def setup_logging(name: str = "dns") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("[%(name)s] %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


# ── sticky strategy state ──────────────────────────────────────


@dataclass
class StrategyRecord:
    alive: bool = True
    wins: int = 0
    deaths: int = 0
    last_win: int = 0
    last_death: int = 0


@dataclass
class StrategyState:
    current: str = ""
    round: int = 1
    strategies: dict = field(default_factory=dict)

    def record_for(self, sid: str) -> StrategyRecord:
        if sid not in self.strategies:
            self.strategies[sid] = StrategyRecord().__dict__
        return StrategyRecord(**self.strategies[sid])

    def is_alive(self, sid: str) -> bool:
        return self.record_for(sid).alive

    def mark_success(self, sid: str) -> None:
        rec = self.record_for(sid)
        rec.alive = True
        rec.wins += 1
        rec.last_win = int(time.time())
        self.current = sid
        self.strategies[sid] = rec.__dict__

    def mark_death(self, sid: str) -> None:
        rec = self.record_for(sid)
        rec.alive = False
        rec.deaths += 1
        rec.last_death = int(time.time())
        self.strategies[sid] = rec.__dict__

    def save(self) -> None:
        ensure_dir(CACHE_DIR)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "current": self.current,
            "round": self.round,
            "strategies": self.strategies,
        }, indent=2, sort_keys=True))
        os.replace(tmp, STATE_FILE)

    @classmethod
    def load(cls) -> StrategyState:
        try:
            data = json.loads(STATE_FILE.read_text())
            return cls(
                current=data.get("current", ""),
                round=data.get("round", 1),
                strategies=data.get("strategies", {}),
            )
        except (FileNotFoundError, json.JSONDecodeError):
            return cls()
