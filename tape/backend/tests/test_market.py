import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from tape import market
from tape.db import make_sessionmaker

NOW = datetime(2026, 9, 29, 12, 30, tzinfo=timezone.utc)

TWELVE = {"meta": {"symbol": "XAU/USD", "interval": "1h"}, "status": "ok", "values": [
    {"datetime": "2026-09-29 12:00:00", "open": "3370", "high": "3375", "low": "3368", "close": "3372.40"},   # w trakcie
    {"datetime": "2026-09-29 11:00:00", "open": "3365", "high": "3371", "low": "3364", "close": "3370.00"},
    {"datetime": "2026-09-28 11:00:00", "open": "3300", "high": "3310", "low": "3299", "close": "3306.20"},
]}

OANDA = {"instrument": "XAG_USD", "granularity": "H1", "candles": [
    {"complete": True, "volume": 900, "time": "2026-09-29T10:00:00.000000000Z", "mid": {"o": "39.7", "h": "39.9", "l": "39.6", "c": "39.80"}},
    {"complete": True, "volume": 800, "time": "2026-09-29T11:00:00.000000000Z", "mid": {"o": "39.8", "h": "39.9", "l": "39.7", "c": "39.85"}},
    {"complete": False, "volume": 120, "time": "2026-09-29T12:00:00.000000000Z", "mid": {"o": "39.85", "h": "39.9", "l": "39.8", "c": "39.88"}},
]}


def recorder(payload):
    calls = []

    def opener(url, headers):
        calls.append((url, headers))
        return json.dumps(payload).encode()
    return opener, calls


def test_twelvedata_closed_candles_and_quote():
    op, calls = recorder(TWELVE)
    snap = market.TwelveData("KEY", op).fetch("XAU", NOW)
    assert "symbol=XAU%2FUSD" in calls[0][0] and "timezone=UTC" in calls[0][0] and "apikey=KEY" in calls[0][0]
    assert snap.candles == [(datetime(2026, 9, 28, 12, tzinfo=timezone.utc), 3306.2),
                            (datetime(2026, 9, 29, 12, tzinfo=timezone.utc), 3370.0)]       # bez świecy w trakcie
    assert snap.quote == (NOW, 3372.4)


def test_twelvedata_error_raises():
    op, _ = recorder({"code": 429, "message": "You have run out of API credits", "status": "error"})
    with pytest.raises(market.ProviderError, match="credits"):
        market.TwelveData("KEY", op).fetch("XAU", NOW)


def test_oanda_uses_bearer_and_complete_flag():
    op, calls = recorder(OANDA)
    snap = market.Oanda("TOKEN", "practice", op).fetch("XAG", NOW)
    url, headers = calls[0]
    assert url.startswith("https://api-fxpractice.oanda.com/v3/instruments/XAG_USD/candles?")
    assert headers["Authorization"] == "Bearer TOKEN"
    assert [p for _, p in snap.candles] == [39.8, 39.85] and snap.quote == (NOW, 39.88)
    with pytest.raises(ValueError):
        market.Oanda("T", "prod")


def test_goldapi_quote_only():
    op, _ = recorder({"name": "Gold", "price": 3371.9, "symbol": "XAU", "updatedAt": "2026-09-29T12:25:00Z"})
    snap = market.GoldApi(op).fetch("XAU", NOW)
    assert snap.candles == [] and snap.quote == (datetime(2026, 9, 29, 12, 25, tzinfo=timezone.utc), 3371.9)


def test_provider_from_env(monkeypatch):
    monkeypatch.delenv("TAPE_PRICE_PROVIDER", raising=False)
    assert market.provider_from_env() is None
    monkeypatch.setenv("TAPE_PRICE_PROVIDER", "twelvedata")
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    with pytest.raises(ValueError):
        market.provider_from_env()
    monkeypatch.setenv("TWELVEDATA_API_KEY", "k")
    assert market.provider_from_env().name == "twelvedata"


class Both:
    name = "twelvedata"

    def __init__(self, fail_xag=False):
        self.fail_xag = fail_xag

    def fetch(self, asset, now, bars=48):
        if asset == "XAG" and self.fail_xag:
            raise market.ProviderError("limit")
        op, _ = recorder(TWELVE)
        return market.TwelveData("k", op).fetch("XAU", now)


def test_run_once_is_idempotent_and_feeds_portfolio_and_quotes(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    Session = make_sessionmaker(url)
    with Session() as s:
        rep = market.run_once(s, Both(fail_xag=True), NOW)
        assert rep["XAU"] == 2 and str(rep["XAG"]).startswith("błąd")
        assert market.run_once(s, Both(fail_xag=True), NOW)["XAU"] == 0           # te same świece drugi raz
        assert market.mark(s, "XAU") == (NOW, 3372.4)
        q = market.quotes(s, NOW)[0]
    assert q["asset"] == "XAU" and q["age_minutes"] == 0
    assert q["change_24h"] == pytest.approx(3372.4 / 3306.2 - 1)

    from tape.api import create_app
    c = TestClient(create_app(url))
    assert c.get("/api/market/quotes").json()[0]["price"] == 3372.4
    csv = "Time,Deal,Symbol,Type,Direction,Volume,Price,Commission,Fee,Swap,Profit\n2026.09.29 10:00:00,1,XAUUSD,buy,in,0.10,3350,0,0,0,0\n"
    c.post("/api/imports", files={"file": ("d.csv", csv.encode(), "text/csv")})
    h = c.get("/api/portfolio").json()["holdings"][0]
    assert h["mark"] == 3372.4 and h["unrealized"] == pytest.approx(224.0)       # (3372.4 − 3350) × 10 oz
