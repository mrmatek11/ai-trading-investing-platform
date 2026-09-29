"""Raport tygodniowy i alerty e-mail.

- Raport: poniedziałek rano (UTC), ostatnie 7 dni — wynik, najlepsza/najgorsza transakcja, koszt błędów,
  stan limitów prop i ważne dane USD w nadchodzącym tygodniu. Bez nowego wywołania AI (tylko nagłówek
  ostatniego zapisanego przeglądu), więc raport nic nie kosztuje.
- Alert: konto prop przechodzi na poziom „danger” albo „breached” — jeden e-mail na konto na dzień.

Wysyłka przez SMTP (dowolny dostawca): TAPE_SMTP_HOST, TAPE_SMTP_PORT (587), TAPE_SMTP_USER,
TAPE_SMTP_PASSWORD, TAPE_MAIL_FROM. Bez konfiguracji worker tylko loguje, co by wysłał.

Worker:  python -m tape.reports --every 300
"""

from __future__ import annotations

import argparse
import html
import logging
import os
import smtplib
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Callable, Dict, List, Optional

from sqlalchemy import Boolean, Date, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import econ_calendar, journal, prop_accounts, service
from .db import Base, UtcDateTime
from .engine import prop, stats

log = logging.getLogger("tape.reports")


class UserSettings(Base):
    __tablename__ = "user_settings"

    account: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), default="")
    weekly_report: Mapped[bool] = mapped_column(Boolean, default=True)
    prop_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    last_weekly_at: Mapped[Optional[datetime]] = mapped_column(UtcDateTime, nullable=True)


class AlertLog(Base):
    """Jeden alert na konto prop na dzień i poziom — żeby nie zasypać skrzynki co 5 minut."""

    __tablename__ = "alert_log"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    account: Mapped[str] = mapped_column(String(64), index=True)
    day: Mapped[date] = mapped_column(Date)
    sent_at: Mapped[datetime] = mapped_column(UtcDateTime)


@dataclass
class Mail:
    to: str
    subject: str
    text: str
    html: str
    kind: str = "weekly"                       # weekly | alert


Sender = Callable[[Mail], None]


def smtp_sender_from_env() -> Optional[Sender]:
    host = os.getenv("TAPE_SMTP_HOST", "")
    sender = os.getenv("TAPE_MAIL_FROM", "")
    if not host or not sender:
        return None
    port = int(os.getenv("TAPE_SMTP_PORT", "587"))
    user, password = os.getenv("TAPE_SMTP_USER", ""), os.getenv("TAPE_SMTP_PASSWORD", "")

    def send(m: Mail) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = sender, m.to, m.subject
        msg.set_content(m.text)
        msg.add_alternative(m.html, subtype="html")
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)

    return send


def _m(v: float) -> str:
    s = f"{abs(v):,.2f}".replace(",", " ").replace(".", ",")
    return ("+" if v > 0 else "−" if v < 0 else "") + s


def trades_word(n: int) -> str:
    if n == 1:
        return "transakcja"
    return "transakcje" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "transakcji"


def _row(label: str, value: str, color: str = "#e8e8ea") -> str:
    return (f'<tr><td style="padding:6px 0;color:#8b8b93">{html.escape(label)}</td>'
            f'<td style="padding:6px 0;text-align:right;font-family:monospace;color:{color}">{html.escape(value)}</td></tr>')


def _tone(v: float) -> str:
    return "#3fb68b" if v > 0 else "#e5534b" if v < 0 else "#e8e8ea"


def weekly_report(session: Session, account: str, now: datetime, app_url: str = "") -> Optional[Mail]:
    """Treść raportu za ostatnie 7 dni; None gdy w tym tygodniu nie było zamkniętych transakcji."""
    start = now - timedelta(days=7)
    all_pos = service.load_positions(session, account)
    week = [p for p in all_pos if p.closed_at is not None and start <= p.closed_at <= now]
    if not week:
        return None
    s = stats.summarize(week)
    best = max(week, key=lambda p: p.net_pnl)
    worst = min(week, key=lambda p: p.net_pnl)
    entries = journal.entries_by_key(session, account)
    mistakes = [g for g in journal.mistake_costs(week, entries) if g.net_pnl < 0][:3]
    rows = list(session.scalars(select(prop_accounts.PropAccountRow)
                                .where(prop_accounts.PropAccountRow.account == account)))
    props = [(r, prop.today_status(service.load_positions(session, account, r.book), r.rules(), now)) for r in rows]
    upcoming = econ_calendar.relevant(session, now, now + timedelta(days=7))
    from .review import latest as latest_review
    rev = latest_review(session, account)

    text = [f"Tape — tydzień do {now:%d.%m.%Y}", "",
            f"Wynik netto: {_m(s.net_pnl)} USD · {s.trades} {trades_word(s.trades)} · win rate {(s.win_rate or 0) * 100:.0f}%",
            f"Najlepsza: {best.symbol} {_m(float(best.net_pnl))} · najgorsza: {worst.symbol} {_m(float(worst.net_pnl))}"]
    body = [_row("Wynik netto", f"{_m(s.net_pnl)} USD", _tone(s.net_pnl)), _row("Transakcje", str(s.trades)),
            _row("Win rate", f"{(s.win_rate or 0) * 100:.0f}%"),
            _row("Najlepsza", f"{best.symbol} {_m(float(best.net_pnl))}", _tone(float(best.net_pnl))),
            _row("Najgorsza", f"{worst.symbol} {_m(float(worst.net_pnl))}", _tone(float(worst.net_pnl)))]
    sections = []
    if s.trades < 20:
        text.append("Mała próba — tygodniowy wynik to jeszcze szum, patrz na proces.")
        sections.append("<p style='color:#8b8b93;margin-top:12px'>Mała próba — tygodniowy wynik to jeszcze szum, patrz na proces.</p>")
    if mistakes:
        items = "".join(f"<li>{html.escape(g.key)}: {g.trades}× · {_m(g.net_pnl)} USD</li>" for g in mistakes)
        sections.append(f"<h3 style='font-size:14px;margin:18px 0 6px'>Koszt błędów</h3><ul style='margin:0;padding-left:18px'>{items}</ul>")
        text += ["", "Koszt błędów:"] + [f"- {g.key}: {g.trades}x, {_m(g.net_pnl)} USD" for g in mistakes]
    if props:
        items = "".join(f"<li>{html.escape(r.name)}: zostało {_m(float(st.daily_left))} dziennego limitu, "
                        f"{_m(float(st.overall_left))} do max drawdownu</li>" for r, st in props)
        sections.append(f"<h3 style='font-size:14px;margin:18px 0 6px'>Konta prop</h3><ul style='margin:0;padding-left:18px'>{items}</ul>")
        text += ["", "Konta prop:"] + [f"- {r.name}: {st.daily_left:.0f} dziś / {st.overall_left:.0f} do DD" for r, st in props]
    if upcoming:
        items = "".join(f"<li>{e.ts:%a %d.%m %H:%M} UTC — {html.escape(e.title)}</li>" for e in upcoming[:8])
        sections.append(f"<h3 style='font-size:14px;margin:18px 0 6px'>Ważne dane USD w tym tygodniu</h3><ul style='margin:0;padding-left:18px'>{items}</ul>")
        text += ["", "Ważne dane USD:"] + [f"- {e.ts:%a %d.%m %H:%M} UTC {e.title}" for e in upcoming[:8]]
    if rev and rev.payload.get("headline"):
        sections.append(f"<p style='color:#8b8b93;margin-top:18px'>Ostatni przegląd AI: {html.escape(rev.payload['headline'])}</p>")
    link = f"<p style='margin-top:18px'><a href='{html.escape(app_url)}' style='color:#e8e8ea'>Otwórz Tape</a></p>" if app_url else ""
    page = (f"<div style='background:#0b0b0d;color:#e8e8ea;font-family:-apple-system,Segoe UI,sans-serif;padding:24px'>"
            f"<div style='max-width:560px;margin:0 auto'><h2 style='font-size:18px;margin:0 0 12px'>Tydzień do {now:%d.%m.%Y}</h2>"
            f"<table style='width:100%;border-collapse:collapse;font-size:14px'>{''.join(body)}</table>"
            f"{''.join(sections)}{link}<p style='color:#5a5a62;font-size:12px;margin-top:24px'>"
            f"Liczby z zamkniętych transakcji. Raport możesz wyłączyć w ustawieniach Tape.</p></div></div>")
    return Mail(to="", subject=f"Tape: tydzień {_m(s.net_pnl)} USD · {s.trades} {trades_word(s.trades)}",
                text="\n".join(text), html=page)


def prop_alerts(session: Session, account: str, now: datetime) -> List[Mail]:
    out = []
    rows = session.scalars(select(prop_accounts.PropAccountRow).where(prop_accounts.PropAccountRow.account == account))
    from .sync import latest_equity

    for r in rows:
        eq = latest_equity(session, account, r.book)
        fresh = eq is not None and now - eq.ts <= timedelta(minutes=15)
        st = prop.today_status(service.load_positions(session, account, r.book), r.rules(), now,
                               floating=(eq.equity - eq.balance) if fresh else None)
        if st.level not in ("danger", "breached"):
            continue
        key = f"{account}|{r.id}|{st.day.isoformat()}|{st.level}"
        if session.get(AlertLog, key) is not None:
            continue
        session.add(AlertLog(key=key, account=account, day=st.day, sent_at=now))
        what = ("limit złamany" if st.level == "breached"
                else f"zostało {_m(float(st.daily_left))} z {_m(float(st.daily_limit))} dziennego limitu")
        text = (f"{r.name}: {what}.\nDo max drawdownu: {_m(float(st.overall_left))}.\n"
                "Rozważ przerwę do jutra — większość oblanych challenge'y to złamana reguła, nie strategia.")
        out.append(Mail(to="", kind="alert", subject=f"Tape: {r.name} — {what}", text=text,
                        html=f"<p>{html.escape(text).replace(chr(10), '<br>')}</p>"))
    return out


def due_weekly(settings: UserSettings, now: datetime) -> bool:
    if not settings.weekly_report or not settings.email:
        return False
    if now.weekday() != 0 or now.hour < 7:                         # poniedziałek od 07:00 UTC
        return False
    return settings.last_weekly_at is None or now - settings.last_weekly_at > timedelta(days=6)


def run_once(session: Session, send: Optional[Sender], now: Optional[datetime] = None,
             app_url: str = "") -> Dict[str, int]:
    now = now or datetime.now(timezone.utc)
    sent = {"weekly": 0, "alerts": 0}
    for st in list(session.scalars(select(UserSettings).where(UserSettings.email != ""))):
        try:
            _send_for(session, st, send, now, app_url, sent)
            session.commit()                                         # zapis po wysyłce: nie gubimy informacji o wysłanych
        except Exception as exc:  # błąd jednego użytkownika nie blokuje raportów pozostałych
            session.rollback()
            sent["errors"] = sent.get("errors", 0) + 1
            log.warning("raporty dla %s: %s", st.account, type(exc).__name__)
    return sent


def _send_for(session: Session, st: UserSettings, send: Optional[Sender], now: datetime, app_url: str,
              sent: Dict[str, int]) -> None:
    mails: List[Mail] = []
    weekly = due_weekly(st, now)
    if weekly:
        m = weekly_report(session, st.account, now, app_url)
        st.last_weekly_at = now                                  # także gdy brak transakcji — nie próbujemy co 5 min
        if m:
            mails.append(m)
    if st.prop_alerts:
        mails += prop_alerts(session, st.account, now)
    for m in mails:
        m.to = st.email
        m.subject = " ".join(m.subject.split())[:180]           # nagłówek w jednej linii (nazwy od użytkownika)
        if send is None:
            log.info("(bez SMTP) do %s: %s", m.to, m.subject)
        else:
            send(m)
        sent["weekly" if m.kind == "weekly" else "alerts"] += 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Raporty i alerty e-mail Tape")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--every", type=int, default=300)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from .db import make_sessionmaker

    Session_ = make_sessionmaker()
    send = smtp_sender_from_env()
    app_url = os.getenv("TAPE_APP_URL", "")
    while True:
        with Session_() as s:
            try:
                log.info("raporty: %s", run_once(s, send, app_url=app_url))
            except Exception as exc:  # SMTP/sieć — spróbujemy przy kolejnym przebiegu
                s.rollback()
                log.warning("raporty: %s", exc)
        if args.once:
            break
        time.sleep(args.every)


if __name__ == "__main__":
    main()
