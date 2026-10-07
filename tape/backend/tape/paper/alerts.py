"""Plan dnia dla przebiegów paper: wiadomość z gotowymi zleceniami na Telegram / Discord.

Właściciel handluje ręcznie (np. XTB, bez API brokera) i chce iść za zamrożoną strategią. W aktywny dzień
(po NR7), gdy zakres 08:00–09:00 Londynu jest zamknięty, worker paper wysyła raz na przebieg i dzień:
kupno stop + SL, sprzedaż stop + SL (OCO), ważność do 12:00, zamknięcie do 16:00 Londynu (z czasem Warszawy)
i wielkość pozycji dla bieżącego kapitału i ryzyka przebiegu.

Zasady:
- tylko przebiegi aktywne z włączonym `alerts` (domyślnie wyłączone), tylko dni od `first_day` przebiegu;
- poziomy liczy silnik (`engine.evaluate` → `planned_orders`), ten sam podział na dni co `store.today_plan`;
- zakres uznajemy za zamknięty dopiero, gdy w bazie jest świeca z 09:00 Londynu (świece są zapisywane
  tylko zamknięte, więc wtedy zakres jest ostateczny);
- po 12:00 Londynu (zlecenia wygasły) nic już nie wysyłamy;
- kanały i dziennik jak w porannym briefie: czat Telegram i webhook Discord z `BriefSubscription` właściciela;
  każda próba (udana albo nie) zostaje w `paper_alert_deliveries` — po restarcie workera nie ma duplikatów.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import Boolean, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .. import brief
from ..db import Base, UtcDateTime
from ..llm import default_http
from ..secretbox import SecretBox
from . import engine, store

log = logging.getLogger("tape.paper.alerts")

WARSAW = ZoneInfo("Europe/Warsaw")
DISCLAIMER = "Paper test zamrożonej strategii (w trakcie weryfikacji) — to nie jest rekomendacja inwestycyjna."


class PaperAlertDelivery(Base):
    __tablename__ = "paper_alert_deliveries"
    __table_args__ = (UniqueConstraint("run_id", "day", "target", name="uq_paper_alert_delivery"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    day: Mapped[str] = mapped_column(String(10))                                 # dzień handlowy YYYY-MM-DD
    target: Mapped[str] = mapped_column(String(96))                              # tg:<chat> | dc:<konto>
    sent_at: Mapped[datetime] = mapped_column(UtcDateTime)
    ok: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str] = mapped_column(String(300), default="")


@dataclass
class Channels:
    """Kanały dostarczania (te same co dla briefu). Brak tg / box = ten kanał wyłączony."""
    tg: Optional[brief.Telegram] = None
    box: Optional[SecretBox] = None
    http: brief.Http = default_http

    @classmethod
    def from_env(cls) -> "Channels":
        return cls(brief.telegram_from_env(), SecretBox.from_env())


# ─── plan ──────────────────────────────────────────────────────────────────────

def due_plan(session: Session, run: store.PaperRun, now: datetime,
             bars: Optional[List[engine.Bar]] = None) -> Optional[Dict[str, object]]:
    """Plan do wysłania teraz albo None (dzień nieaktywny, zakres jeszcze trwa, zlecenia wygasły…)."""
    v = store.VERSIONS.get(run.version)
    if v is None or run.status != "active" or not run.alerts or run.fingerprint != v.fingerprint:
        return None
    day = store.trading_day(now)
    if day.isoformat() < run.first_day:
        return None
    if bars is None:
        bars = store.load_bars(session, v.asset, now - timedelta(days=21))
    res = engine.evaluate(bars, v.params, now=now)
    last = res[-1] if res else None
    if last is None or last.status != "pending" or last.day != day or not last.nr7 or len(last.orders) != 2:
        return None
    days = engine.group_days(bars)
    if not days or days[-1][0] != day:
        return None
    closed = 8 * 60 + int(v.params["range_min"])                    # 09:00 Londynu
    if not any(s.dmin >= 300 and s.ldn_min >= closed for s in days[-1][1]):
        return None                                                  # świecy z 09:00 jeszcze nie ma
    cancel = last.orders[0].cancel_if_no_fill
    if cancel is not None and now >= cancel:
        return None                                                  # po 12:00 Londynu plan jest nieaktualny
    eq = store.equity(session, run)
    orders = [{"direction": o.direction, "entry": round(o.entry, 2), "stop": round(o.stop, 2),
               "lots": store._lots(eq, run.risk_pct, o.entry, o.stop, run)} for o in last.orders]
    return {"day": day, "run_id": run.id, "name": run.name, "version": run.version, "equity": eq,
            "risk_pct": run.risk_pct, "risk_usd": (eq * run.risk_pct / Decimal(100)).quantize(Decimal("0.01")),
            "orders": orders, "valid_until": cancel, "flat_by": last.orders[0].deadline}


# ─── formatowanie ──────────────────────────────────────────────────────────────

def _times(ts: Optional[datetime]) -> str:
    if ts is None:
        return "—"
    return f"{ts.astimezone(engine.LONDON):%H:%M} Londyn ({ts.astimezone(WARSAW):%H:%M} Warszawa)"


def _lots(x: Decimal) -> str:
    return f"{x:.2f} lota" if x > 0 else "poniżej 0,01 lota przy tym saldzie i ryzyku — pomiń"


def _money(x: Decimal) -> str:
    return f"{x:,.2f}".replace(",", " ")


def _order_lines(p: Dict[str, object]) -> List[str]:
    out = []
    for o in p["orders"]:
        side = "🟢 KUPNO STOP" if o["direction"] == 1 else "🔴 SPRZEDAŻ STOP"
        out.append(f"{side} {o['entry']:.2f} · SL {o['stop']:.2f} · {_lots(o['lots'])}")
    return out


def _rules(p: Dict[str, object]) -> List[str]:
    return [
        "OCO: gdy jedno zlecenie się wypełni, anuluj drugie.",
        f"Zlecenia ważne do {_times(p['valid_until'])} — potem anuluj niewypełnione.",
        f"Pozycję zamknij najpóźniej o {_times(p['flat_by'])}.",
        f"Wielkość: kapitał {_money(p['equity'])} USD, ryzyko {p['risk_pct']:g}% (≈ {_money(p['risk_usd'])} USD do SL).",
        "Poziomy z cen dostawcy świec — u brokera kurs może różnić się o spread.",
    ]


def _title(p: Dict[str, object]) -> str:
    return f"📋 {brief.BRAND} · plan dnia {p['day']}"


def to_telegram(p: Dict[str, object], app_url: str = "") -> str:
    e = html.escape
    lines = [f"<b>{e(_title(p))}</b>", f"<b>{e(str(p['name']))}</b> · {e(str(p['version']))}",
             "Dzień aktywny (po NR7), zakres 08:00–09:00 Londynu zamknięty.", ""]
    lines += [f"<b>{e(x)}</b>" for x in _order_lines(p)]
    lines += [""] + [e(x) for x in _rules(p)]
    lines += ["", f"<i>{e(DISCLAIMER)}</i>"]
    if app_url:
        lines.append(f"<a href=\"{e(app_url, quote=True)}/paper\">Przebieg w {brief.BRAND}</a>")
    return "\n".join(lines)


def to_discord(p: Dict[str, object], app_url: str = "") -> Dict[str, object]:
    embed = {"title": _title(p)[:256],
             "description": (f"**{p['name']}** · {p['version']}\nDzień aktywny (po NR7), zakres 08:00–09:00 Londynu "
                             "zamknięty.\n\n" + "\n".join(f"**{x}**" for x in _order_lines(p)) + "\n\n"
                             + "\n".join(_rules(p)))[:4000],
             "color": 0xC9A86A, "footer": {"text": DISCLAIMER}}
    if app_url:
        embed["url"] = f"{app_url}/paper"
    return embed


# ─── wysyłka ───────────────────────────────────────────────────────────────────

def _deliver(session: Session, run: store.PaperRun, p: Dict[str, object], ch: Channels, now: datetime,
             app_url: str) -> Tuple[int, int]:
    sub = session.get(brief.BriefSubscription, run.account)
    if sub is None:
        return 0, 0
    day = p["day"].isoformat()
    targets: List[Tuple[str, Callable[[], None]]] = []
    if ch.tg is not None and sub.telegram_chat_id:
        text = to_telegram(p, app_url)
        targets.append((f"tg:{sub.telegram_chat_id}", lambda c=sub.telegram_chat_id: ch.tg.send(c, text)))
    if ch.box is not None and sub.discord_webhook:
        embed = to_discord(p, app_url)

        def send(s=sub):
            brief.send_discord(ch.box.decrypt(s.discord_webhook, brief.webhook_context(s.account)).decode(), embed, ch.http)
        targets.append((f"dc:{sub.account}", send))
    done = set(session.scalars(select(PaperAlertDelivery.target).where(PaperAlertDelivery.run_id == run.id,
                                                                         PaperAlertDelivery.day == day)))
    sent = failed = 0
    for target, fn in targets:
        target = target[:96]
        if target in done:
            continue
        err = ""
        try:
            fn()
            sent += 1
        except Exception as exc:  # bez adresów i tokenów — tylko typ i krótki opis
            err = f"{type(exc).__name__}: {str(exc)[:200]}"
            failed += 1
        session.add(PaperAlertDelivery(run_id=run.id, day=day, target=target, sent_at=now, ok=not err, error=err))
        session.commit()                                 # zapis po każdej próbie — restart nie wyśle drugi raz
    return sent, failed


def send_due(session: Session, now: Optional[datetime] = None, ch: Optional[Channels] = None,
             app_url: str = "") -> Dict[str, int]:
    """Wyślij plany dnia, które są już gotowe, a jeszcze nie poszły. Błąd jednego przebiegu nie blokuje innych."""
    now = now or datetime.now(timezone.utc)
    ch = ch or Channels()
    if ch.tg is None and ch.box is None:
        return {"sent": 0, "failed": 0}
    runs = list(session.scalars(select(store.PaperRun).where(store.PaperRun.status == "active",
                                                             store.PaperRun.alerts.is_(True))))
    bars_by_asset: Dict[str, List[engine.Bar]] = {}
    sent = failed = 0
    for run in runs:
        run_id = run.id
        try:
            v = store.VERSIONS.get(run.version)
            if v is None:
                continue
            if v.asset not in bars_by_asset:
                bars_by_asset[v.asset] = store.load_bars(session, v.asset, now - timedelta(days=21))
            p = due_plan(session, run, now, bars_by_asset[v.asset])
            if p is None:
                continue
            s, f = _deliver(session, run, p, ch, now, app_url)
            sent, failed = sent + s, failed + f
        except Exception as exc:  # jeden przebieg nie zatrzymuje pozostałych
            session.rollback()
            log.warning("paper alert %s: %s", run_id, exc)
    return {"sent": sent, "failed": failed}
