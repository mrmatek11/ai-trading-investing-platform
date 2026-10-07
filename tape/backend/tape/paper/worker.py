"""Worker paper tradingu: pobiera świece M15 i rozlicza zakończone dni aktywnych przebiegów.

    python -m tape.paper --every 300

Świece: OANDA (konto demo, strona bid + spread) albo Twelve Data. gold-api nie ma świec — wtedy paper
trading nie działa i UI to mówi.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import store

log = logging.getLogger("tape.paper")


def run_once(session: Session, provider=None, now: Optional[datetime] = None, count: int = 200) -> Dict[str, object]:
    now = now or datetime.now(timezone.utc)
    rep: Dict[str, object] = {}
    if provider is not None and hasattr(provider, "fetch_m15"):
        for asset in sorted({v.asset for v in store.VERSIONS.values()}):
            try:
                rep[f"bars_{asset}"] = store.save_bars(session, asset, provider.fetch_m15(asset, now, count), provider.name)
                session.commit()
            except Exception as exc:  # sieć / limit — rozliczamy na tym, co już jest w bazie
                session.rollback()
                rep[f"bars_{asset}"] = f"błąd: {exc}"
    recorded = 0
    for run in session.scalars(select(store.PaperRun).where(store.PaperRun.status == "active")).all():
        try:
            recorded += store.process_run(session, run, now)["recorded"]
            session.commit()
        except Exception as exc:  # jeden przebieg nie zatrzymuje pozostałych
            session.rollback()
            log.warning("paper %s: %s", run.id, exc)
    rep["recorded"] = recorded
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description="Paper trading GoldTape")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--every", type=int, default=300)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from ..db import make_sessionmaker
    from ..market import provider_from_env

    Session_ = make_sessionmaker()
    provider = provider_from_env()
    if provider is None or not hasattr(provider, "fetch_m15"):
        log.warning("dostawca bez świec M15 (ustaw TAPE_PRICE_PROVIDER=oanda albo twelvedata) — tylko rozliczanie")
    while True:
        with Session_() as s:
            log.info("paper: %s", run_once(s, provider))
        if args.once:
            break
        time.sleep(max(30, args.every))


if __name__ == "__main__":
    main()
