import base64
import hashlib
import hmac
import io
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from tape.api import create_app
from tape.auth import AuthError, JwksVerifier

ISSUER = "https://auth.example.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC = KEY.public_key()
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(sub="user_a", key=KEY, alg="RS256", **overrides):
    now = int(time.time())
    claims = {"sub": sub, "iss": ISSUER, "iat": now, "exp": now + 300, "azp": "https://app.example.com"}
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm=alg)


class LocalVerifier(JwksVerifier):
    """Prawdziwa weryfikacja, tylko klucz podany lokalnie zamiast pobierania JWKS przez sieć."""

    def signing_key(self, tok):
        return PUBLIC


@pytest.fixture
def verify():
    return LocalVerifier("https://unused/jwks.json", ISSUER, authorized_parties=("https://app.example.com",))


def test_valid_token_gives_sub(verify):
    assert verify(token("user_42")) == "user_42"


@pytest.mark.parametrize("bad", [
    lambda: token(exp=int(time.time()) - 3600),                  # wygasły
    lambda: token(iss="https://evil.example.com"),               # obcy wystawca
    lambda: token(key=OTHER_KEY),                                 # zły podpis
    lambda: token(azp="https://evil.example.com"),               # obcy frontend
    lambda: jwt.encode({"sub": "x", "iss": ISSUER, "iat": 0, "exp": 9999999999}, None, algorithm="none"),
    lambda: hs256_forged_with_public_key(),                       # podrabianie kluczem publicznym jako sekretem
])
def test_rejects_bad_tokens(verify, bad):
    with pytest.raises(AuthError):
        verify(bad())


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def hs256_forged_with_public_key() -> str:
    """Atak „algorithm confusion” złożony ręcznie (PyJWT sam nie pozwala zbudować takiego tokenu)."""
    now = int(time.time())
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(json.dumps({"sub": "attacker", "iss": ISSUER, "iat": now, "exp": now + 60}).encode())
    secret = PUBLIC.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    sig = b64(hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


MT5_CSV = """Time,Deal,Symbol,Type,Direction,Volume,Price,Commission,Fee,Swap,Profit
2026.09.10 10:00:00,1,XAUUSD,buy,in,0.10,2650.00,0,0,0,0
2026.09.10 12:00:00,2,XAUUSD,sell,out,0.10,2670.00,0,0,0,200.00
"""


def test_users_are_isolated_and_account_param_is_ignored(tmp_path, verify, monkeypatch):
    monkeypatch.setenv("TAPE_ADMIN_SUBS", "admin_1")
    c = TestClient(create_app(f"sqlite:///{tmp_path / 'a.db'}", verifier=verify))
    a = {"Authorization": f"Bearer {token('user_a')}"}
    b = {"Authorization": f"Bearer {token('user_b')}"}

    assert c.get("/api/health").status_code == 200
    assert c.get("/api/positions").status_code == 401
    assert c.get("/api/positions", headers={"Authorization": "Bearer garbage"}).status_code == 401

    rep = c.post("/api/imports", headers=a, files={"file": ("d.csv", io.BytesIO(MT5_CSV.encode()), "text/csv")},
                 data={"account": "user_b"}).json()                      # próba zapisu do cudzego konta
    assert rep["new"] == 2
    assert len(c.get("/api/positions", headers=a).json()) == 1
    assert c.get("/api/positions", headers=b).json() == []
    assert c.get("/api/positions?account=user_a", headers=b).json() == []  # parametr ignorowany
    key = c.get("/api/positions", headers=a).json()[0]["key"]
    assert c.get(f"/api/positions/{key}", headers=b).status_code == 404
    assert c.put(f"/api/positions/{key}/journal", headers=b, json={"notes": "x"}).status_code == 404
    assert c.get("/api/stats", headers=b).json()["summary"]["trades"] == 0

    sid = c.post("/api/setups", headers=a, json={"name": "A setup"}).json()["id"]
    assert c.get("/api/setups", headers=b).json() == []
    assert c.delete(f"/api/setups/{sid}", headers=b).status_code == 404

    prices = {"file": ("p.csv", io.BytesIO(b"timestamp,close\n2026-01-01 00:00:00,2600\n"), "text/csv")}
    assert c.post("/api/prices", headers=a, files=prices).status_code == 403
    admin = {"Authorization": f"Bearer {token('admin_1')}"}
    prices = {"file": ("p.csv", io.BytesIO(b"timestamp,close\n2026-01-01 00:00:00,2600\n"), "text/csv")}
    assert c.post("/api/prices", headers=admin, files=prices).json()["added"] == 1
