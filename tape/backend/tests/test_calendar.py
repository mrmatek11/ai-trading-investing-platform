import io
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from tape import econ_calendar as cal
from tape.engine import stats
from tape.engine.positions import build_positions
from tape.importers.base import Fill
from decimal import Decimal as D


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


FF = json.dumps([
    {"title": "CPI m/m", "country": "USD", "date": "2026-09-10T08:30:00-04:00", "impact": "High", "forecast": "0.3%", "previous": "0.2%"},
    {"title": "Bank Holiday", "country": "GBP", "date": "2026-09-11T03:00:00-04:00", "impact": "Holiday"},
    {"title": "ECB Press Conference", "country": "EUR", "date": "2026-09-10T08:45:00-04:00", "impact": "High"},
    {"title": "Unemployment Claims", "country": "USD", "date": "2026-09-10T08:30:00-04:00", "impact": "Medium"},
]).encode()


def test_fomc_seed_uses_new_york_time():
    ts = {e["ts"].date(): e["ts"] for e in cal.fomc_seed()}
    assert ts[utc(2026, 1, 28).date()] == utc(2026, 1, 28, 19)       # EST
    assert ts[utc(2026, 9, 16).date()] == utc(2026, 9, 16, 18)       # EDT


def test_parse_forexfactory_and_near():
    ev = cal.parse_forexfactory(FF)
    assert [e["title"] for e in ev] == ["CPI m/m", "ECB Press Conference", "Unemployment Claims"]
    assert ev[0]["ts"] == utc(2026, 9, 10, 12, 30) and ev[0]["impact"] == "high"
    times = [utc(2026, 9, 10, 12, 30)]
    assert cal.near(times, utc(2026, 9, 10, 12, 5)) and cal.near(times, utc(2026, 9, 10, 13, 0))
    assert not cal.near(times, utc(2026, 9, 10, 13, 1)) and not cal.near([], utc(2026, 9, 10))


def test_relevant_filters_usd_and_dedupes(tmp_path):
    from tape.db import make_sessionmaker

    S = make_sessionmaker(f"sqlite:///{tmp_path / 'c.db'}")
    with S() as s:
        assert cal.add_events(s, "forexfactory", cal.parse_forexfactory(FF)) == 3
        assert cal.add_events(s, "forexfactory", cal.parse_forexfactory(FF)) == 0     # idempotentnie
        cal.add_events(s, "csv", [{"ts": utc(2026, 9, 10, 12, 30), "country": "USD", "title": "CPI m/m", "impact": "high"}])
        s.commit()
        hi = cal.relevant(s, utc(2026, 9, 10), utc(2026, 9, 11))
        assert [r.title for r in hi] == ["CPI m/m"]                                    # bez EUR, bez duplikatu z CSV
        assert len(cal.relevant(s, utc(2026, 9, 10), utc(2026, 9, 11), impact="medium")) == 2


def _pair(i, open_ts, pnl):
    return [Fill(f"{i}a", open_ts, "XAUUSD", "buy", D(1), D(2000), D(1)),
            Fill(f"{i}b", open_ts + timedelta(minutes=20), "XAUUSD", "sell", D(1), D(2000 + pnl), D(1))]


def test_news_window_segment():
    news = [utc(2026, 9, d, 12, 30) for d in range(1, 29)]
    fills = []
    for d in range(1, 13):
        fills += _pair(f"n{d}", utc(2026, 9, d, 12, 20), -50)          # wejścia 10 min przed danymi — straty
        fills += _pair(f"q{d}", utc(2026, 9, d, 3, 0), 40 + d)          # spokojne godziny — zyski
    seg = {s.key: s for s in stats.segments(build_positions(fills), news) if s.group == "news_window"}
    hot = seg["±30 min od ważnych danych"]
    assert hot.trades == 12 and hot.net_pnl == -600 and hot.significant
    assert not any(s.group == "news_window" for s in stats.segments(build_positions(fills)))


def test_api_calendar_and_trade_events(tmp_path, monkeypatch):
    from tape.api import create_app
    from test_auth import ISSUER, LocalVerifier, token

    monkeypatch.setenv("TAPE_ADMIN_SUBS", "admin_1")
    verify = LocalVerifier("https://unused/jwks.json", ISSUER, authorized_parties=("https://app.example.com",))
    c = TestClient(create_app(f"sqlite:///{tmp_path / 'a.db'}", verifier=verify))
    user = {"Authorization": f"Bearer {token('user_a')}"}
    admin = {"Authorization": f"Bearer {token('admin_1')}"}
    csv = b"time,country,title,impact\n2026-09-10 12:30:00,USD,CPI y/y,high\n"
    assert c.post("/api/calendar", headers=user, files={"file": ("c.csv", io.BytesIO(csv), "text/csv")}).status_code == 403
    assert c.post("/api/calendar", headers=admin, files={"file": ("c.csv", io.BytesIO(csv), "text/csv")}).json()["added"] == 1
    bad = b"time,country,title,impact\n2026-09-10 12:30:00,USD,CPI,huge\n"
    assert c.post("/api/calendar", headers=admin, files={"file": ("c.csv", io.BytesIO(bad), "text/csv")}).status_code == 400

    deals = ("Time,Deal,Symbol,Type,Direction,Volume,Price,Commission,Fee,Swap,Profit\n"
             "2026.09.10 15:25:00,1,XAUUSD,buy,in,0.10,2650,0,0,0,0\n"
             "2026.09.10 16:00:00,2,XAUUSD,sell,out,0.10,2640,0,0,0,-100\n")          # 12:25 i 13:00 UTC
    c.post("/api/imports", headers=user, files={"file": ("d.csv", io.BytesIO(deals.encode()), "text/csv")})
    key = c.get("/api/positions", headers=user).json()[0]["key"]
    events = c.get(f"/api/positions/{key}", headers=user).json()["events"]
    assert [e["title"] for e in events] == ["CPI y/y"]
    assert isinstance(c.get("/api/calendar", headers=user).json()["events"], list)
