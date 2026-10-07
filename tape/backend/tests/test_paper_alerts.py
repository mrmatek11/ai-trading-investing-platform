import base64
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tape import brief
from tape.api import create_app
from tape.db import make_sessionmaker
from tape.paper import alerts, store, worker
from tape.paper.engine import BAR, LONDON
from tape.secretbox import SecretBox
from test_brief import HOOK, FakeTelegram
from test_paper import history, start_run


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.delenv("TAPE_APP_URL", raising=False)
    url = f"sqlite:///{tmp_path / 'a.db'}"
    return make_sessionmaker(url), url


@pytest.fixture
def box():
    return SecretBox.from_env("k1:" + base64.b64encode(os.urandom(32)).decode())


def at(day, hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=LONDON).astimezone(timezone.utc)


def live(bars, now):
    return [b for b in bars if b.ts + BAR <= now]           # w bazie są tylko zamknięte świece


def seed(S, bars, now, runs=(("a", "111"),), alerts_on=True, name=None):
    """Świece do `now` + po jednym przebiegu na konto, czat Telegram z ustawień briefu."""
    ids = {}
    with S() as s:
        store.save_bars(s, "XAU", live(bars, now), "oanda")
        for account, chat in runs:
            day = store.trading_day(now)
            run = start_run(s, day, account=account)
            run.alerts = alerts_on
            if name:
                run.name = name
            sub = s.get(brief.BriefSubscription, account) or brief.BriefSubscription(account=account)
            sub.telegram_chat_id = chat
            s.add(sub)
            ids[account] = run.id
        s.commit()
    return ids


def test_plan_is_sent_once_after_range_closes(db, box):
    S, _ = db
    bars, day = history()
    ids = seed(S, bars, at(day, "09:20"))
    with S() as s:
        sub = s.get(brief.BriefSubscription, "a")
        sub.discord_webhook = box.encrypt(HOOK.encode(), brief.webhook_context("a"))
        s.commit()
    tg, posts = FakeTelegram(), []

    def http(method, url, headers, data):
        posts.append((url, json.loads(data)))
        return 204, b""

    ch = alerts.Channels(tg, box, http)
    with S() as s:
        assert alerts.send_due(s, at(day, "09:20"), ch) == {"sent": 2, "failed": 0}
    assert len(tg.sent) == 1 and len(posts) == 1
    chat, text = tg.sent[0]
    assert chat == "111"
    assert f"plan dnia {day}" in text
    assert "KUPNO STOP 2004.80 · SL 1996.00 · 0.05 lota" in text          # 50 USD / (8,8 × 100 oz) → 0,05
    assert "SPRZEDAŻ STOP 1995.20 · SL 2004.00 · 0.05 lota" in text
    assert "Za późno" not in text
    assert "OCO" in text and "12:00 Londyn (13:00 Warszawa)" in text and "16:00 Londyn (17:00 Warszawa)" in text
    assert "nie jest rekomendacja" in text and "Paper test" in text
    url, body = posts[0]
    assert url == HOOK and "KUPNO STOP 2004.80" in body["embeds"][0]["description"]
    with S() as s:                                                       # „restart workera”: nowa sesja
        assert alerts.send_due(s, at(day, "09:35"), ch) == {"sent": 0, "failed": 0}
        rows = s.scalars(select(alerts.PaperAlertDelivery)).all()
        assert {(r.run_id, r.day, r.target, r.ok) for r in rows} == {
            (ids["a"], day.isoformat(), "tg:111", True), (ids["a"], day.isoformat(), "dc:a", True)}
    assert len(tg.sent) == 1 and len(posts) == 1


def test_not_before_range_closes(db):
    S, _ = db
    bars, day = history()
    seed(S, bars, at(day, "09:05"))                                      # świeca 08:45 jest, 09:00 jeszcze nie
    tg = FakeTelegram()
    with S() as s:
        assert alerts.send_due(s, at(day, "09:05"), alerts.Channels(tg))["sent"] == 0
        assert alerts.send_due(s, at(day, "09:14"), alerts.Channels(tg))["sent"] == 0
        store.save_bars(s, "XAU", live(bars, at(day, "09:15")), "oanda")   # świeca 09:00 zamknięta
        s.commit()
        assert alerts.send_due(s, at(day, "09:15"), alerts.Channels(tg))["sent"] == 1
    assert len(tg.sent) == 1


def test_late_plan_warns_when_a_level_is_already_crossed(db):
    S, _ = db
    bars, day = history(signal_overrides={"09:00": (2003, 2006, 2002, 2005)})   # wybicie w pierwszej świecy
    seed(S, bars, at(day, "09:20"))
    tg = FakeTelegram()
    with S() as s:
        assert alerts.send_due(s, at(day, "09:20"), alerts.Channels(tg))["sent"] == 1
    text = tg.sent[0][1]
    assert "Za późno" in text and "09:00 Londyn (10:00 Warszawa)" in text and "pozycję long" in text
    assert "KUPNO STOP 2004.80 ·" not in text and "OCO: gdy" not in text and "(kupno stop 2004.80 · SL 1996.00)" in text


def test_not_after_orders_expire(tmp_path):
    S = make_sessionmaker(f"sqlite:///{tmp_path / 'noon.db'}")
    bars, day = history()
    seed(S, bars, at(day, "12:05"))                                      # worker wrócił po 12:00 Londynu
    tg = FakeTelegram()
    with S() as s:
        assert alerts.send_due(s, at(day, "12:05"), alerts.Channels(tg))["sent"] == 0
    assert tg.sent == []


@pytest.mark.parametrize("setup", ["no_nr7", "stopped", "disabled", "before_first_day"])
def test_not_on_non_nr7_days_stopped_disabled_or_early_runs(tmp_path, setup):
    S = make_sessionmaker(f"sqlite:///{tmp_path / 'x.db'}")
    bars, day = history(last_width=12.0 if setup == "no_nr7" else 4.0)  # 12.0: wczoraj nie był dniem NR7
    now = at(day, "09:20")
    ids = seed(S, bars, now, alerts_on=setup != "disabled")
    with S() as s:
        run = s.get(store.PaperRun, ids["a"])
        if setup == "stopped":
            run.status = "stopped"
        if setup == "before_first_day":                                   # przebieg handluje dopiero od jutra
            run.first_day = (day + timedelta(days=1)).isoformat()
        s.commit()
        tg = FakeTelegram()
        assert alerts.send_due(s, now, alerts.Channels(tg))["sent"] == 0
        assert alerts.due_plan(s, run, now) is None
    assert tg.sent == []


def test_runs_of_other_users_never_mix_and_html_is_escaped(db):
    S, _ = db
    bars, day = history()
    seed(S, bars, at(day, "09:20"), runs=(("a", "111"), ("b", "222")))
    with S() as s:
        runs = {r.account: r for r in s.scalars(select(store.PaperRun))}
        runs["a"].name, runs["b"].name = "Złoto <b>A</b> & co", "Konto B"
        runs["b"].risk_pct = Decimal("2")
        s.commit()
    tg = FakeTelegram()
    with S() as s:
        assert alerts.send_due(s, at(day, "09:20"), alerts.Channels(tg))["sent"] == 2
    by_chat = dict(tg.sent)
    assert set(by_chat) == {"111", "222"}
    assert "Złoto &lt;b&gt;A&lt;/b&gt; &amp; co" in by_chat["111"] and "<b>A</b>" not in by_chat["111"]
    assert "Konto B" not in by_chat["111"] and "Złoto" not in by_chat["222"]
    assert "0.05 lota" in by_chat["111"] and "0.22 lota" in by_chat["222"]   # 200 USD / 880 → 0,22


def test_no_channel_means_no_record_and_failures_are_logged_once(db):
    S, _ = db
    bars, day = history()
    seed(S, bars, at(day, "09:20"), runs=(("a", "blocked"),))
    with S() as s:
        assert alerts.send_due(s, at(day, "09:20"), alerts.Channels()) == {"sent": 0, "failed": 0}
        assert s.scalars(select(alerts.PaperAlertDelivery)).all() == []      # bez kanału nic nie zapisujemy
    tg = FakeTelegram()
    with S() as s:
        assert alerts.send_due(s, at(day, "09:20"), alerts.Channels(tg)) == {"sent": 0, "failed": 1}
        assert alerts.send_due(s, at(day, "09:35"), alerts.Channels(tg)) == {"sent": 0, "failed": 0}
        row = s.scalars(select(alerts.PaperAlertDelivery)).one()
        assert not row.ok and "blocked" in row.error


def test_worker_sends_after_settlement_and_alert_errors_do_not_break_it(db, monkeypatch):
    S, _ = db
    bars, day = history()
    seed(S, bars, at(day, "09:20"))
    tg = FakeTelegram()
    with S() as s:
        rep = worker.run_once(s, None, at(day, "09:20"), channels=alerts.Channels(tg))
    assert rep["alerts"] == {"sent": 1, "failed": 0} and len(tg.sent) == 1

    def boom(*a, **kw):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(alerts, "send_due", boom)
    with S() as s:
        store.save_bars(s, "XAU", bars, "oanda")                            # cały dzień: jest co rozliczyć
        s.commit()
        rep = worker.run_once(s, None, at(day, "17:00"), channels=alerts.Channels(tg))
        assert rep["recorded"] == 1 and "alerts" not in rep
        assert s.scalars(select(store.PaperDay)).one().day == day.isoformat()


def test_alerts_toggle_endpoint_is_per_owner(db):
    S, url = db
    with S() as s:
        mine = start_run(s, datetime.now(timezone.utc).date(), account="default")
        theirs = start_run(s, datetime.now(timezone.utc).date(), account="other")
        s.commit()
        mine_id, theirs_id = mine.id, theirs.id
    c = TestClient(create_app(url))
    assert c.get(f"/api/paper/runs/{mine_id}").json()["alerts"] is False         # domyślnie wyłączone
    r = c.post(f"/api/paper/runs/{mine_id}/alerts", json={"enabled": True})
    assert r.status_code == 200 and r.json()["alerts"] is True
    assert c.get("/api/paper").json()["runs"][0]["alerts"] is True
    assert c.post(f"/api/paper/runs/{theirs_id}/alerts", json={"enabled": True}).status_code == 404
    with S() as s:
        assert s.get(store.PaperRun, theirs_id).alerts is False
