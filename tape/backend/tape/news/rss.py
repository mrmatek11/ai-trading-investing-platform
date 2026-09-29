"""Newsy z kanałów RSS / Atom — darmowe, oficjalne źródła obok GDELT.

Dlaczego RSS: banki centralne i urzędy statystyczne publikują komunikaty jako RSS w chwili publikacji,
bez klucza API i bez limitów. Serwisy rynkowe (metale, FX) też mają kanały RSS. Pobieramy tylko tytuł,
link, czas i krótki opis — w aplikacji pokazujemy nagłówek ze źródłem i linkiem, nie przedrukowujemy treści.

- Parser RSS 2.0, RSS 1.0 (RDF) i Atom; XML przez defusedxml (blokuje encje: XXE, „billion laughs”).
- Każdy kanał osobno: błąd jednego nie zatrzymuje pozostałych.
- Kanały ogólne (filter=True) przepuszczają tylko wpisy o metalach i ich czynnikach (Fed, inflacja, USD…).
- Lista kanałów: DEFAULT_FEEDS albo TAPE_RSS_FEEDS="nazwa|url|filter;…" (filter = 1/0).
  ⚠️ Adresy domyślnych kanałów sprawdź przed produkcją — serwisy potrafią je zmieniać.
"""

from __future__ import annotations

import html
import logging
import os
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from .classify import Article

log = logging.getLogger("tape.news.rss")

MAX_BYTES = 3 * 1024 * 1024
MAX_ITEMS_PER_FEED = 60


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    filter: bool = True          # True = zostaw tylko wpisy pasujące do słów kluczowych


DEFAULT_FEEDS: Tuple[Feed, ...] = (
    Feed("Federal Reserve", "https://www.federalreserve.gov/feeds/press_monetary.xml", filter=False),
    Feed("ECB", "https://www.ecb.europa.eu/rss/press.html", filter=False),
    Feed("BLS", "https://www.bls.gov/feed/bls_latest.rss", filter=False),
    Feed("FXStreet", "https://www.fxstreet.com/rss/news"),
    Feed("Mining.com", "https://www.mining.com/commodity/gold/feed/", filter=False),
    Feed("Investing.com", "https://www.investing.com/rss/news_11.rss"),
)

KEYWORDS = re.compile(
    r"\b(gold|silver|bullion|xau|xag|precious metals?|fed|fomc|powell|federal reserve|rate (?:cut|hike)s?|"
    r"interest rates?|inflation|cpi|pce|payrolls?|nfp|jobs report|unemployment|treasur(?:y|ies)|yields?|"
    r"dollar|dxy|usd|central bank|ecb|safe[- ]haven|sanctions?|geopolitic\w*|mine|mining)\b",
    re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def feeds_from_env(value: Optional[str] = None) -> List[Feed]:
    raw = (os.getenv("TAPE_RSS_FEEDS", "") if value is None else value).strip()
    if not raw:
        return list(DEFAULT_FEEDS)
    out = []
    for part in raw.split(";"):
        bits = [b.strip() for b in part.split("|")]
        if len(bits) >= 2 and bits[1].startswith("https://"):
            out.append(Feed(bits[0] or bits[1], bits[1], filter=(bits[2] != "0") if len(bits) > 2 else True))
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child(el: Element, *names: str) -> Optional[Element]:
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _text(el: Optional[Element]) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def clean(text: str, limit: int = 400) -> str:
    """HTML z opisu → zwykły tekst (bez tagów i encji), przycięty."""
    t = _WS.sub(" ", html.unescape(_TAG.sub(" ", text))).strip()
    return t if len(t) <= limit else t[: limit - 1].rsplit(" ", 1)[0] + "…"


def parse_date(value: str) -> Optional[datetime]:
    value = value.strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)                        # RSS 2.0: RFC 822
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))   # Atom / RDF: ISO 8601
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse(data: bytes, feed: Feed) -> List[Article]:
    """Treść kanału → artykuły. Niepoprawny XML albo encje → ValueError."""
    try:
        root = ElementTree.fromstring(data)
    except (ParseError, DefusedXmlException) as exc:
        raise ValueError(f"{feed.name}: niepoprawny XML ({type(exc).__name__})") from exc
    items = [e for e in root.iter() if _local(e.tag) in ("item", "entry")]
    out: List[Article] = []
    for it in items[:MAX_ITEMS_PER_FEED]:
        title = clean(_text(_child(it, "title")), 300)
        links = [c for c in it if _local(c.tag) == "link"]
        atom = [c for c in links if c.get("href")]
        if atom:                                                 # Atom: rel="alternate" (albo brak rel) = strona artykułu
            best = next((c for c in atom if c.get("rel", "alternate") == "alternate"), atom[0])
            link = best.get("href", "").strip()
        else:
            link = _text(links[0]) if links else ""
        if not link:
            guid = _text(_child(it, "guid", "id"))
            link = guid if guid.startswith("http") else ""
        when = None
        for name in ("pubdate", "published", "updated", "date", "issued"):
            when = parse_date(_text(_child(it, name)))
            if when:
                break
        if not title or not link.startswith(("https://", "http://")) or when is None:
            continue
        summary = clean(_text(_child(it, "description", "summary", "content", "encoded")))
        if feed.filter and not KEYWORDS.search(f"{title} {summary}"):
            continue
        out.append(Article(title=title, text=f"{title}. {summary}" if summary else title, url=link, published_at=when))
    return out


def _open(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "tape-news/0.1 (+rss)", "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 — adresy z konfiguracji administratora
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("kanał większy niż 3 MB")
    return data


@dataclass
class FetchReport:
    articles: List[Article] = field(default_factory=list)
    feeds: Dict[str, str] = field(default_factory=dict)       # nazwa → "ok (n)" albo opis błędu


def fetch_all(feeds: Sequence[Feed] = DEFAULT_FEEDS, opener: Callable[[str], bytes] = _open,
              now: Optional[datetime] = None, max_age: timedelta = timedelta(days=2)) -> FetchReport:
    now = now or datetime.now(timezone.utc)
    rep = FetchReport()
    seen = set()
    for f in feeds:
        try:
            items = parse(opener(f.url), f)
        except Exception as exc:  # sieć, XML, limit rozmiaru — jeden kanał nie psuje reszty
            rep.feeds[f.name] = f"błąd: {str(exc)[:120]}"
            log.warning("rss %s: %s", f.name, exc)
            continue
        fresh = [a for a in items if now - max_age <= a.published_at <= now + timedelta(minutes=10) and a.url not in seen]
        seen.update(a.url for a in fresh)
        rep.articles.extend(fresh)
        rep.feeds[f.name] = f"ok ({len(fresh)})"
    return rep


def fetch(feeds: Optional[Sequence[Feed]] = None, opener: Callable[[str], bytes] = _open) -> List[Article]:
    """Interfejs jak gdelt.fetch — do pipeline'u newsów."""
    return fetch_all(feeds if feeds is not None else feeds_from_env(), opener).articles
