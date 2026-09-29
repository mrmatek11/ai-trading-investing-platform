from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from fastapi.testclient import TestClient

from tape import journal, prop_accounts, reports
from tape.db import make_sessionmaker, store_fills
from tape.importers.base import Fill

MONDAY = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)          # poniedziałek


def fills(pairs, start, prefix=""):
    out = []
    for i, pnl in enumerate(pairs):
        t = start + timedelta(hours=i * 5)
        out += [Fill(f"{prefix}{i}a", t, "XAUUSD", "buy", D(1), D(2000), D(1)),
                Fill(f"{prefix}{i}b", t + timedelta(hours=1), "XAUUSD", "sell", D(1), D(2000 + pnl), D(1))]
    return out


def setup_db(tmp_path, email="a@example.com"):
    S = make_sessionmaker(f"sqlite:///{tmp_path / 'r.db'}")
    with S() as s:
        store_fills(s, "u1", "mt5", fills([300, -4200, 150], MONDAY - timedelta(days=3)), book="FTMO")
        s.add(reports.UserSettings(account="u1", email=email))
        s.add(prop_accounts.PropAccountRow(account="u1", book="FTMO", name="FTMO <100k>", initial_balance=D(100000),
                                           daily_loss_pct=D(5), max_drawdown_pct=D(10), drawdown_type="static",
                                           profit_target_pct=D(10), day_tz="UTC"))
        s.commit()
    return S


def test_weekly_report_escapes_user_content(tmp_path):
    S = setup_db(tmp_path)
    with S() as s:
        key = next(p.key for p in reports.service.load_positions(s, "u1") if p.net_pnl < 0)
        s.add(journal.JournalEntry(account="u1", position_key=key, mistakes=["<script>alert(1)</script>"], notes=""))
        s.commit()
        m = reports.weekly_report(s, "u1", MONDAY)
    assert m.subject == "Tape: tydzień −3 750,00 USD · 3 transakcje"
    assert "<script>" not in m.html and "&lt;script&gt;" in m.html
    assert "FTMO &lt;100k&gt;" in m.html and "Mała próba" in m.text
    with S() as s:
        assert reports.weekly_report(s, "u1", MONDAY + timedelta(days=30)) is None   # brak transakcji w tygodniu


def test_due_weekly():
    st = reports.UserSettings(account="u", email="x@y.z", weekly_report=True)
    assert reports.due_weekly(st, MONDAY) and not reports.due_weekly(st, MONDAY - timedelta(hours=2))
    assert not reports.due_weekly(st, MONDAY + timedelta(days=1))
    st.last_weekly_at = MONDAY
    assert not reports.due_weekly(st, MONDAY + timedelta(hours=3))
    assert not reports.due_weekly(reports.UserSettings(account="u", email="", weekly_report=True), MONDAY)


def test_run_once_sends_weekly_once_and_alert_once_per_day(tmp_path):
    S = setup_db(tmp_path)
    sent = []
    with S() as s:
        assert reports.run_once(s, sent.append, MONDAY) == {"weekly": 1, "alerts": 0}
        assert reports.run_once(s, sent.append, MONDAY + timedelta(minutes=5)) == {"weekly": 0, "alerts": 0}
        # wtorek: −4000 na koncie prop → zostaje 1000 z 5000 dziennego limitu
        store_fills(s, "u1", "mt5", fills([-4000], MONDAY + timedelta(days=1, hours=1), prefix="t"), book="FTMO")
        s.commit()
        tue = MONDAY + timedelta(days=1, hours=6)
        assert reports.run_once(s, sent.append, tue)["alerts"] == 1
        assert reports.run_once(s, sent.append, tue + timedelta(minutes=5))["alerts"] == 0   # bez powtórki
    assert [m.to for m in sent] == ["a@example.com", "a@example.com"]
    assert sent[1].kind == "alert" and "dziennego limitu" in sent[1].subject


def test_no_email_no_mail(tmp_path):
    S = setup_db(tmp_path, email="")
    sent = []
    with S() as s:
        assert reports.run_once(s, sent.append, MONDAY) == {"weekly": 0, "alerts": 0}
    assert sent == []


def test_settings_api_and_preview(tmp_path, monkeypatch):
    from tape.api import create_app
    from test_auth import ISSUER, LocalVerifier, token

    monkeypatch.delenv("TAPE_SMTP_HOST", raising=False)
    verify = LocalVerifier("https://unused/jwks.json", ISSUER, authorized_parties=("https://app.example.com",))
    c = TestClient(create_app(f"sqlite:///{tmp_path / 's.db'}", verifier=verify))
    a = {"Authorization": f"Bearer {token('user_a')}"}
    b = {"Authorization": f"Bearer {token('user_b')}"}
    assert c.get("/api/settings", headers=a).json() == {"email": "", "weekly_report": True, "prop_alerts": True,
                                                         "mail_configured": False}
    assert c.put("/api/settings", headers=a, json={"email": "nie-email"}).status_code == 422
    assert c.put("/api/settings", headers=a, json={"email": "a@example.com", "weekly_report": False}).json() == {"ok": True}
    assert c.get("/api/settings", headers=a).json()["email"] == "a@example.com"
    assert c.get("/api/settings", headers=b).json()["email"] == ""
    assert c.get("/api/reports/weekly/preview", headers=a).status_code == 404


def test_trades_word():
    assert [reports.trades_word(n) for n in (1, 2, 5, 12, 22, 25)] == [
        "transakcja", "transakcje", "transakcji", "transakcji", "transakcje", "transakcji"]


def test_newline_in_name_cannot_inject_headers_or_block_others(tmp_path):
    from email.message import EmailMessage

    S = setup_db(tmp_path)
    with S() as s:
        row = s.scalars(reports.select(prop_accounts.PropAccountRow)).one()
        row.name = "FTMO\nBcc: evil@example.com"
        store_fills(s, "u2", "mt5", fills([100], MONDAY - timedelta(days=2)), book="")
        s.add(reports.UserSettings(account="u2", email="b@example.com"))
        store_fills(s, "u1", "mt5", fills([-4500], MONDAY + timedelta(hours=1), prefix="t"), book="FTMO")
        s.commit()

    built = []

    def strict_send(m):
        msg = EmailMessage()                                   # jak prawdziwy nadawca SMTP
        msg["To"], msg["Subject"] = m.to, m.subject
        msg.set_content(m.text)
        if m.to == "a@example.com" and m.kind == "weekly":
            raise OSError("SMTP chwilowo niedostępny")          # awaria u jednego użytkownika
        built.append(msg)

    with S() as s:
        rep = reports.run_once(s, strict_send, MONDAY + timedelta(hours=3))
    assert rep["errors"] == 1 and [m["To"] for m in built] == ["b@example.com"]     # drugi użytkownik dostał raport
    alert = reports.prop_alerts
    with S() as s:                                               # alert dla u1 wysłany przy kolejnym przebiegu
        mails = alert(s, "u1", MONDAY + timedelta(hours=3))
    assert mails and "\n" not in " ".join(mails[0].subject.split())
    subject = " ".join(mails[0].subject.split())
    msg = EmailMessage()
    msg["Subject"] = subject
    assert "Bcc" not in msg.keys()
