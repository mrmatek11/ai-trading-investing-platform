"""GDELT DOC 2.0 API — darmowy strumień newsów z całego świata (aktualizacja co 15 min).

Pobieramy listę artykułów (tytuł, URL, czas, domena) dla zapytania o metale i ich czynniki.
GDELT nie zwraca pełnej treści — klasyfikacja i walidacja cytatów działają na tytułach.
Licencja: GDELT jest otwarty; przy publikacji podajemy źródło.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable, List, Optional

from .classify import Article

ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"

# Zapytanie: bezpośrednio o metale + główne kanały wpływu (Fed, inflacja, konflikty, kopalnie).
DEFAULT_QUERY = (
    '(gold OR silver OR bullion OR "central bank" OR "Federal Reserve" OR inflation OR '
    '"interest rates" OR sanctions OR "mine strike" OR "safe haven") sourcelang:english'
)


def build_url(query: str = DEFAULT_QUERY, timespan: str = "1h", max_records: int = 250) -> str:
    params = {"query": query, "mode": "ArtList", "format": "json", "timespan": timespan,
              "maxrecords": str(max_records), "sort": "DateDesc"}
    return f"{ENDPOINT}?{urllib.parse.urlencode(params)}"


def parse(payload: str) -> List[Article]:
    """Odpowiedź GDELT → artykuły. GDELT czasem zwraca pusty tekst albo HTML z błędem — wtedy []."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    out = []
    for a in data.get("articles", []) or []:
        title = (a.get("title") or "").strip()
        url = (a.get("url") or "").strip()
        seen = a.get("seendate") or ""
        if not title or not url or not seen:
            continue
        try:
            ts = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        out.append(Article(title=title, text=title, url=url, published_at=ts))
    return out


def fetch(query: str = DEFAULT_QUERY, timespan: str = "1h",
          opener: Optional[Callable[[str], str]] = None) -> List[Article]:
    url = build_url(query, timespan)
    if opener is None:
        def opener(u: str) -> str:
            req = urllib.request.Request(u, headers={"User-Agent": "tape-news/0.1"})
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — stały, zaufany endpoint
                return resp.read().decode("utf-8", errors="replace")
    return parse(opener(url))
