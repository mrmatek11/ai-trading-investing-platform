"""Limit zapytań dla publicznych endpointów (ingest MT5, MCP, logowanie Discord).

Token bucket na parę (adres IP, grupa endpointów): pozwala na krótkie serie — pierwsza synchronizacja EA
wysyła historię paczkami jedna po drugiej, Claude Code na starcie robi kilka wywołań naraz — a przy
dłuższym zalewie zwraca 429 z nagłówkiem Retry-After.

Limiter jest w pamięci procesu: przy kilku workerach uvicorna każdy liczy osobno (limit efektywnie ×N).
Pamięć jest ograniczona: nieużywane kubełki są usuwane, a przy przepełnieniu najstarsze wypadają.

Adres klienta: za nginx (TAPE_TRUST_PROXY=1) bierzemy OSTATNI wpis X-Forwarded-For — ten dopisany przez
nasz nginx. Wcześniejsze wpisy podaje klient i można je podrobić.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple


@dataclass(frozen=True)
class Rule:
    capacity: float          # ile zapytań można zrobić seryjnie
    per_second: float        # tempo odnawiania


class RateLimiter:
    def __init__(self, rules: Dict[str, Rule], clock: Callable[[], float] = time.monotonic, max_keys: int = 50_000):
        self.rules, self.clock, self.max_keys = rules, clock, max_keys
        self._buckets: "OrderedDict[Tuple[str, str], Tuple[float, float]]" = OrderedDict()   # (tokeny, czas)

    def hit(self, group: str, client: str) -> Optional[int]:
        """None = przepuść; liczba = za ile sekund spróbować ponownie."""
        rule = self.rules[group]
        now = self.clock()
        key = (group, client)
        tokens, last = self._buckets.pop(key, (rule.capacity, now))
        tokens = min(rule.capacity, tokens + (now - last) * rule.per_second)
        if tokens >= 1:
            self._buckets[key] = (tokens - 1, now)
            retry = None
        else:
            self._buckets[key] = (tokens, now)
            retry = max(1, math.ceil((1 - tokens) / rule.per_second))
        self._prune(now)
        return retry

    def _prune(self, now: float) -> None:
        while len(self._buckets) > self.max_keys:          # przepełnienie: wypada najdawniej użyty
            self._buckets.popitem(last=False)
        # kubełki pełne od dawna są niepotrzebne — sprawdzamy kilka najstarszych przy każdym trafieniu
        for _ in range(4):
            if not self._buckets:
                break
            (group, client), (tokens, last) = next(iter(self._buckets.items()))
            rule = self.rules[group]
            if tokens + (now - last) * rule.per_second >= rule.capacity:
                self._buckets.popitem(last=False)
            else:
                break

    def __len__(self) -> int:
        return len(self._buckets)


# serie: EA ~10 paczek historii od razu, potem co minutę; MCP ~kilkanaście wywołań w sesji Claude Code
DEFAULT_RULES = {
    "ingest": Rule(capacity=30, per_second=0.5),       # 30 od razu, potem 30/min
    "mcp": Rule(capacity=60, per_second=2.0),          # 60 od razu, potem 120/min
    "auth": Rule(capacity=10, per_second=0.2),         # logowanie: 10 od razu, potem 12/min
}
GROUPS = {
    "/api/ingest/mt5": "ingest",
    "/api/mcp": "mcp",
    "/api/auth/discord/login": "auth",
    "/api/auth/discord/callback": "auth",
}


def client_ip(peer: Optional[str], forwarded_for: Optional[str], trust_proxy: bool) -> str:
    if trust_proxy and forwarded_for:
        last = forwarded_for.split(",")[-1].strip()
        if last:
            return last
    return peer or "unknown"
