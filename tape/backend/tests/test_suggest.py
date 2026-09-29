import io
from types import SimpleNamespace

from fastapi.testclient import TestClient

from tape.api import create_app
from tape.importers import suggest as sg

CSV = "Trade ID;When;Instrument;Action;Qty;Fill Price;Costs\nA1;2026-09-01 10:00:00;XAUUSD;Kupno;1;2600;-2\nA2;2026-09-01 11:00:00;XAUUSD;Sprzedaż;1;2610;-2\n"


def test_heuristic_maps_common_aliases():
    cols = sg.heuristic(["trade_id", "when", "instrument", "action", "qty", "fill_price", "costs"])
    assert cols == {"id": "trade_id", "symbol": "instrument", "side": "action", "qty": "qty",
                    "price": "fill_price", "fee": "costs"}          # „when” nie jest aliasem czasu


class FakeAI:
    def __init__(self, fields):
        self.fields = fields

    @property
    def beta(self):
        return SimpleNamespace(messages=self)

    def parse(self, **kwargs):
        out = sg.MappingSuggestion(fields=[sg.FieldChoice(field=f, column=c, confidence=0.9) for f, c in self.fields],
                                   tz="Europe/Warsaw", date_format="%Y-%m-%d %H:%M:%S",
                                   buy_values=["Kupno"], sell_values=["Sprzedaż"], notes="")
        return SimpleNamespace(parsed_output=out, stop_reason="end_turn")


def test_ai_fills_gaps_and_invented_columns_are_dropped():
    ai = FakeAI([("id", "trade_id"), ("time", "when"), ("symbol", "instrument"), ("side", "action"),
                 ("qty", "qty"), ("price", "fill_price"), ("fee", "commission_total")])   # nie ma takiej kolumny
    res = sg.suggest(["trade_id", "when", "instrument", "action", "qty", "fill_price", "costs"], [], ai)
    assert res["source"] == "ai" and res["columns"]["time"] == "when"
    assert "fee" not in res["columns"] and res["missing"] == []


def test_suggest_then_import_with_mapping(tmp_path):
    ai = FakeAI([("id", "trade_id"), ("time", "when"), ("symbol", "instrument"), ("side", "action"),
                 ("qty", "qty"), ("price", "fill_price"), ("fee", "costs")])
    client = TestClient(create_app(f"sqlite:///{tmp_path / 't.db'}", ai_client=ai))
    files = {"file": ("x.csv", io.BytesIO(CSV.encode()), "text/csv")}
    body = client.post("/api/imports/suggest", files=files).json()
    assert body["rows"] == 2 and body["ai_available"] and body["suggestion"]["missing"] == []
    s = body["suggestion"]
    mapping = {"columns": s["columns"], "tz": s["tz"], "date_format": s["date_format"],
               "buy_values": s["buy_values"], "sell_values": s["sell_values"]}
    import json
    files = {"file": ("x.csv", io.BytesIO(CSV.encode()), "text/csv")}
    rep = client.post("/api/imports", files=files, data={"mapping": json.dumps(mapping)}).json()
    assert rep["new"] == 2 and rep["errors"] == []
    pos = client.get("/api/positions").json()
    assert len(pos) == 1 and pos[0]["net_pnl"] == 996.0       # (2610-2600)*1*100 − 4


def test_suggest_without_ai_returns_heuristic(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = TestClient(create_app(f"sqlite:///{tmp_path / 't.db'}"))
    files = {"file": ("x.csv", io.BytesIO(CSV.encode()), "text/csv")}
    body = client.post("/api/imports/suggest", files=files).json()
    assert body["ai_available"] is False and body["suggestion"]["missing"] == ["time"]
