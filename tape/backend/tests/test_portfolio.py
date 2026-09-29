import io
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from tape.api import create_app
from tape.engine.portfolio import Flow, metal_of, monthly_returns, report
from tape.engine.positions import build_positions
from tape.importers.base import Fill

D = Decimal


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def fill(i, ts, side, qty, price, symbol="XAUUSD", cs=100, pnl=None):
    return Fill(str(i), ts, symbol, side, D(qty), D(price), D(cs), D(0), None if pnl is None else D(pnl))


@pytest.mark.parametrize("sym,metal", [("XAUUSD", "XAU"), ("GCZ6", "XAU"), ("MGCZ6", "XAU"), ("XAGUSD", "XAG"),
                                       ("SIZ6", "XAG"), ("SILH7", "XAG"), ("EURUSD", None), ("GCZ", None)])
def test_metal_of(sym, metal):
    assert metal_of(sym) == metal


def test_modified_dietz_and_chain():
    pos = build_positions([
        fill(1, utc(2026, 9, 5), "buy", "0.5", "2600"), fill(2, utc(2026, 9, 10), "sell", "0.5", "2610"),   # +500
        fill(3, utc(2026, 10, 20), "buy", "0.3", "2700"), fill(4, utc(2026, 10, 21), "sell", "0.3", "2710"),  # +300
    ])
    flows = [Flow(utc(2026, 9, 1), D(10000)), Flow(utc(2026, 10, 16), D(5000))]
    sep, oct_ = monthly_returns(pos, flows)
    assert sep.ret == pytest.approx(0.05)
    # październik: 5000 wpłacone po 15 z 31 dni → waga 16/31
    assert oct_.ret == pytest.approx(300 / (10500 + 5000 * 16 / 31))
    assert oct_.start_equity == D(10500) and oct_.end_equity == D(15800)
    rep = report(pos, flows, {}, now=utc(2026, 11, 1))
    assert rep.balance == D(15800) and rep.twr == pytest.approx(1.05 * (1 + oct_.ret) - 1)
    assert rep.ytd == rep.twr


def test_gap_months_and_no_flows():
    pos = build_positions([fill(1, utc(2026, 1, 5), "buy", "1", "2000"), fill(2, utc(2026, 1, 6), "sell", "1", "2010"),
                           fill(3, utc(2026, 3, 5), "buy", "1", "2000"), fill(4, utc(2026, 3, 6), "sell", "1", "1990")])
    months = monthly_returns(pos, [])
    assert [m.month for m in months] == ["2026-01", "2026-02", "2026-03"]
    assert all(m.ret is None for m in months)                       # bez kapitału nie ma stopy zwrotu
    rep = report(pos, [], {})
    assert rep.balance is None and rep.twr is None and rep.realized == 0


def test_holdings_fifo_and_exposure():
    pos = build_positions([
        fill(1, utc(2026, 9, 1), "buy", "1", "2600"), fill(2, utc(2026, 9, 2), "buy", "1", "2700"),
        fill(3, utc(2026, 9, 3), "sell", "1", "2650"),                           # zamyka lot po 2600 (FIFO)
        fill(4, utc(2026, 9, 3), "sell", "2", "31.50", symbol="SIZ6", cs=5000),  # short futures srebra
    ])
    marks = {"XAU": (utc(2026, 9, 4), 2720.0), "XAG": (utc(2026, 9, 4), 31.0)}
    rep = report(pos, [], marks)
    gold = next(h for h in rep.holdings if h.symbol == "XAUUSD")
    assert gold.avg_price == D(2700) and gold.qty == D(1) and gold.ounces == D(100)
    assert gold.unrealized == D(2000)                                          # (2720 − 2700) × 1 × 100
    silver = next(h for h in rep.holdings if h.symbol == "SIZ6")
    assert silver.ounces == D(-10000) and silver.unrealized is None            # futures: bez wyceny spotem
    assert rep.exposure["XAU"].ounces == D(100) and rep.exposure["XAU"].notional == D(272000)
    assert rep.exposure["XAG"].notional == D(-310000)


MT5_WITH_BALANCE = """Time,Deal,Symbol,Type,Direction,Volume,Price,Commission,Fee,Swap,Profit
2026.09.01 09:00:00,100,,balance,,,,0,0,0,10000.00
2026.09.10 10:00:00,1,XAUUSD,buy,in,0.10,2650.00,0,0,0,0
2026.09.10 12:00:00,2,XAUUSD,sell,out,0.10,2670.00,0,0,0,200.00
2026.09.20 09:00:00,101,,balance,,,,0,0,0,-1000.00
"""

FLEX_CASH = b"""<FlexQueryResponse><FlexStatements><FlexStatement>
<CashTransactions>
 <CashTransaction type="Deposits/Withdrawals" amount="25000" currency="USD" dateTime="20260901;100000" transactionID="9001"/>
 <CashTransaction type="Dividends" amount="12" currency="USD" dateTime="20260902;100000" transactionID="9002"/>
 <CashTransaction type="Deposits/Withdrawals" amount="25000" currency="USD" levelOfDetail="SUMMARY" dateTime="20260901;100000"/>
</CashTransactions>
</FlexStatement></FlexStatements></FlexQueryResponse>"""


def test_api_portfolio_flows(tmp_path):
    c = TestClient(create_app(f"sqlite:///{tmp_path / 'p.db'}"))
    up = lambda name, data: c.post("/api/imports", files={"file": (name, io.BytesIO(data), "text/plain")}).json()  # noqa: E731
    rep = up("deals.csv", MT5_WITH_BALANCE.encode())
    assert rep["new"] == 2 and rep["cash_flows"] == 2 and rep["error_count"] == 0
    assert up("deals.csv", MT5_WITH_BALANCE.encode())["cash_flows"] == 0          # idempotentnie
    assert up("flex.xml", FLEX_CASH)["cash_flows"] == 1

    p = c.get("/api/portfolio").json()
    assert p["deposits"] == 34000 and p["realized"] == 200 and p["balance"] == 34200
    assert [x["amount"] for x in p["cash_flows"]] == [10000, 25000, -1000]
    # wrzesień, czasy w UTC: MT5 09:00 UTC+2 = 07:00, IBKR 10:00 EDT = 14:00, wypłata 20.09 07:00
    assert p["months"][0]["ret"] == pytest.approx(200 / (10000 * (30 - 7 / 24) / 30 + 25000 * (30 - 14 / 24) / 30
                                                          - 1000 * (30 - 19 - 7 / 24) / 30), rel=1e-9)

    manual = c.post("/api/cashflows", json={"ts": "2026-10-01T00:00:00Z", "amount": 500, "note": "dopłata"})
    assert manual.status_code == 201
    assert c.post("/api/cashflows", json={"ts": "2026-10-01T00:00:00Z", "amount": 0}).status_code == 422
    broker_flow = p["cash_flows"][0]["id"]
    assert c.delete(f"/api/cashflows/{broker_flow}").status_code == 400
    assert c.delete(f"/api/cashflows/{manual.json()['id']}").json() == {"ok": True}


def test_api_portfolio_isolation(tmp_path):
    from test_auth import ISSUER, LocalVerifier, token

    verify = LocalVerifier("https://unused/jwks.json", ISSUER, authorized_parties=("https://app.example.com",))
    c = TestClient(create_app(f"sqlite:///{tmp_path / 'i.db'}", verifier=verify))
    a = {"Authorization": f"Bearer {token('user_a')}"}
    b = {"Authorization": f"Bearer {token('user_b')}"}
    fid = c.post("/api/cashflows", headers=a, json={"ts": "2026-10-01T00:00:00Z", "amount": 500}).json()["id"]
    assert c.get("/api/portfolio", headers=b).json()["cash_flows"] == []
    assert c.delete(f"/api/cashflows/{fid}", headers=b).status_code == 404
    assert c.get("/api/portfolio", headers=a).json()["deposits"] == 500
