import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tape.api import create_app
from tape.review import Action, Finding, ReviewOutput, validate


def deals_csv(n, start=1, month=8):
    lines = ["Time,Deal,Symbol,Type,Direction,Volume,Price,Commission,Fee,Swap,Profit"]
    d = start * 10
    for i in range(n):
        win = i % 3 != 0
        pnl = 150 if win else -100
        day = 1 + i % 27
        lines.append(f"2026.{month:02d}.{day:02d} 10:{i % 60:02d}:00,{d},XAUUSD,buy,in,0.10,2600,0,0,0,0")
        lines.append(f"2026.{month:02d}.{day:02d} 12:{i % 60:02d}:00,{d + 1},XAUUSD,sell,out,0.10,{2600 + pnl / 10},0,0,0,{pnl}")
        d += 2
    return "\n".join(lines).encode()


class FakeMessages:
    def __init__(self, output):
        self.output, self.calls = output, []

    def parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=self.output)


def fake_output():
    return ReviewOutput(
        headline="Proces działa, ale próba jest mała",
        strengths=[Finding(title="Dodatni wynik", detail="Wynik netto 950.00 USD przy 20 transakcjach.", facts=["F1"])],
        leaks=[
            Finding(title="Zmyślona liczba", detail="Tracisz 1234.56 USD na piątkach.", facts=["F1"]),
            Finding(title="Bez faktu", detail="Za dużo tradujesz.", facts=[]),
        ],
        actions=[Action(text="Dodaj regułę: maksymalnie 3 transakcje dziennie.", facts=["F1"])],
    )


def test_validate_drops_unsupported_numbers():
    facts = [{"id": "F1", "text": "Zamkniętych transakcji: 20; wynik netto 950.00 USD"}]
    out = validate(fake_output(), facts)
    assert [f["title"] for f in out["strengths"]] == ["Dodatni wynik"]
    assert out["leaks"] == [] and out["dropped"] == 2
    assert len(out["actions"]) == 1                              # „3” to liczba porządkowa, nie statystyka


@pytest.fixture
def setup(tmp_path):
    msgs = FakeMessages(fake_output())
    ai = SimpleNamespace(beta=SimpleNamespace(messages=msgs))
    c = TestClient(create_app(f"sqlite:///{tmp_path / 'r.db'}", ai_client=ai))
    return c, msgs


def upload(c, data, name="d.csv"):
    return c.post("/api/imports", files={"file": (name, io.BytesIO(data), "text/csv")}).json()


def test_review_flow_caches_by_facts(setup):
    c, msgs = setup
    upload(c, deals_csv(5, month=6))
    assert c.post("/api/review").status_code == 400                # za mało transakcji
    upload(c, deals_csv(20, start=100))
    r = c.post("/api/review")
    assert r.status_code == 200, r.text
    body = r.json()
    call = msgs.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    listing = call["messages"][0]["content"]
    assert "F1: Zamkniętych transakcji: 25" in listing
    assert body["stale"] is False and body["dropped"] >= 1 and body["facts"][0]["id"] == "F1"

    assert c.post("/api/review").json()["created_at"] == body["created_at"]   # te same fakty → bez nowego wywołania
    assert len(msgs.calls) == 1
    assert c.get("/api/review").json()["review"]["stale"] is False

    upload(c, deals_csv(3, start=500, month=9))                             # nowe dane → przegląd nieaktualny
    assert c.get("/api/review").json()["review"]["stale"] is True
    assert c.post("/api/review").status_code == 429                # limit częstotliwości


def test_review_without_ai(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = TestClient(create_app(f"sqlite:///{tmp_path / 'n.db'}"))
    assert c.get("/api/review").json()["ai_available"] is False
    assert c.post("/api/review").status_code == 503


def test_review_isolation(tmp_path):
    from test_auth import ISSUER, LocalVerifier, token

    msgs = FakeMessages(fake_output())
    verify = LocalVerifier("https://unused/jwks.json", ISSUER, authorized_parties=("https://app.example.com",))
    c = TestClient(create_app(f"sqlite:///{tmp_path / 'i.db'}", verifier=verify,
                              ai_client=SimpleNamespace(beta=SimpleNamespace(messages=msgs))))
    a = {"Authorization": f"Bearer {token('user_a')}"}
    b = {"Authorization": f"Bearer {token('user_b')}"}
    c.post("/api/imports", headers=a, files={"file": ("d.csv", io.BytesIO(deals_csv(12)), "text/csv")})
    assert c.post("/api/review", headers=a).status_code == 200
    assert c.get("/api/review", headers=b).json()["review"] is None
    assert c.post("/api/review", headers=b).status_code == 400
