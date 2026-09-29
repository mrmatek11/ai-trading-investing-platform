import base64
import io
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tape.api import create_app
from tape.secretbox import SecretBox, SecretError
from tape.sync import Connection, Mt5Payload, mt5_fills, run_due

from test_auth import LocalVerifier, ISSUER, token
from test_ibkr import FLEX

K1 = base64.b64encode(os.urandom(32)).decode()
K2 = base64.b64encode(os.urandom(32)).decode()


def box(spec=f"k1:{K1}"):
    return SecretBox.from_env(spec)


# ---- szyfrowanie ----

def test_roundtrip_and_context_binding():
    b = box()
    ct = b.encrypt(b"flex-token-123", "user_a|c1|ibkr_flex")
    assert b"flex-token-123" not in ct.encode() and ct.startswith("v1.k1.")
    assert b.decrypt(ct, "user_a|c1|ibkr_flex") == b"flex-token-123"
    with pytest.raises(SecretError):
        b.decrypt(ct, "user_b|c1|ibkr_flex")                 # przeniesienie do innego konta
    tampered = ct[:-4] + ("AAAA" if not ct.endswith("AAAA") else "BBBB")
    with pytest.raises(SecretError):
        b.decrypt(tampered, "user_a|c1|ibkr_flex")
    assert b.encrypt(b"x", "c") != b.encrypt(b"x", "c")      # losowy DEK i nonce


def test_rotation_and_missing_key():
    old = box(f"k1:{K1}")
    ct = old.encrypt(b"secret", "ctx")
    rotated = box(f"k2:{K2},k1:{K1}")
    assert rotated.decrypt(ct, "ctx") == b"secret"
    moved = rotated.rewrap(ct, "ctx")
    assert moved.startswith("v1.k2.") and box(f"k2:{K2}").decrypt(moved, "ctx") == b"secret"
    with pytest.raises(SecretError):
        box(f"k2:{K2}").decrypt(ct, "ctx")                    # stary klucz usunięty za wcześnie
    with pytest.raises(SecretError):
        SecretBox.from_env("k1:" + base64.b64encode(b"short").decode())
    assert SecretBox.from_env("") is None


# ---- MT5 payload ----

def test_mt5_payload_to_fills():
    p = Mt5Payload.model_validate({"gmt_offset": 10800, "deals": [
        {"ticket": 1, "time": 1789034400, "symbol": "", "type": "balance", "volume": 0, "price": 0, "profit": "5000",
         "sl": "0.00000", "contract_size": None},
        {"ticket": 10, "time": 1789034400, "symbol": "GOLD", "type": "buy", "entry": "in", "volume": "0.10",
         "price": "2650.10", "commission": "-0.35", "sl": "2640", "contract_size": "0.00"},
        {"ticket": 11, "time": 1789041600, "symbol": "GOLD", "type": "sell", "entry": "out", "volume": "0.10",
         "price": "2670.10", "commission": "-0.35", "swap": "-1.2", "profit": "200", "contract_size": 100},
    ]})
    fills, errors = mt5_fills(p)
    assert errors == [] and [f.external_id for f in fills] == ["10", "11"]
    buy, sell = fills
    assert buy.symbol == "XAUUSD" and buy.ts == datetime.fromtimestamp(1789034400 - 10800, tz=timezone.utc)
    assert buy.broker_pnl is None and str(buy.stop_loss) == "2640"
    assert str(sell.fee) == "-1.55" and str(sell.broker_pnl) == "200"


# ---- API ----

@pytest.fixture
def app_env(tmp_path, monkeypatch):
    calls = []

    def fetch(tok, q):
        calls.append((tok, q))
        return FLEX

    verify = LocalVerifier("https://unused/jwks.json", ISSUER, authorized_parties=("https://app.example.com",))
    url = f"sqlite:///{tmp_path / 's.db'}"
    c = TestClient(create_app(url, verifier=verify, secret_box=box(), flex_fetch=fetch))
    return c, calls, url


def hdr(sub):
    return {"Authorization": f"Bearer {token(sub)}"}


def test_ibkr_connection_sync_and_isolation(app_env):
    c, calls, url = app_env
    created = c.post("/api/connections/ibkr", headers=hdr("user_a"),
                     json={"label": "IBKR", "token": "123456789012345678901234", "query_id": "987654"})
    assert created.status_code == 201
    cid = created.json()["id"]
    assert "token" not in created.json() and created.json()["last_status"] == "never"

    # token w bazie tylko zaszyfrowany
    from tape.db import make_sessionmaker
    with make_sessionmaker(url)() as s:
        raw = s.scalars(select(Connection)).one().secret
    assert "123456789012345678901234" not in raw and "987654" not in raw

    # cudzy użytkownik nie widzi, nie synchronizuje, nie usuwa
    assert c.get("/api/connections", headers=hdr("user_b")).json()["connections"] == []
    assert c.post(f"/api/connections/{cid}/sync", headers=hdr("user_b")).status_code == 404
    assert c.delete(f"/api/connections/{cid}", headers=hdr("user_b")).status_code == 404

    rep = c.post(f"/api/connections/{cid}/sync", headers=hdr("user_a")).json()
    assert calls == [("123456789012345678901234", "987654")]
    assert rep["new"] == 3 and rep["connection"]["last_status"] == "partial"   # wiersz HOLD w raporcie
    assert c.post(f"/api/connections/{cid}/sync", headers=hdr("user_a")).status_code == 429  # limit IBKR
    assert len(c.get("/api/positions", headers=hdr("user_a")).json()) == 2
    assert c.get("/api/positions", headers=hdr("user_b")).json() == []

    # ręczny import tego samego raportu = same duplikaty (wspólne źródło „ibkr”)
    imp = c.post("/api/imports", headers=hdr("user_a"),
                 files={"file": ("flex.xml", io.BytesIO(FLEX), "application/xml")}).json()
    assert imp["new"] == 0 and imp["duplicates"] == 3

    assert c.delete(f"/api/connections/{cid}", headers=hdr("user_a")).json() == {"ok": True}
    assert c.get("/api/connections", headers=hdr("user_a")).json()["connections"] == []


def test_ibkr_requires_encryption_key(tmp_path, monkeypatch):
    monkeypatch.delenv("TAPE_SECRET_KEYS", raising=False)
    c = TestClient(create_app(f"sqlite:///{tmp_path / 'n.db'}"))
    r = c.post("/api/connections/ibkr", json={"token": "123456789012", "query_id": "1"})
    assert r.status_code == 503                               # odmowa zamiast zapisu jawnego tokenu
    assert c.get("/api/connections").json()["encryption"] is False


def test_mt5_push_token(app_env):
    c, _, url = app_env
    made = c.post("/api/connections/mt5", headers=hdr("user_a"), json={"label": "FTMO 100k"}).json()
    tok = made["token"]
    assert tok.startswith("tps_")
    assert "token" not in c.get("/api/connections", headers=hdr("user_a")).json()["connections"][0]

    deals = {"gmt_offset": 7200, "deals": [
        {"ticket": 10, "time": 1789034400, "symbol": "XAUUSD", "type": "buy", "entry": "in", "volume": 0.1, "price": 2650},
        {"ticket": 11, "time": 1789041600, "symbol": "XAUUSD", "type": "sell", "entry": "out", "volume": 0.1,
         "price": 2670, "profit": 200},
    ]}
    assert c.post("/api/ingest/mt5", json=deals).status_code == 401
    assert c.post("/api/ingest/mt5", json=deals, headers={"Authorization": "Bearer tps_wrong"}).status_code == 401
    assert c.post("/api/ingest/mt5", json=deals, headers=hdr("user_a")).status_code == 401   # sesja ≠ token EA
    ok = c.post("/api/ingest/mt5", json=deals, headers={"Authorization": f"Bearer {tok}"}).json()
    assert ok["new"] == 2
    again = c.post("/api/ingest/mt5", json=deals, headers={"Authorization": f"Bearer {tok}"}).json()
    assert again == {"new": 0, "duplicates": 2, "errors": []}
    assert c.get("/api/positions", headers=hdr("user_a")).json()[0]["net_pnl"] == 200
    assert c.get("/api/positions", headers=hdr("user_b")).json() == []
    bad = c.post("/api/ingest/mt5", content=b"{not json", headers={"Authorization": f"Bearer {tok}"})
    assert bad.status_code == 422

    c.delete(f"/api/connections/{made['id']}", headers=hdr("user_a"))
    assert c.post("/api/ingest/mt5", json=deals, headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_worker_syncs_only_due_connections(tmp_path):
    from tape.db import make_sessionmaker
    from tape.sync import create_ibkr

    Session = make_sessionmaker(f"sqlite:///{tmp_path / 'w.db'}")
    b = box()
    now = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
    with Session() as s:
        fresh = create_ibkr(s, b, "u1", "a", "111111111111", "1")
        stale = create_ibkr(s, b, "u2", "b", "222222222222", "2")
        broken = create_ibkr(s, b, "u3", "c", "333333333333", "3")
        fresh.last_sync_at = now - timedelta(minutes=10)
        stale.last_sync_at = now - timedelta(hours=2)
        s.commit()

        def fetch(tok, q):
            if q == "3":
                raise RuntimeError("IBKR odrzucił zapytanie: Token has expired")
            return FLEX

        out = {r["id"]: r for r in run_due(s, b, timedelta(hours=1), fetch, now)}
        assert fresh.id not in out and out[stale.id]["new"] == 3
        assert out[broken.id]["status"] == "error"
        assert "Token has expired" in broken.last_error and "333333333333" not in broken.last_error


def test_secret_copied_to_other_account_does_not_decrypt(tmp_path):
    """Ktoś z dostępem zapisu do bazy przepisuje zaszyfrowany token cudzego konta do swojego połączenia."""
    from tape.db import make_sessionmaker
    from tape.sync import create_ibkr, sync_ibkr

    Session = make_sessionmaker(f"sqlite:///{tmp_path / 'x.db'}")
    b = box()
    with Session() as s:
        victim = create_ibkr(s, b, "victim", "v", "999999999999", "9")
        attacker = create_ibkr(s, b, "attacker", "a", "000000000000", "0")
        attacker.secret = victim.secret
        s.commit()
        seen = []
        rep = sync_ibkr(s, attacker, b, lambda tok, q: seen.append(tok) or FLEX)
        assert seen == [] and rep["new"] == 0 and attacker.last_status == "error"
