"""Pipeline newsów w trybie shadow: pobierz → zgrupuj → sklasyfikuj → zapisz → zaloguj nastawienie.

Uruchomienie (np. co 15 minut z crona / kolejki):
    python -m tape.news.pipeline --once
    python -m tape.news.pipeline --every 900

Kontrola kosztów: klasyfikujemy tylko klastry z artykułami z ≥ MIN_DOMAINS różnych serwisów
(sprawa, o której pisze więcej niż jedno medium) i najwyżej MAX_CLUSTERS_PER_RUN na uruchomienie.
"""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import gdelt, rss
from .bias import aggregate
from .classify import MODEL, Article, classify_cluster
from .cluster import cluster
from .store import ArticleRow, BiasSnapshot, EventRow, recent_events, save_event

log = logging.getLogger("tape.news")

MIN_DOMAINS = 2
MAX_CLUSTERS_PER_RUN = 20
LOOKBACK = timedelta(hours=24)
ASSETS = ("XAU", "XAG")


@dataclass
class RunReport:
    fetched: int = 0
    new_articles: int = 0
    clusters: int = 0
    classified: int = 0
    skipped: int = 0
    snapshots: int = 0


def fetch_all_sources() -> List[Article]:
    """GDELT + kanały RSS; awaria jednego źródła nie zatrzymuje drugiego."""
    out: List[Article] = []
    for name, fn in (("gdelt", gdelt.fetch), ("rss", rss.fetch)):
        try:
            out.extend(fn())
        except Exception as exc:  # sieć / format — pipeline działa dalej na tym, co przyszło
            log.warning("źródło %s niedostępne: %s", name, exc)
    return out


def ingest(session: Session, articles: List[Article]) -> int:
    """Zapisz nowe artykuły (po URL); zwraca liczbę nowych."""
    known = set(session.scalars(select(ArticleRow.url).where(ArticleRow.url.in_([a.url for a in articles]))))
    n = 0
    for a in articles:
        if a.url not in known:
            session.add(ArticleRow(url=a.url[:1024], title=a.title, published_at=a.published_at))
            known.add(a.url)
            n += 1
    session.flush()
    return n


def run_once(session: Session, client, fetch: Callable[[], List[Article]] = fetch_all_sources,
             now: Optional[datetime] = None) -> RunReport:
    now = now or datetime.now(timezone.utc)
    rep = RunReport()

    articles = fetch()
    rep.fetched = len(articles)
    rep.new_articles = ingest(session, articles)

    # Grupujemy całe okno 24 h, żeby nowe artykuły dołączały do spraw z poprzednich uruchomień.
    window = [r.to_article() for r in session.scalars(select(ArticleRow).where(ArticleRow.published_at >= now - LOOKBACK))]
    groups = cluster(window)
    rep.clusters = len(groups)
    done = set(session.scalars(select(EventRow.id)))
    by_url = {r.url: r for r in session.scalars(select(ArticleRow).where(ArticleRow.published_at >= now - LOOKBACK))}
    candidates = []
    for c in groups:
        # Sprawa już sklasyfikowana wcześniej (jej pierwszy artykuł mógł wypaść z okna 24 h) —
        # zachowujemy jej identyfikator, żeby nie klasyfikować i nie liczyć jej drugi raz.
        inherited = {by_url[a.url].cluster_id for a in c.articles} & done
        event_id = min(inherited) if inherited else c.id
        for a in c.articles:
            by_url[a.url].cluster_id = event_id
        if event_id not in done and c.domains >= MIN_DOMAINS:
            candidates.append((event_id, c))
    candidates.sort(key=lambda x: -x[1].domains)          # najpierw sprawy opisywane najszerzej

    for event_id, c in candidates[:MAX_CLUSTERS_PER_RUN]:
        try:
            event = classify_cluster(client, event_id, c.articles)
        except Exception as exc:  # błąd API nie może zatrzymać całego przebiegu
            log.warning("klasyfikacja %s nieudana: %s", c.id, exc)
            event = None
        if event is None:
            rep.skipped += 1
            continue
        save_event(session, event, MODEL)
        rep.classified += 1
    rep.skipped += max(0, len(candidates) - MAX_CLUSTERS_PER_RUN)

    events = recent_events(session, now)
    for asset in ASSETS:
        b = aggregate(events, asset, now)
        session.add(BiasSnapshot(ts=now, asset=asset, score=b.score, label=b.label, events_used=b.events_used))
        rep.snapshots += 1
    session.commit()
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pipeline newsów Tape (tryb shadow)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--every", type=int, default=900, help="Odstęp między przebiegami w sekundach")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from ..ai_keys import server_llm
    from ..api import default_ai_client
    from ..db import make_sessionmaker

    Session_ = make_sessionmaker()
    client = server_llm(default_ai_client())          # Claude (ANTHROPIC_API_KEY) albo DeepSeek (DEEPSEEK_API_KEY)
    if client is None:
        raise SystemExit("Pipeline newsów potrzebuje ANTHROPIC_API_KEY albo DEEPSEEK_API_KEY")
    while True:
        with Session_() as s:
            rep = run_once(s, client)
        log.info("news: %s", rep)
        if args.once:
            break
        time.sleep(args.every)


if __name__ == "__main__":
    main()
