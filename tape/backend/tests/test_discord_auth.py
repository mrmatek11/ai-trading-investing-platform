import json
import time
import urllib.parse

import jwt
import pytest
from fastapi.testclient import TestClient

from tape import discord_auth
from tape.api import create_app
from test_auth import ISSUER, LocalVerifier, token

APP = "https://app.example.com"
SECRET = "s" * 40
CFG = discord_auth.DiscordConfig("123456", "discord-client-secret", APP, SECRET)


class FakeDiscord:
    def __init__(self, user_id="80351110224678912", token_status=200):
        self.user_id, self.token_status, self.calls = user_id, token_status, []

    def __call__(self, method, url, headers, data):
        self.calls.append((method, url, headers, data))
        if url == discord_auth.TOKEN_URL:
            form = urllib.parse.parse_qs(data.decode())
            if self.token_status != 200 or form["code"] != ["good-code"]:
                return 400, json.dumps({"error": "invalid_grant"}).encode()
            return 200, json.dumps({"access_token": "discord-access", "token_type": "Bearer"}).encode()
        if url == discord_auth.ME_URL:
            assert headers["Authorization"] == "Bearer discord-access"
            return 200, json.dumps({"id": self.user_id, "username": "nelly", "global_name": "Nelly", "avatar": "abc"}).encode()
        return 404, b"{}"


def client(tmp_path, fake=None, verifier=None):
    c = TestClient(create_app(f"sqlite:///{tmp_path / 'd.db'}", discord=CFG, discord_http=fake or FakeDiscord(),
                              verifier=verifier), base_url=APP)
    return c


def login(c, code="good-code"):
    r = c.get("/api/auth/discord/login", follow_redirects=False)
    assert r.status_code == 302
    loc = urllib.parse.urlparse(r.headers["location"])
    q = urllib.parse.parse_qs(loc.query)
    assert loc.netloc == "discord.com" and q["scope"] == ["identify"]
    assert q["redirect_uri"] == [f"{APP}/api/auth/discord/callback"] and "discord-client-secret" not in r.headers["location"]
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "secure" in set_cookie and "samesite=lax" in set_cookie
    return c.get(f"/api/auth/discord/callback?code={code}&state={q['state'][0]}", follow_redirects=False)


def test_full_login_and_session(tmp_path):
    fake = FakeDiscord()
    c = client(tmp_path, fake)
    assert c.get("/api/auth/config").json() == {"clerk": False, "discord": True, "single_user": False}
    assert c.get("/api/positions").status_code == 401
    r = login(c)
    assert r.status_code == 302 and r.headers["location"] == f"{APP}/"
    assert "tape_session=" in r.headers["set-cookie"] and "httponly" in r.headers["set-cookie"].lower()
    me = c.get("/api/auth/me").json()
    assert me == {"account": "discord:80351110224678912", "name": "Nelly", "provider": "discord",
                  "avatar": "https://cdn.discordapp.com/avatars/80351110224678912/abc.png?size=64"}
    assert c.get("/api/positions").status_code == 200
    body = urllib.parse.parse_qs(fake.calls[0][3].decode())
    assert body["client_secret"] == ["discord-client-secret"]                  # sekret tylko w żądaniu serwer→Discord
    assert c.post("/api/auth/logout", headers={"Origin": APP}).status_code == 200
    assert c.get("/api/positions").status_code == 401


def test_state_must_match(tmp_path):
    c = client(tmp_path)
    r = c.get("/api/auth/discord/callback?code=good-code&state=attacker-state", follow_redirects=False)
    assert r.headers["location"].endswith("login_error=state")
    c.get("/api/auth/discord/login", follow_redirects=False)                      # ciasteczko z innym state
    r = c.get("/api/auth/discord/callback?code=good-code&state=other", follow_redirects=False)
    assert r.headers["location"].endswith("login_error=state") and "tape_session=" not in r.headers.get("set-cookie", "")
    r = login(c, code="stolen-or-expired")
    assert r.headers["location"].endswith("login_error=discord")
    assert c.get("/api/auth/discord/callback?error=access_denied", follow_redirects=False).headers["location"].endswith("cancelled")


def test_csrf_origin_required_for_writes(tmp_path):
    c = client(tmp_path)
    login(c)
    body = {"name": "Breakout", "rules": []}
    assert c.post("/api/setups", json=body).status_code == 403                                   # brak Origin
    assert c.post("/api/setups", json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/setups", json=body, headers={"Referer": "https://app.example.com.evil.io/x"}).status_code == 403
    assert c.post("/api/setups", json=body, headers={"Origin": APP}).status_code == 201


@pytest.mark.parametrize("forge", [
    lambda: jwt.encode({"sub": "discord:1", "iss": "tape", "aud": "tape-session", "iat": int(time.time()),
                        "exp": int(time.time()) + 60}, "x" * 40, algorithm="HS256"),               # inny sekret
    lambda: jwt.encode({"sub": "discord:1", "iss": "tape", "aud": "tape-session", "iat": 0, "exp": 1}, SECRET,
                       algorithm="HS256"),                                                           # wygasła
    lambda: jwt.encode({"sub": "default", "iss": "tape", "aud": "tape-session", "iat": int(time.time()),
                        "exp": int(time.time()) + 60}, SECRET, algorithm="HS256"),                  # nie konto discord
    lambda: jwt.encode({"sub": "discord:1", "iss": "tape", "aud": "tape-session", "iat": int(time.time()),
                        "exp": int(time.time()) + 60}, None, algorithm="none"),
])
def test_forged_sessions_rejected(tmp_path, forge):
    c = client(tmp_path)
    c.cookies.set("tape_session", forge())
    assert c.get("/api/positions").status_code == 401


def test_users_isolated_and_clerk_still_works(tmp_path):
    verify = LocalVerifier("https://unused/jwks.json", ISSUER, authorized_parties=("https://app.example.com",))
    a = client(tmp_path, FakeDiscord("111111111111111111"), verifier=verify)
    login(a)
    a.post("/api/setups", json={"name": "Mój setup"}, headers={"Origin": APP})
    b = client(tmp_path, FakeDiscord("222222222222222222"), verifier=verify)
    login(b)
    assert b.get("/api/setups").json() == [] and len(a.get("/api/setups").json()) == 1
    clerk = TestClient(a.app, base_url=APP)
    r = clerk.get("/api/auth/me", headers={"Authorization": f"Bearer {token('user_clerk')}"})
    assert r.json()["account"] == "user_clerk"


def test_config_validation():
    with pytest.raises(RuntimeError):
        discord_auth.DiscordConfig("1", "s", "http://app.example.com", SECRET).validate()        # bez https
    with pytest.raises(RuntimeError):
        discord_auth.DiscordConfig("1", "s", APP, "short").validate()
