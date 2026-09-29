import base64
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tape import brief, econ_calendar, market
from tape.api import create_app
from tape.db import make_sessionmaker
from tape.llm import LLM
from tape.news.store import ArticleRow, PriceRow
from tape.secretbox import SecretBox

NOW = datetime(2026, 9, 29, 6, 0, tzinfo=timezone.utc)          # wtorek 08:00 w Warszawie
HOOK = "https://discord.com/api/webhooks/123456789012/" + "a" * 40


class FakeLLM(LLM):
    provider = "fake"

    def __init__(self, out):
        super().__init__("fake-1")
        self.out, self.prompts = out, []

    def parse(self, system, user, schema, **kw):
        self.prompts.append(user)
        return self.out


def output(cpi_id="F3"):
    return brief.BriefOutput(
        headline="CPI zdecyduje o kierunku złota",
        what_decides="Odczyt CPI o 14:30: prognoza 0.3%.",
        what_decides_facts=[cpi_id],
        events=[brief.EventNote(fact=cpi_id, why="Inflacja wpływa na oczekiwania wobec Fed.",
                                if_above="Powyżej 0.3% → mocniejszy dolar, presja na złoto.",
                                if_below="Poniżej 0.3% → słabszy dolar, wsparcie dla złota."),
                brief.EventNote(fact=cpi_id, why="Zmyślone: rynek oczekuje 0.7%.", if_above="-", if_below="-"),
                brief.EventNote(fact="F1", why="To nie kalendarz.", if_above="-", if_below="-")],
        drivers=[brief.Driver(title="Złoto blisko 2650.40", detail="XAU 2650.40 USD.", facts=["F1"]),
                 brief.Driver(title="Cel 2800", detail="Złoto pójdzie na 2800 USD.", facts=["F1"])],
        outlook=[brief.Outlook(asset="XAU", lean="neutralnie", reasoning="Rynek czeka na CPI.", facts=[cpi_id])],
        risk="Zmienność wokół 14:30.",
    )


@pytest.fixture
def db(tmp_path, monkeypatch):
    for k in ("TAPE_BRIEF_TIME", "TAPE_BRIEF_TZ", "TAPE_TELEGRAM_CHAT_ID", "TAPE_APP_URL"):
        monkeypatch.delenv(k, raising=False)
    url = f"sqlite:///{tmp_path / 'b.db'}"
    S = make_sessionmaker(url)
    with S() as s:
        econ_calendar.add_events(s, "csv", [
            {"ts": datetime(2026, 9, 29, 12, 30, tzinfo=timezone.utc), "country": "USD", "title": "CPI m/m",
             "impact": "high", "forecast": "0.3%", "previous": "0.2%"},
            {"ts": datetime(2026, 9, 30, 12, 15, tzinfo=timezone.utc), "country": "USD", "title": "ADP Employment",
             "impact": "high", "forecast": "", "previous": ""},
            {"ts": datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc), "country": "EUR", "title": "German CPI",
             "impact": "high", "forecast": "", "previous": ""},
        ])
        s.add(PriceRow(asset="XAU", ts=NOW - timedelta(hours=25), price=2637.0))
        s.add(market.QuoteRow(asset="XAU", provider="test", ts=NOW - timedelta(minutes=5), price=2650.4))
        s.add(ArticleRow(url="https://fx.example/gold", title="Gold <b>steady</b> ahead of CPI & Fed",
                         published_at=NOW - timedelta(hours=2)))
        s.add(ArticleRow(url="https://old.example/x", title="Old news", published_at=NOW - timedelta(days=2)))
        s.commit()
    return S, url


def test_facts_cover_calendar_prices_and_headlines(db):
    S, _ = db
    with S() as s:
        data = brief.gather(s, NOW)
    facts = brief.build_facts(data)
    texts = [f["text"] for f in facts]
    assert texts[0] == "XAU: ostatnia cena 2650.40 USD, zmiana 24 h +0.51%."
    assert "Kalendarz (dziś 14:30 czasu lokalnego): USD CPI m/m — ważność wysoka; prognoza 0.3%; poprzednio 0.2%." in texts
    assert any("jutro 14:15" in t and "ADP" in t for t in texts)
    assert not any("German" in t for t in texts)                               # tylko USD
    assert [h["source"] for h in data["headlines"]] == ["fx.example"]            # stare nagłówki odpadają


def test_ai_commentary_is_validated_against_facts(db):
    S, _ = db
    with S() as s:
        facts = brief.build_facts(brief.gather(s, NOW))
    cpi = next(f["id"] for f in facts if "CPI" in f["text"])
    llm = FakeLLM(output(cpi))
    out = brief.generate(llm, facts)
    assert out["what_decides"] and out["headline"]
    assert [e["why"] for e in out["events"]] == ["Inflacja wpływa na oczekiwania wobec Fed."]   # 0.7% i F1 odrzucone
    assert [d["title"] for d in out["drivers"]] == ["Złoto blisko 2650.40"]                  # 2800 zmyślone
    assert out["dropped"] == 2 and out["outlook"][0]["lean"] == "neutralnie"
    assert "<fakty>" in llm.prompts[0] and "Gold <b>steady</b>" in llm.prompts[0]


def test_brief_without_ai_and_formatting_is_escaped(db):
    S, _ = db
    with S() as s:
        row = brief.create(s, None, NOW)
        s.commit()
        p = row.payload
    assert p["ai"] is None and "Brak klucza" in p["ai_error"]
    tg = brief.to_telegram(p, "https://app.example")
    assert "Gold &lt;b&gt;steady&lt;/b&gt; ahead of CPI &amp; Fed" in tg and "<b>steady</b>" not in tg
    assert "🔴 dziś 14:30 — CPI m/m (prog. 0.3%, poprz. 0.2%)" in tg and len(tg) <= 3900
    emb = brief.to_discord(p)
    assert emb["title"].startswith("☀️ GoldTape") and len(emb["fields"]) <= 25


def test_telegram_is_split_to_limit(db):
    S, _ = db
    with S() as s:
        p = brief.create(s, None, NOW).payload
    p["headlines"] = [{"title": "x" * 900, "url": "https://a.example/" + str(i), "source": "a.example", "local": "07:00"}
                      for i in range(6)]
    assert len(brief.to_telegram(p)) <= 3900


def test_due_only_on_weekdays_after_time(monkeypatch):
    assert brief.due(NOW)
    assert not brief.due(datetime(2026, 9, 29, 5, 0, tzinfo=timezone.utc))      # 07:00 Warszawa
    assert not brief.due(datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc))      # sobota
    monkeypatch.setenv("TAPE_BRIEF_TIME", "06:00")
    assert brief.due(datetime(2026, 9, 29, 4, 30, tzinfo=timezone.utc))


class FakeTelegram(brief.Telegram):
    def __init__(self, updates=()):
        self.sent, self._updates, self.offsets = [], list(updates), []

    def send(self, chat_id, text):
        if chat_id == "blocked":
            raise RuntimeError("Telegram 403: bot was blocked by the user")
        self.sent.append((chat_id, text))

    def updates(self, offset):
        self.offsets.append(offset)
        out = [u for u in self._updates if u["update_id"] >= offset]
        return out


def msg(uid, chat, text):
    return {"update_id": uid, "message": {"chat": {"id": chat, "username": f"u{chat}"}, "text": text}}


def test_worker_links_telegram_and_delivers_once(db, monkeypatch):
    S, _ = db
    monkeypatch.setenv("TAPE_TELEGRAM_CHAT_ID", "@goldtape_brief")
    box = SecretBox.from_env("k1:" + base64.b64encode(os.urandom(32)).decode())
    with S() as s:
        s.add(brief.BriefSubscription(account="a", link_code="CODEaaaa1111", link_expires=NOW + timedelta(minutes=10)))
        s.add(brief.BriefSubscription(account="b", link_code="CODEbbbb2222", link_expires=NOW - timedelta(minutes=1)))
        s.add(brief.BriefSubscription(account="c", telegram_chat_id="blocked"))
        s.add(brief.BriefSubscription(account="d", discord_webhook=box.encrypt(HOOK.encode(), brief.webhook_context("d"))))
        s.add(brief.BriefSubscription(account="e", enabled=False, telegram_chat_id="999"))
        s.commit()
    tg = FakeTelegram([msg(10, 111, "/start CODEaaaa1111"), msg(11, 222, "/start CODEbbbb2222"),
                       msg(12, 333, "/start@goldtape_bot CODEaaaa1111")])
    posts = []

    def http(method, url, headers, data):
        posts.append((url, json.loads(data)))
        return 204, b""

    with S() as s:
        rep = brief.run_once(s, FakeLLM(None), box, tg, NOW, http)
    assert rep["created"] == "2026-09-29" and rep["sent"] == 3 and rep["failed"] == 1
    chats = [c for c, _ in tg.sent]
    assert "111" in chats and "@goldtape_brief" in chats and "999" not in chats
    assert any(c == "222" and "wygasł" in t for c, t in tg.sent)                   # przeterminowany kod
    assert any(c == "333" and "wygasł" in t for c, t in tg.sent)                   # kod jednorazowy
    assert posts[0][0] == HOOK and posts[0][1]["username"] == "GoldTape"
    with S() as s:
        a = s.get(brief.BriefSubscription, "a")
        assert a.telegram_chat_id == "111" and a.link_code is None
        failed = s.scalars(select(brief.BriefDelivery).where(brief.BriefDelivery.ok.is_(False))).one()
        assert failed.target == "tg:blocked" and "blocked" in failed.error and HOOK not in failed.error
    n = len(tg.sent)
    with S() as s:
        rep2 = brief.run_once(s, FakeLLM(None), box, tg, NOW + timedelta(minutes=1), http)
    assert rep2.get("sent", 0) == 0 and len(tg.sent) == n and tg.offsets[-1] == 13     # bez duplikatów
    with S() as s:
        brief.run_once(s, None, box, FakeTelegram([msg(13, 111, "/stop")]), NOW + timedelta(minutes=2), http)
        assert s.get(brief.BriefSubscription, "a").telegram_chat_id is None


def test_brief_api_subscription_and_link(db, monkeypatch):
    _, url = db
    box = SecretBox.from_env("k1:" + base64.b64encode(os.urandom(32)).decode())
    posts = []

    def http(method, u, headers, data):
        posts.append(u)
        return (204, b"") if u == HOOK else (404, b"")

    c = TestClient(create_app(url, secret_box=box, notify_http=http))
    assert c.get("/api/brief").json()["brief"] is None
    assert c.post("/api/brief/telegram/link").status_code == 503                  # bot nieskonfigurowany
    assert c.put("/api/brief/subscription", json={"discord_webhook": "https://evil.example/hook"}).status_code == 400
    assert c.put("/api/brief/subscription", json={"discord_webhook": HOOK.replace("a" * 40, "b" * 40)}).status_code == 400
    r = c.put("/api/brief/subscription", json={"discord_webhook": HOOK})
    assert r.status_code == 200 and r.json()["discord_linked"] and posts[-1] == HOOK
    with make_sessionmaker(url)() as s:
        assert HOOK not in s.get(brief.BriefSubscription, "default").discord_webhook
    monkeypatch.setenv("TAPE_TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TAPE_TELEGRAM_BOT_USERNAME", "@goldtape_bot")
    link = c.post("/api/brief/telegram/link").json()
    assert link["url"].startswith("https://t.me/goldtape_bot?start=")
    assert c.post("/api/brief/generate").status_code == 200                       # tryb jednego użytkownika = admin
    body = c.get("/api/brief").json()
    assert body["brief"]["day"] and body["config"]["telegram"] and body["subscription"]["discord_linked"]
