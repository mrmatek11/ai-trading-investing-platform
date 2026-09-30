import json

from fastapi.testclient import TestClient

from tape.api import create_app
from tape.ratelimit import DEFAULT_RULES, RateLimiter, Rule, client_ip, proxy_hops


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_bucket_trips_and_recovers():
    clock = Clock()
    rl = RateLimiter({"g": Rule(capacity=3, per_second=0.5)}, clock)
    assert [rl.hit("g", "1.2.3.4") for _ in range(3)] == [None, None, None]
    assert rl.hit("g", "1.2.3.4") == 2                     # pusty kubełek: token za 2 s
    assert rl.hit("g", "5.6.7.8") is None                  # inny adres ma własny limit
    clock.t += 2
    assert rl.hit("g", "1.2.3.4") is None
    assert rl.hit("g", "1.2.3.4") is not None


def test_realistic_bursts_pass_with_default_rules():
    clock = Clock()
    rl = RateLimiter(DEFAULT_RULES, clock)
    # pierwsza synchronizacja EA: 90 dni historii = kilkanaście paczek po 500 transakcji w ciągu kilku sekund,
    # potem jedno zapytanie na minutę przez godzinę
    assert all(rl.hit("ingest", "ea") is None for _ in range(15))
    for _ in range(60):
        clock.t += 60
        assert rl.hit("ingest", "ea") is None
    # sesja Claude Code: initialize, powiadomienie, tools/list i kilkanaście wywołań naraz
    assert all(rl.hit("mcp", "cc") is None for _ in range(40))


def test_memory_is_bounded():
    clock = Clock()
    rl = RateLimiter({"g": Rule(capacity=5, per_second=1)}, clock, max_keys=100)
    for i in range(10_000):
        rl.hit("g", f"10.0.{i // 256}.{i % 256}")
    assert len(rl) <= 100
    clock.t += 3600                                         # po godzinie pełne kubełki są sprzątane
    for i in range(50):
        rl.hit("g", f"192.168.0.{i}")
    assert len(rl) <= 100


def test_client_ip_counts_trusted_proxy_hops_from_the_right():
    assert client_ip("172.18.0.5", "6.6.6.6, 203.0.113.9", hops=1) == "203.0.113.9"      # tylko nginx
    assert client_ip("172.18.0.5", "spoof, 203.0.113.9, 10.0.0.2", hops=2) == "203.0.113.9"   # TLS/LB + nginx
    assert client_ip("172.18.0.5", "203.0.113.9", hops=2) == "203.0.113.9"               # ominięto LB
    assert client_ip("172.18.0.5", "6.6.6.6", hops=0) == "172.18.0.5"
    assert client_ip("172.18.0.5", None, hops=1) == "172.18.0.5"
    assert [proxy_hops(v) for v in (None, "", "0", "1", "2", "true", "abc", "99")] == [0, 0, 0, 1, 2, 1, 0, 10]


def test_public_endpoints_return_429_with_retry_after(tmp_path, monkeypatch):
    monkeypatch.setenv("TAPE_TRUST_PROXY", "1")
    clock = Clock()
    rl = RateLimiter({"ingest": Rule(3, 0.5), "mcp": Rule(2, 1), "auth": Rule(2, 0.1)}, clock)
    c = TestClient(create_app(f"sqlite:///{tmp_path / 'r.db'}", rate_limiter=rl))
    body = json.dumps({"deals": []})
    hdr = {"Authorization": "Bearer tps_wrong", "X-Forwarded-For": "203.0.113.9"}
    codes = [c.post("/api/ingest/mt5", content=body, headers=hdr).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    r = c.post("/api/ingest/mt5", content=body, headers=hdr)
    assert r.status_code == 429 and r.headers["Retry-After"] == "2"
    # inny klient za tym samym nginx ma własny limit; podrobiony pierwszy wpis XFF niczego nie zmienia
    other = {**hdr, "X-Forwarded-For": "1.1.1.1, 198.51.100.7"}
    assert c.post("/api/ingest/mt5", content=body, headers=other).status_code == 401
    spoof = {**hdr, "X-Forwarded-For": "198.51.100.99, 203.0.113.9"}
    assert c.post("/api/ingest/mt5", content=body, headers=spoof).status_code == 429
    clock.t += 2
    assert c.post("/api/ingest/mt5", content=body, headers=hdr).status_code == 401    # odnowiony
    # endpointy z sesją użytkownika nie są objęte limitem
    assert all(c.get("/api/health").status_code == 200 for _ in range(20))
