"""Główne przepływy na prawdziwym PostgreSQL (typy NUMERIC, JSON, strefy czasowe, ograniczenia unikalności).

Uruchamiane tylko z TAPE_TEST_POSTGRES_URL, np.:
  TAPE_TEST_POSTGRES_URL=postgresql+psycopg://postgres@localhost:5432/tape_test pytest tests/test_postgres.py
"""

import io
import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

URL = os.getenv("TAPE_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="brak TAPE_TEST_POSTGRES_URL")

MT5_CSV = """Time,Deal,Symbol,Type,Direction,Volume,Price,Commission,Fee,Swap,Profit
2026.09.10 10:00:00,1,XAUUSD,buy,in,0.10,2650.12345,0,0,0,0
2026.09.10 12:00:00,2,XAUUSD,sell,out,0.10,2670.12345,-0.70,0,0,200.00
"""


@pytest.fixture
def client():
    from tape.db import Base, make_sessionmaker

    from sqlalchemy import text

    from tape.db import import_models

    import_models()
    engine = create_engine(URL)
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:                 # czysta baza: migracje wykonają się od zera
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()
    from tape.api import create_app

    return TestClient(create_app(URL))


def test_core_flows_on_postgres(client):
    rep = client.post("/api/imports", files={"file": ("d.csv", io.BytesIO(MT5_CSV.encode()), "text/csv")}).json()
    assert rep["new"] == 2
    again = client.post("/api/imports", files={"file": ("d.csv", io.BytesIO(MT5_CSV.encode()), "text/csv")}).json()
    assert again["new"] == 0 and again["duplicates"] == 2

    pos = client.get("/api/positions").json()[0]
    assert pos["avg_entry"] == "2650.12345" and pos["net_pnl"] == 199.3      # NUMERIC bez utraty precyzji
    sid = client.post("/api/setups", json={"name": "Breakout", "rules": ["A", "B"]}).json()["id"]
    ok = client.put(f"/api/positions/{pos['key']}/journal",
                    json={"setup_id": sid, "initial_stop": 2640.12345, "mistakes": ["Za duża pozycja"], "checklist": {"A": True}})
    assert ok.status_code == 200
    detail = client.get(f"/api/positions/{pos['key']}").json()
    assert detail["journal"]["initial_stop"] == "2640.1234500000"            # NUMERIC(28,10)
    assert detail["position"]["r_multiple"] == 1.99
    stats = client.get("/api/stats").json()
    assert stats["setups"][0]["key"] == "Breakout" and stats["mistakes"][0]["net_pnl"] == 199.3

    csv = b"timestamp,close\n2026-09-10 09:00:00,2649\n2026-09-10 11:00:00,2660\n"
    assert client.post("/api/prices", files={"file": ("p.csv", io.BytesIO(csv), "text/csv")}).json()["added"] == 2
    prices = client.get(f"/api/positions/{pos['key']}").json()["prices"]
    assert datetime.fromisoformat(prices[0]["t"]) == datetime(2026, 9, 10, 9, tzinfo=timezone.utc)


def test_news_pipeline_on_postgres(client):
    from tape.db import make_sessionmaker
    from tape.news.pipeline import run_once
    from tape.news.store import BiasSnapshot, EventRow

    from test_news_pipeline import STORY, art, fake_client

    Session = make_sessionmaker(URL)
    ai, msgs = fake_client()
    now = datetime.now(timezone.utc)
    with Session() as s:
        run_once(s, ai, lambda: [art(t, d, m, now) for t, d, m in STORY], now)
        run_once(s, ai, lambda: [art(t, d, m, now) for t, d, m in STORY], now)
        assert msgs.calls == 1
        assert len(s.scalars(select(EventRow)).all()) == 1
        assert len(s.scalars(select(BiasSnapshot)).all()) == 4
    assert client.get("/api/events").json()[0]["sample"] is False
    assert isinstance(Decimal(str(client.get("/api/bias").json()["XAU"]["score"])), Decimal)


def test_sync_and_portfolio_on_postgres(client):
    from test_portfolio import FLEX_CASH, MT5_WITH_BALANCE

    rep = client.post("/api/imports", files={"file": ("d.csv", io.BytesIO(MT5_WITH_BALANCE.encode()), "text/csv")}).json()
    assert rep["cash_flows"] == 2
    assert client.post("/api/imports", files={"file": ("f.xml", io.BytesIO(FLEX_CASH), "text/xml")}).json()["cash_flows"] == 1
    p = client.get("/api/portfolio").json()
    assert p["balance"] == 34200 and p["months"][0]["ret"] > 0

    tok = client.post("/api/connections/mt5", json={"label": "PG"}).json()["token"]
    deals = {"gmt_offset": 0, "deals": [
        {"ticket": 77, "time": 1789034400, "symbol": "XAGUSD", "type": "sell", "entry": "in", "volume": "0.3", "price": "40.12345"},
        {"ticket": 78, "time": 1789034500, "symbol": "", "type": "balance", "volume": 0, "price": 0, "profit": "500"},
    ]}
    r = client.post("/api/ingest/mt5", json=deals, headers={"Authorization": f"Bearer {tok}"}).json()
    assert r["new"] == 1
    holding = next(h for h in client.get("/api/portfolio").json()["holdings"] if h["symbol"] == "XAGUSD")
    assert holding["avg_price"] == "40.12345" and holding["ounces"] == -1500
