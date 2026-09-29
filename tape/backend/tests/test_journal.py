import io
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tape.api import create_app
from tape.engine.positions import build_positions
from tape.importers import parse_file

# MT5 nie podaje SL → R pojawia się dopiero po wpisaniu SL w journalu
MT5_CSV = """Time,Deal,Symbol,Type,Direction,Volume,Price,Commission,Fee,Swap,Profit
2026.09.10 10:00:00,1,XAUUSD,buy,in,0.10,2650.00,0,0,0,0
2026.09.10 12:00:00,2,XAUUSD,sell,out,0.10,2670.00,0,0,0,200.00
2026.09.11 10:00:00,3,XAUUSD,sell,in,0.10,2680.00,0,0,0,0
2026.09.11 11:00:00,4,XAUUSD,buy,out,0.10,2690.00,0,0,0,-100.00
"""


def client_with_trades(tmp_path):
    c = TestClient(create_app(f"sqlite:///{tmp_path / 'j.db'}"))
    c.post("/api/imports", files={"file": ("d.csv", io.BytesIO(MT5_CSV.encode()), "text/csv")})
    return c


def test_position_key_is_stable_across_rebuilds():
    fills = parse_file(MT5_CSV.encode(), "d.csv").fills
    a = [p.key for p in build_positions(fills)]
    b = [p.key for p in build_positions(list(reversed(fills)))]
    assert a == b and len(set(a)) == 2


def test_journal_entry_sets_r_and_validates_stop_side(tmp_path):
    c = client_with_trades(tmp_path)
    long_pos = next(p for p in c.get("/api/positions").json() if p["direction"] == "long")
    assert long_pos["r_multiple"] is None

    bad = c.put(f"/api/positions/{long_pos['key']}/journal", json={"initial_stop": 2660})   # nad wejściem longa
    assert bad.status_code == 400 and "poniżej" in bad.json()["detail"]

    ok = c.put(f"/api/positions/{long_pos['key']}/journal",
               json={"initial_stop": 2640, "notes": "Czyste wybicie", "mistakes": ["Za wczesne wyjście"]})
    assert ok.status_code == 200
    detail = c.get(f"/api/positions/{long_pos['key']}").json()
    # 1R = (2650 − 2640) × 0,1 × 100 = 100 USD → +200 USD = +2R
    assert detail["position"]["r_multiple"] == 2.0
    assert detail["journal"]["notes"] == "Czyste wybicie" and len(detail["fills"]) == 2
    assert c.get("/api/positions/nieistnieje").status_code == 404


def test_setups_checklist_and_mistake_costs(tmp_path):
    c = client_with_trades(tmp_path)
    sid = c.post("/api/setups", json={"name": "London breakout", "rules": ["Wybicie zakresu azjatyckiego", "Brak newsa ±30 min"]}).json()["id"]
    assert c.post("/api/setups", json={"name": "London breakout"}).status_code == 409
    pos = {p["direction"]: p for p in c.get("/api/positions").json()}
    c.put(f"/api/positions/{pos['long']['key']}/journal",
          json={"setup_id": sid, "checklist": {"Wybicie zakresu azjatyckiego": True}})
    c.put(f"/api/positions/{pos['short']['key']}/journal",
          json={"mistakes": ["Wejście po stracie (revenge)", "Brak setupu / planu"]})
    assert c.put(f"/api/positions/{pos['short']['key']}/journal", json={"setup_id": 999}).status_code == 400

    stats = c.get("/api/stats").json()
    by_setup = {g["key"]: g for g in stats["setups"]}
    assert by_setup["London breakout"]["net_pnl"] == 200 and by_setup["Bez setupu"]["net_pnl"] == -100
    assert {m["key"]: m["net_pnl"] for m in stats["mistakes"]} == {
        "Wejście po stracie (revenge)": -100, "Brak setupu / planu": -100}

    setups = c.get("/api/setups").json()
    assert setups[0]["stats"]["trades"] == 1 and setups[0]["rules"][1] == "Brak newsa ±30 min"
    listed = {p["direction"]: p for p in c.get("/api/positions").json()}
    assert listed["long"]["setup"] == "London breakout" and listed["short"]["mistakes"]

    assert c.delete(f"/api/setups/{sid}").status_code == 200
    assert c.get(f"/api/positions/{pos['long']['key']}").json()["journal"]["setup_id"] is None
    assert "FOMO — wejście za późno" in c.get("/api/journal/meta").json()["mistakes"]


def test_detail_includes_prices_around_trade(tmp_path):
    c = client_with_trades(tmp_path)
    csv = "timestamp,close\n2026-09-09 12:00:00,2640\n2026-09-10 09:00:00,2648\n2026-09-10 11:00:00,2660\n2026-08-01 00:00:00,2500\n"
    c.post("/api/prices", files={"file": ("p.csv", io.BytesIO(csv.encode()), "text/csv")}, data={"asset": "XAU"})
    long_pos = next(p for p in c.get("/api/positions").json() if p["direction"] == "long")
    prices = c.get(f"/api/positions/{long_pos['key']}").json()["prices"]
    assert [x["p"] for x in prices] == [2640, 2648, 2660]      # sierpień poza oknem
    assert datetime.fromisoformat(prices[0]["t"]) == datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
