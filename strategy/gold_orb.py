"""Złoto: London opening-range breakout po dniu NR7 — plan dnia, paper trading i alert.

Status: KANDYDAT DO PAPER TRADINGU, nie strategia do handlu prawdziwymi pieniędzmi.
Badanie (docs/BACKTEST_RESULTS.md, research/intraday_lab.py): XAUUSD M15 2012–2022, parametry wybrane
na 2012–2016, poza próbą 2017–2022: +4,0 bps/transakcję, t = 1,19, dodatni w 5 z 6 lat, 184 transakcje.
Nieistotne statystycznie (próg Bonferroni dla 8 strategii: t > 2,73). Zysk znika przy koszcie ~1 USD/oz.

Reguły (dokładnie te same funkcje co w backteście — zero rozjazdu między testem a live):
- dzień handlowy od 17:00 NY; filtr: wczorajszy zakres najwęższy z 7 dni (NR7);
- zakres 08:00–09:00 czasu Londynu; zlecenia stop: kupno 10% szerokości ponad szczytem, sprzedaż 10% pod dołkiem
  (OCO — pierwsze wypełnione anuluje drugie); SL = przeciwna strona zakresu;
- zlecenia ważne do 12:00 Londynu, wyjście najpóźniej 16:00 Londynu; brak pozycji na noc.

CLI:
  python -m strategy.gold_orb --csv XAUUSD-m15.csv --plan 2022-03-03       # plan na dzień z pliku
  python -m strategy.gold_orb --csv XAUUSD-m15.csv --paper 60              # replay ostatnich 60 dni
  python -m strategy.gold_orb --live [--discord URL]                       # GC=F z Yahoo, plan na dziś
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import List, Optional

import pandas as pd

from research import intraday_lab as lab

PARAMS = {"range_min": 60, "stop": "range", "tp_r": None, "buffer": 0.1}   # wybrane na 2012–2016
COST_USD = 0.40
EXPECTED_BPS = 4.0          # wynik OOS z badania — punkt odniesienia dla paper tradingu


@dataclass
class DayPlan:
    day: str
    active: bool
    reason: str
    range_high: Optional[float] = None
    range_low: Optional[float] = None
    buy_stop: Optional[float] = None
    sell_stop: Optional[float] = None
    long_sl: Optional[float] = None
    short_sl: Optional[float] = None
    orders_valid_until_utc: Optional[str] = None
    flat_by_utc: Optional[str] = None
    lots: Optional[float] = None
    risk_usd: Optional[float] = None


def prepare(m15: pd.DataFrame) -> pd.DataFrame:
    """Świece M15 w UTC (open/high/low/close) → kolumny sesji jak w laboratorium."""
    return lab.with_sessions(m15[["open", "high", "low", "close"]].sort_index())


def plan_for_day(df: pd.DataFrame, day: pd.Timestamp, balance: Optional[float] = None,
                 risk_pct: float = 0.5, contract_oz: float = 100.0, lot_step: float = 0.01) -> DayPlan:
    ctx = lab.daily_context(df)
    bars = df[df.day == day].copy()
    label = day.strftime("%Y-%m-%d")
    if bars.empty:
        return DayPlan(label, False, "brak danych dla dnia")
    if day not in ctx.index or not bool(ctx.at[day, "nr7"]):
        return DayPlan(label, False, "wczoraj nie był dzień NR7 — dziś bez transakcji")
    bars.attrs["nr7"] = True
    orders = lab.london_orb_nr7(bars, PARAMS)
    if not orders:
        return DayPlan(label, False, "zakres 08:00–09:00 Londynu jeszcze niekompletny albo brak danych")
    buy = next(o for o in orders if o.direction == 1)
    sell = next(o for o in orders if o.direction == -1)
    r = lambda v: round(float(v), 2)  # noqa: E731
    plan = DayPlan(label, True, "NR7 wczoraj — plan aktywny",
                   range_high=r(sell.stop), range_low=r(buy.stop),
                   buy_stop=r(buy.entry), sell_stop=r(sell.entry),
                   long_sl=r(buy.stop), short_sl=r(sell.stop),
                   orders_valid_until_utc=buy.cancel_if_no_fill.isoformat() if buy.cancel_if_no_fill is not None else None,
                   flat_by_utc=buy.deadline.isoformat())
    if balance:
        risk_usd = balance * risk_pct / 100
        per_lot = abs(buy.entry - buy.stop) * contract_oz
        lots = math.floor(risk_usd / per_lot / lot_step) * lot_step if per_lot > 0 else 0
        plan.lots, plan.risk_usd = round(float(lots), 2), round(float(lots * per_lot), 2)
    return plan


def paper(df: pd.DataFrame, days: int, cost: float = COST_USD) -> pd.DataFrame:
    """Replay ostatnich `days` dni tym samym silnikiem co backtest (paper trading na historii)."""
    last = sorted(df.day.unique())[-days:]
    return lab.backtest(df, lab.london_orb_nr7, PARAMS, cost, set(last))


def paper_summary(tr: pd.DataFrame) -> str:
    s = lab.stats(tr)
    if not s["n"]:
        return "Brak transakcji w okresie (NR7 zdarza się ~1 dzień na 7)."
    return (f"{s['n']} transakcji · średnio {s['mean_bps']:.1f} bps (badanie: {EXPECTED_BPS:.1f}) · "
            f"win {s['win']:.0%} · PF {s['pf']:.2f} · wynik {s['net_usd']:.1f} USD/oz")


def discord_embed(plan: DayPlan) -> dict:
    if not plan.active:
        return {"title": f"Złoto · London ORB · {plan.day}", "description": plan.reason, "color": 0x8A8A93}
    fields = [
        {"name": "Zakres 08–09 LDN", "value": f"{plan.range_low} – {plan.range_high}", "inline": True},
        {"name": "Kupno stop", "value": f"{plan.buy_stop} (SL {plan.long_sl})", "inline": True},
        {"name": "Sprzedaż stop", "value": f"{plan.sell_stop} (SL {plan.short_sl})", "inline": True},
        {"name": "Ważne do / zamknij do (UTC)", "value": f"{plan.orders_valid_until_utc} / {plan.flat_by_utc}", "inline": False},
    ]
    if plan.lots is not None:
        fields.append({"name": "Wielkość", "value": f"{plan.lots} lota · ryzyko {plan.risk_usd} USD", "inline": True})
    return {"title": f"Złoto · London ORB (NR7) · {plan.day}",
            "description": "PAPER TRADING — kandydat bez istotnej przewagi statystycznej. OCO: pierwsze wypełnione anuluje drugie.",
            "color": 0xC9A86A, "fields": fields}


def fetch_live() -> pd.DataFrame:
    """GC=F (futures COMEX) z Yahoo, 15 min, ~60 dni. Poziomy liczone na futures — przenieś na swój instrument."""
    from fetchers.yfinance import YFinanceDataFetcher

    df = YFinanceDataFetcher().fetch_ohlcv("GC=F", "15m", period="60d", force_refresh=True)
    if df is None or df.empty:
        raise SystemExit("Brak danych z Yahoo Finance (GC=F 15m)")
    return df


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", help="M15 z MT5 (czas serwera EET/EEST, ceny ×100 jak w ejtraderLabs)")
    ap.add_argument("--price-scale", type=float, default=100.0)
    ap.add_argument("--plan", help="dzień handlowy YYYY-MM-DD")
    ap.add_argument("--paper", type=int, help="replay ostatnich N dni")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--balance", type=float)
    ap.add_argument("--risk-pct", type=float, default=0.5)
    ap.add_argument("--discord", default=os.getenv("DISCORD_WEBHOOK_URL", ""))
    ap.add_argument("--log", default="gold_orb_paper.csv", help="plik dziennika planów (paper)")
    args = ap.parse_args(argv)

    if args.live:
        df = prepare(fetch_live())
        day = pd.Timestamp(sorted(df.day.unique())[-1])
    else:
        if not args.csv:
            raise SystemExit("--csv albo --live")
        df = prepare(lab.load_m15(args.csv, args.price_scale))
        day = pd.Timestamp(args.plan) if args.plan else pd.Timestamp(sorted(df.day.unique())[-1])

    if args.paper:
        tr = paper(df, args.paper)
        print(paper_summary(tr))
        if not tr.empty:
            print(tr[["day", "dir", "entry", "exit", "reason", "net"]].tail(20).to_string(index=False))
        return

    plan = plan_for_day(df, day, args.balance, args.risk_pct)
    for k, v in asdict(plan).items():
        print(f"{k:24s} {v}")
    new = not os.path.exists(args.log)
    with open(args.log, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["logged_at", *asdict(plan)])
        if new:
            w.writeheader()
        w.writerow({"logged_at": datetime.now(timezone.utc).isoformat(), **asdict(plan)})
    if args.discord:
        from notifiers.discord import DiscordNotifier

        DiscordNotifier(args.discord, bot_name="Gold ORB").send_custom_embed(discord_embed(plan))


if __name__ == "__main__":
    main()
