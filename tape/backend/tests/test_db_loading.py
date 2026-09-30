from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from sqlalchemy import select

from tape.db import FillRow, load_fills_by_book, make_sessionmaker, store_fills
from tape.importers.base import Fill


def test_column_loader_matches_orm_rows(tmp_path):
    S = make_sessionmaker(f"sqlite:///{tmp_path / 'l.db'}")
    t = datetime(2026, 5, 4, 8, tzinfo=timezone.utc)
    with S() as s:
        store_fills(s, "a", "mt5", [Fill(f"{i}", t + timedelta(minutes=i // 2), "XAUUSD", "buy", D("0.1"), D("2000.5"),
                                         D(100), D("-0.7"), D("1.5") if i % 2 else None, D("1990") if i % 3 else None)
                                    for i in range(20)], book="acc1")
        store_fills(s, "a", "xtb", [Fill("x1", t, "XAGUSD", "sell", D(1), D("31.2"), D(5000))])     # bez rachunku
        store_fills(s, "b", "mt5", [Fill("z", t, "XAUUSD", "buy", D(1), D(1), D(100))])            # inne konto
        s.commit()
        loaded = load_fills_by_book(s, "a")
        rows = s.scalars(select(FillRow).where(FillRow.account == "a").order_by(FillRow.ts, FillRow.id))
        want = {}
        for r in rows:
            want.setdefault(r.book, []).append(r.to_fill())
    assert loaded == want
    assert loaded["acc1"][0].external_id == "mt5:acc1:0" and loaded[""][0].external_id == "xtb:x1"
