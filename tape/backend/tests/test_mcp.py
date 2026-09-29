import io
import json

import pytest
from fastapi.testclient import TestClient

from tape.api import create_app
from test_auth import ISSUER, LocalVerifier, token
from test_review import deals_csv


def hdr(sub):
    return {"Authorization": f"Bearer {token(sub)}"}


def rpc(method, params=None, id_=1):
    m = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        m["params"] = params
    return m


@pytest.fixture
def env(tmp_path):
    verify = LocalVerifier("https://unused/jwks.json", ISSUER, authorized_parties=("https://app.example.com",))
    c = TestClient(create_app(f"sqlite:///{tmp_path / 'm.db'}", verifier=verify))
    c.post("/api/imports", headers=hdr("a"), files={"file": ("d.csv", io.BytesIO(deals_csv(12)), "text/csv")})
    tok_a = c.post("/api/mcp/tokens", headers=hdr("a"), json={"name": "Claude Code"}).json()
    tok_b = c.post("/api/mcp/tokens", headers=hdr("b"), json={"name": "Desktop"}).json()
    return c, tok_a, tok_b


def call(c, tok, body):
    return c.post("/api/mcp", headers={"Authorization": f"Bearer {tok}"}, json=body)


def test_handshake_and_tools(env):
    c, a, _ = env
    assert a["token"].startswith("tpk_") and a["hint"] == a["token"][:8] + "…"
    init = call(c, a["token"], rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                  "clientInfo": {"name": "claude-code", "version": "2"}})).json()
    assert init["result"]["protocolVersion"] == "2025-06-18" and init["result"]["serverInfo"]["name"] == "goldtape"
    old = call(c, a["token"], rpc("initialize", {"protocolVersion": "1999-01-01"})).json()
    assert old["result"]["protocolVersion"] == "2025-06-18"                     # nieznana wersja → nasza najnowsza
    r = call(c, a["token"], {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202
    tools = call(c, a["token"], rpc("tools/list")).json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"get_performance", "list_trades", "get_journal_facts", "get_daily_brief", "get_prop_status"} <= names
    assert all(t["annotations"]["readOnlyHint"] for t in tools)
    perf = call(c, a["token"], rpc("tools/call", {"name": "get_performance", "arguments": {}})).json()["result"]
    assert perf["isError"] is False and perf["structuredContent"]["summary"]["trades"] == 12
    assert json.loads(perf["content"][0]["text"])["summary"]["trades"] == 12
    facts = call(c, a["token"], rpc("tools/call", {"name": "get_journal_facts"})).json()["result"]["structuredContent"]
    assert facts["facts"][0]["id"] == "F1"


def test_isolation_revocation_and_auth(env):
    c, a, b = env
    trades_b = call(c, b["token"], rpc("tools/call", {"name": "list_trades", "arguments": {"limit": 5}})).json()
    assert trades_b["result"]["structuredContent"]["trades"] == []                 # b nie widzi transakcji a
    trades_a = call(c, a["token"], rpc("tools/call", {"name": "list_trades", "arguments": {"limit": 5}})).json()
    assert len(trades_a["result"]["structuredContent"]["trades"]) == 5
    assert c.post("/api/mcp", json=rpc("ping")).status_code == 401
    assert call(c, token("a"), rpc("ping")).status_code == 401                    # sesja aplikacji to nie token MCP
    assert c.post("/api/mcp", headers={"Authorization": f"Bearer {a['token']}", "Origin": "https://evil.example"},
                  json=rpc("ping")).status_code == 403
    assert c.delete(f"/api/mcp/tokens/{a['id']}", headers=hdr("b")).status_code == 404   # cudzy token
    listed = c.get("/api/mcp/tokens", headers=hdr("a")).json()
    assert [t["id"] for t in listed] == [a["id"]] and "token" not in listed[0] and listed[0]["last_used_at"]
    assert c.delete(f"/api/mcp/tokens/{a['id']}", headers=hdr("a")).json() == {"ok": True}
    assert call(c, a["token"], rpc("ping")).status_code == 401


def test_protocol_errors(env):
    c, a, _ = env
    t = a["token"]
    assert call(c, t, rpc("resources/list")).json()["error"]["code"] == -32601
    bad = call(c, t, rpc("tools/call", {"name": "list_trades", "arguments": {"limit": 999}})).json()["result"]
    assert bad["isError"] and "zakres" in bad["content"][0]["text"]
    bad = call(c, t, rpc("tools/call", {"name": "list_trades", "arguments": {"account": "b"}})).json()["result"]
    assert bad["isError"] and "Nieznane" in bad["content"][0]["text"]            # konta nie da się podać z zewnątrz
    assert call(c, t, rpc("tools/call", {"name": "rm_rf"})).json()["error"]["code"] == -32602
    r = c.post("/api/mcp", headers={"Authorization": f"Bearer {t}", "Content-Type": "application/json"}, content=b"{nope")
    assert r.json()["error"]["code"] == -32700
    batch = call(c, t, [rpc("ping", id_=1), {"jsonrpc": "2.0", "method": "notifications/x"}, rpc("ping", id_=2)]).json()
    assert [m["id"] for m in batch] == [1, 2]
    assert c.get("/api/mcp", headers={"Authorization": f"Bearer {t}"}).status_code == 405
