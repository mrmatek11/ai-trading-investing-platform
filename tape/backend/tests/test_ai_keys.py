import base64
import json
import io
import os
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tape.api import create_app
from tape.secretbox import SecretBox
from test_auth import ISSUER, LocalVerifier, token
from test_review import FakeMessages, deals_csv, fake_output

GOOD = "sk-ant-api03-" + "A" * 40
OTHER = "sk-ant-api03-" + "B" * 40
BAD = "sk-ant-api03-" + "Z" * 40


class Factory:
    """Podstawiony klient Anthropic: zapamiętuje klucze i modele, odrzuca klucz BAD jak prawdziwe API."""

    def __init__(self):
        self.keys, self.calls = [], []

    def __call__(self, key):
        self.keys.append(key)
        msgs = FakeMessages(fake_output())
        self.calls.append(msgs)

        def retrieve(model):
            if key == BAD:
                req = httpx.Request("GET", "https://api.anthropic.com/v1/models")
                raise anthropic.AuthenticationError("invalid x-api-key", response=httpx.Response(401, request=req), body=None)
            return {"id": model}
        return SimpleNamespace(models=SimpleNamespace(retrieve=retrieve), beta=SimpleNamespace(messages=msgs))


DS_GOOD = "sk-" + "a1" * 16
DS_BAD = "sk-" + "ff" * 16


class FakeDeepSeek:
    """Podstawione API DeepSeek (zgodne z OpenAI): /models i /chat/completions."""

    def __init__(self, content=None):
        self.requests = []
        self.content = content

    def __call__(self, method, url, headers, data):
        self.requests.append((method, url, headers, json.loads(data) if data else None))
        if headers["Authorization"] != f"Bearer {DS_GOOD}":
            return 401, b'{"error": {"message": "Authentication Fails"}}'
        if url.endswith("/models"):
            return 200, json.dumps({"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}).encode()
        content = self.content if self.content is not None else fake_output().model_dump_json()
        return 200, json.dumps({"choices": [{"message": {"content": content}}]}).encode()


def hdr(sub):
    return {"Authorization": f"Bearer {token(sub)}"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("TAPE_AI_REQUIRE_USER_KEY", raising=False)
    f = Factory()
    box = SecretBox.from_env("k1:" + base64.b64encode(os.urandom(32)).decode())
    verify = LocalVerifier("https://unused/jwks.json", ISSUER, authorized_parties=("https://app.example.com",))
    url = f"sqlite:///{tmp_path / 'a.db'}"
    return TestClient(create_app(url, verifier=verify, secret_box=box, ai_factory=f, llm_http=FakeDeepSeek())), f, url


def test_key_lifecycle_encrypted_and_masked(env):
    c, f, url = env
    assert c.put("/api/ai/key", headers=hdr("a"), json={"api_key": "sk-proj-xyz"}).status_code == 400    # nie Anthropic
    r = c.put("/api/ai/key", headers=hdr("a"), json={"api_key": BAD})
    assert r.status_code == 400 and "odrzucił" in r.json()["detail"]
    assert c.get("/api/ai/settings", headers=hdr("a")).json()["has_key"] is False                      # nic nie zapisano
    ok = c.put("/api/ai/key", headers=hdr("a"), json={"api_key": GOOD, "model": "claude-sonnet-5-5"}).json()
    assert ok == {"ok": True, "last4": "AAAA", "model": "claude-sonnet-5-5", "provider": "anthropic"}
    st = c.get("/api/ai/settings", headers=hdr("a")).json()
    assert st["has_key"] and st["last4"] == "AAAA" and GOOD not in str(st)
    from tape.ai_keys import AiCredential
    from tape.db import make_sessionmaker
    with make_sessionmaker(url)() as s:
        assert GOOD not in s.scalars(select(AiCredential)).one().secret                               # zaszyfrowany
    assert c.put("/api/ai/key", headers=hdr("a"), json={"model": "claude-opus-5-5"}).json()["model"] == "claude-opus-5-5"
    assert c.put("/api/ai/key", headers=hdr("a"), json={"model": "gpt-9"}).status_code == 400
    assert c.get("/api/ai/settings", headers=hdr("b")).json()["has_key"] is False                     # izolacja
    assert c.delete("/api/ai/key", headers=hdr("a")).json() == {"ok": True}
    assert c.get("/api/ai/settings", headers=hdr("a")).json()["has_key"] is False


def test_review_uses_the_users_own_key_and_model(env):
    c, f, _ = env
    c.put("/api/ai/key", headers=hdr("a"), json={"api_key": GOOD, "model": "claude-sonnet-5-5"})
    c.post("/api/imports", headers=hdr("a"), files={"file": ("d.csv", io.BytesIO(deals_csv(12)), "text/csv")})
    assert c.get("/api/review", headers=hdr("a")).json()["ai_source"] == "user"
    r = c.post("/api/review", headers=hdr("a"))
    assert r.status_code == 200
    call = next(m for m in f.calls if m.calls).calls[0]
    assert call["model"] == "claude-sonnet-5-5" and f.keys[-1] == GOOD
    # użytkownik bez klucza, serwer bez klucza → wyraźna informacja, nie cudzy klucz
    c.post("/api/imports", headers=hdr("b"), files={"file": ("d.csv", io.BytesIO(deals_csv(12)), "text/csv")})
    r = c.post("/api/review", headers=hdr("b"))
    assert r.status_code == 503 and "klucz AI" in r.json()["detail"]
    assert OTHER not in f.keys


def test_server_key_fallback_and_require_user_key(tmp_path, monkeypatch):
    server_msgs = FakeMessages(fake_output())
    server = SimpleNamespace(beta=SimpleNamespace(messages=server_msgs))
    c = TestClient(create_app(f"sqlite:///{tmp_path / 's.db'}", ai_client=server))
    c.post("/api/imports", files={"file": ("d.csv", io.BytesIO(deals_csv(12)), "text/csv")})
    assert c.get("/api/review").json()["ai_source"] == "server"
    monkeypatch.setenv("TAPE_AI_REQUIRE_USER_KEY", "1")
    assert c.get("/api/review").json()["ai_available"] is False
    assert c.post("/api/review").status_code == 503


def test_no_encryption_no_key_storage(tmp_path, monkeypatch):
    monkeypatch.delenv("TAPE_SECRET_KEYS", raising=False)
    c = TestClient(create_app(f"sqlite:///{tmp_path / 'n.db'}", ai_factory=Factory()))
    assert c.put("/api/ai/key", json={"api_key": GOOD}).status_code == 503


def test_deepseek_key_and_review(env):
    c, f, _ = env
    assert c.put("/api/ai/key", headers=hdr("a"), json={"api_key": GOOD, "model": "deepseek-chat"}).status_code == 400
    r = c.put("/api/ai/key", headers=hdr("a"), json={"api_key": DS_BAD, "model": "deepseek-chat"})
    assert r.status_code == 400 and "DeepSeek odrzucił" in r.json()["detail"]
    ok = c.put("/api/ai/key", headers=hdr("a"), json={"api_key": DS_GOOD, "model": "deepseek-chat"}).json()
    assert ok["provider"] == "deepseek" and ok["last4"] == DS_GOOD[-4:]
    st = c.get("/api/ai/settings", headers=hdr("a")).json()
    assert st["provider"] == "deepseek" and DS_GOOD not in str(st)
    # zmiana modelu na innego dostawcę bez nowego klucza → błąd, nie wysłanie klucza DeepSeek do Anthropic
    r = c.put("/api/ai/key", headers=hdr("a"), json={"model": "claude-opus-5-5"})
    assert r.status_code == 400 and not f.keys
    c.post("/api/imports", headers=hdr("a"), files={"file": ("d.csv", io.BytesIO(deals_csv(12)), "text/csv")})
    assert c.get("/api/review", headers=hdr("a")).json()["ai_source"] == "user"
    r = c.post("/api/review", headers=hdr("a"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dropped"] >= 1                          # ta sama walidacja liczb co dla Claude
    assert "1234.56" not in json.dumps(body["leaks"])


def test_deepseek_llm_rejects_malformed_json():
    from tape.llm import DeepSeekLLM
    from tape.review import ReviewOutput

    bad = DeepSeekLLM(DS_GOOD, "deepseek-chat", FakeDeepSeek(content="to nie jest JSON"))
    assert bad.parse("s", "u", ReviewOutput) is None
    fenced = DeepSeekLLM(DS_GOOD, "deepseek-chat", FakeDeepSeek(content="```json\n" + fake_output().model_dump_json() + "\n```"))
    assert fenced.parse("s", "u", ReviewOutput).headline
    http = FakeDeepSeek()
    DeepSeekLLM(DS_GOOD, "deepseek-reasoner", http).parse("s", "u", ReviewOutput)
    assert "response_format" not in http.requests[-1][3]            # reasoner nie ma trybu JSON
    DeepSeekLLM(DS_GOOD, "deepseek-chat", http).parse("s", "u", ReviewOutput)
    assert http.requests[-1][3]["response_format"] == {"type": "json_object"}
