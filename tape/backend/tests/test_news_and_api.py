import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from tape.api import create_app
from tape.news import classify
from tape.news.bias import Event, Impact, aggregate
from tape.news.sample import sample_events

NOW = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)


def ev(i, direction, magnitude=3, age_h=1.0, novelty="new", horizon="days"):
    return Event(f"e{i}", f"event {i}", "conflict", 0, 0, NOW - timedelta(hours=age_h),
                 {"XAU": Impact(direction, magnitude, horizon)}, novelty=novelty, confidence=1.0)


def test_bias_direction_and_shrinkage():
    strong = aggregate([ev(i, 1, 5) for i in range(6)], "XAU", NOW)
    assert strong.label == "long" and strong.strength == "silne"
    single_weak = aggregate([ev(0, 1, 1)], "XAU", NOW)
    assert single_weak.label == "neutral"            # jeden słaby news nie daje „long”
    mixed = aggregate([ev(0, 1, 4), ev(1, -1, 4)], "XAU", NOW)
    assert mixed.score == 0 and mixed.label == "neutral"


def test_bias_decay_and_future_events():
    old = aggregate([ev(0, 1, 5, age_h=24 * 20)], "XAU", NOW)
    assert abs(old.score) < 0.05
    future = aggregate([ev(0, 1, 5, age_h=-5)], "XAU", NOW)
    assert future.events_used == 0 and future.score == 0
    known = aggregate([ev(0, 1, 5, novelty="already_known")], "XAU", NOW)
    fresh = aggregate([ev(0, 1, 5, novelty="new")], "XAU", NOW)
    assert known.score < fresh.score


def test_sample_events_are_flagged():
    assert all(e.sample for e in sample_events(NOW))


class FakeMessages:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.parsed, self.stop_reason, self.calls = parsed, stop_reason, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(parsed_output=self.parsed, stop_reason=self.stop_reason)


def fake_client(parsed, stop_reason="end_turn"):
    msgs = FakeMessages(parsed, stop_reason)
    return SimpleNamespace(beta=SimpleNamespace(messages=msgs)), msgs


def classification(quote):
    impact = classify.AssetImpact(direction=1, magnitude=4, horizon="days", channel="safe_haven", reasoning="…")
    return classify.EventClassification(
        title="Incydent w cieśninie Ormuz", category="conflict", place="Zatoka Perska", lat=26.6, lon=56.3,
        xau=impact, xag=impact, novelty="new", confidence=0.8, summary="…",
        evidence=[classify.Evidence(quote=quote, source_index=0)])


ARTICLES = [classify.Article(title="Tanker hit", text="A tanker was struck near the Strait of Hormuz on Monday.",
                             url="https://example.com/a", published_at=NOW - timedelta(hours=1))]


def test_classify_builds_event_and_uses_cache_and_fallback():
    client, msgs = fake_client(classification("tanker was struck near the Strait"))
    event = classify.classify_cluster(client, "c1", ARTICLES)
    assert event is not None and event.impacts["XAU"].direction == 1 and event.sources == 1
    call = msgs.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["fallbacks"] == "default"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["output_format"] is classify.EventClassification


def test_classify_rejects_unverifiable_evidence_and_refusals():
    client, _ = fake_client(classification("gold jumped 5% on the news"))   # cytatu nie ma w artykule
    assert classify.classify_cluster(client, "c1", ARTICLES) is None
    client, _ = fake_client(None, stop_reason="refusal")
    assert classify.classify_cluster(client, "c1", ARTICLES) is None


MT5_CSV = """Time,Deal,Symbol,Type,Direction,Volume,Price,Commission,Fee,Swap,Profit
2026.09.10 10:00:00,1,XAUUSD,buy,in,0.10,2650.00,0,0,0,0
2026.09.10 12:00:00,2,XAUUSD,sell,out,0.10,2660.00,-0.70,0,0,100.00
"""


def test_api_import_is_idempotent_and_feeds_stats(tmp_path):
    client = TestClient(create_app(f"sqlite:///{tmp_path / 'tape.db'}"))
    assert client.get("/api/health").status_code == 200
    files = {"file": ("deals.csv", io.BytesIO(MT5_CSV.encode()), "text/csv")}
    first = client.post("/api/imports", files=files).json()
    assert first["detected"] == "mt5" and first["new"] == 2 and first["duplicates"] == 0
    files = {"file": ("deals.csv", io.BytesIO(MT5_CSV.encode()), "text/csv")}
    again = client.post("/api/imports", files=files).json()
    assert again["new"] == 0 and again["duplicates"] == 2
    positions = client.get("/api/positions").json()
    assert len(positions) == 1 and positions[0]["net_pnl"] == 99.3
    s = client.get("/api/stats").json()
    assert s["summary"]["trades"] == 1 and len(s["equity"]) == 1


def test_api_events_bias_and_token(tmp_path, monkeypatch):
    monkeypatch.setenv("TAPE_API_TOKEN", "secret")
    client = TestClient(create_app(f"sqlite:///{tmp_path / 'tape.db'}"))
    assert client.get("/api/events").status_code == 401
    h = {"Authorization": "Bearer secret"}
    events = client.get("/api/events", headers=h).json()
    assert events and all(e["sample"] for e in events)
    bias = client.get("/api/bias", headers=h).json()
    assert set(bias) >= {"XAU", "XAG", "track_record"}
    assert bias["track_record"]["available"] is False
