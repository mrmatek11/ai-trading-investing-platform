import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from fastapi.testclient import TestClient

from tape.engine import prop
from tape.engine.positions import build_positions
from tape.importers.base import Fill

RULES = prop.PropRules(D(100000), D(5), D(10), "static", D(10), "Europe/Prague")


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def trade(i, closed, pnl):
    return [Fill(f"{i}a", closed - timedelta(minutes=30), "XAUUSD", "buy", D(1), D(2000), D(1)),
            Fill(f"{i}b", closed, "XAUUSD", "sell", D(1), D(2000) + D(pnl), D(1))]


def test_yesterdays_loss_does_not_eat_todays_limit():
    pos = build_positions(trade(1, utc(2026, 9, 28, 14), -4000))
    st = prop.today_status(pos, RULES, utc(2026, 9, 29, 9))
    assert st.day.isoformat() == "2026-09-29" and st.today_pnl == 0
    assert st.daily_left == D(5000)                       # pełny limit na dziś
    assert st.overall_left == D(6000) and st.level == "ok"


def test_levels_today():
    now = utc(2026, 9, 29, 15)
    warn = prop.today_status(build_positions(trade(1, utc(2026, 9, 29, 10), -2600)), RULES, now)
    assert warn.daily_left == D(2400) and warn.level == "warn"          # zostało 48% dziennego limitu
    danger = prop.today_status(build_positions(trade(1, utc(2026, 9, 29, 10), -4000)), RULES, now)
    assert danger.level == "danger"
    gone = prop.today_status(build_positions(trade(1, utc(2026, 9, 29, 10), -5000)), RULES, now)
    assert gone.level == "breached" and gone.daily_left == 0


def test_day_boundary_uses_firm_timezone():
    # 23:30 UTC 28.09 = 01:30 czasu praskiego 29.09 → strata liczy się do „dziś”
    st = prop.today_status(build_positions(trade(1, utc(2026, 9, 28, 23, 30), -3000)), RULES, utc(2026, 9, 29, 9))
    assert st.today_pnl == D(-3000) and st.daily_left == D(2000)


def test_api_prop_accounts_per_book(tmp_path):
    from tape.api import create_app
    from test_auth import ISSUER, LocalVerifier, token

    verify = LocalVerifier("https://unused/jwks.json", ISSUER, authorized_parties=("https://app.example.com",))
    c = TestClient(create_app(f"sqlite:///{tmp_path / 'p.db'}", verifier=verify))
    a = {"Authorization": f"Bearer {token('user_a')}"}
    b = {"Authorization": f"Bearer {token('user_b')}"}
    today = datetime.now(timezone.utc).strftime("%Y.%m.%d")
    csv = ("Time,Deal,Symbol,Type,Direction,Volume,Price,Commission,Fee,Swap,Profit\n"
           f"{today} 03:05:00,1,XAUUSD,buy,in,1,2650,0,0,0,0\n{today} 03:10:00,2,XAUUSD,sell,out,1,2620,0,0,0,-3000\n")
    c.post("/api/imports", headers=a, data={"book": "FTMO"}, files={"file": ("d.csv", io.BytesIO(csv.encode()), "text/csv")})
    c.post("/api/imports", headers=a, data={"book": "Inne"}, files={"file": ("d.csv", io.BytesIO(csv.encode()), "text/csv")})
    body = {"book": "FTMO", "name": "FTMO 100k", "initial_balance": 100000, "daily_loss_pct": 5, "max_drawdown_pct": 10,
            "drawdown_type": "static", "profit_target_pct": 10, "day_tz": "UTC"}
    pid = c.put("/api/prop/accounts", headers=a, json=body).json()["id"]
    assert c.put("/api/prop/accounts", headers=a, json={**body, "name": "FTMO 100k v2"}).json()["id"] == pid   # upsert
    st = c.get("/api/prop/accounts", headers=a).json()
    assert len(st) == 1 and st[0]["name"] == "FTMO 100k v2"
    assert st[0]["today_pnl"] == -3000 and st[0]["daily_left"] == 2000 and st[0]["level"] == "warn"   # tylko konto FTMO
    assert c.get("/api/prop/accounts", headers=b).json() == []
    assert c.delete(f"/api/prop/accounts/{pid}", headers=b).status_code == 404
    assert c.put("/api/prop/accounts", headers=a, json={**body, "day_tz": "Mars/Base"}).status_code == 400
    assert c.delete(f"/api/prop/accounts/{pid}", headers=a).json() == {"ok": True}
