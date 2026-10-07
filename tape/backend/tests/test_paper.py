import io
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tape.api import create_app
from tape.db import make_sessionmaker
from tape.paper import engine, store, worker
from tape.paper.engine import LONDON, NEW_YORK, Bar
from test_auth import ISSUER, LocalVerifier, token
from test_review import deals_csv

P = store.VERSIONS["london-orb-nr7@1"].params
FIRST = date(2026, 9, 7)                       # 8 dni handlowych: 7 dni kontekstu + dzień z sygnałem


def day_bars(d: date, base: float = 2000.0, width: float = 10.0, overrides=None):
    """Pełny dzień handlowy M15: 17:00 NY dnia poprzedniego → 17:00 NY. overrides: {"HH:MM" Londyn: (o, h, l, c)}."""
    start = datetime(d.year, d.month, d.day, 17, tzinfo=NEW_YORK) - timedelta(days=1)
    out = []
    for i in range(96):
        ts = (start + timedelta(minutes=15 * i)).astimezone(timezone.utc)
        key = ts.astimezone(LONDON).strftime("%H:%M")
        o, h, lo, c = (overrides or {}).get(key, (base, base + width / 2, base - width / 2, base))
        out.append(Bar(ts, o, h, lo, c, 0.3))
    return out


def history(last_width: float = 4.0, signal_day=None, signal_overrides=None, n_context: int = 7):
    """Dni kontekstu (ostatni najwęższy → NR7) + dzień sygnału z zakresem Londynu 1996–2004 (szer. 8)."""
    bars, d = [], FIRST
    for i in range(n_context):
        bars += day_bars(d, width=10.0 if i < n_context - 1 else last_width)
        d += timedelta(days=1)
    rng = {"08:00": (2000, 2004, 1996, 2000), "08:15": (2000, 2003, 1997, 2000),
           "08:30": (2000, 2002, 1998, 2000), "08:45": (2000, 2001, 1999, 2000)}
    flat = (2000, 2001, 1999, 2000)
    ov = {f"{h:02d}:{m:02d}": flat for h in range(9, 17) for m in (0, 15, 30, 45)}
    ov.update(rng)
    ov.update(signal_overrides or {})
    bars += day_bars(signal_day or d, base=2000, width=2.0, overrides=ov)
    return bars, (signal_day or d)


def result_for(bars, day, **kw):
    return next(r for r in engine.evaluate(bars, P, **kw) if r.day == day)


# ─── silnik ────────────────────────────────────────────────────────────────────

def test_long_breakout_with_time_exit():
    bars, day = history(signal_overrides={"10:00": (2002, 2006, 2001, 2005), "15:45": (2010, 2011, 2009, 2010)})
    r = result_for(bars, day)
    assert r.status == "trade" and r.nr7
    t = r.trade                                   # zakres 1996–2004, bufor 0,8 → kupno stop 2004.8, SL 1996
    assert (t.direction, t.entry, t.stop, t.reason, t.exit) == (1, 2004.8, 1996, "time", 2010)
    assert t.exit_time.astimezone(LONDON).strftime("%H:%M") == "15:45"     # ostatnia świeca przed 16:00


def test_gap_fills_at_open_and_stop_inside_entry_bar():
    bars, day = history(signal_overrides={"10:00": (2006, 2007, 2005, 2006)})
    assert result_for(bars, day).trade.entry == 2006                       # luka ponad poziom → otwarcie
    bars, day = history(signal_overrides={"10:00": (2003, 2005, 1995, 1996)})
    t = result_for(bars, day).trade
    assert (t.reason, t.exit, t.entry_time) == ("sl", 1996, t.exit_time)   # SL w świecy wejścia


def test_oco_tie_goes_to_long_and_cancel_after_noon():
    bars, day = history(signal_overrides={"10:00": (2000, 2006, 1994, 2000)})
    assert result_for(bars, day).trade.direction == 1                      # obie strony w tej samej świecy
    bars, day = history(signal_overrides={"12:00": (2003, 2007, 2002, 2006)})
    assert result_for(bars, day).status == "no_fill"                       # po 12:00 Londynu zlecenia wygasają


def test_nr7_pending_and_data_gaps():
    bars, day = history(last_width=12.0, signal_overrides={"10:00": (2002, 2006, 2001, 2005)})
    assert result_for(bars, day).status == "no_nr7"
    bars, day = history(signal_overrides={"10:00": (2002, 2006, 2001, 2005)})
    now = datetime(day.year, day.month, day.day, 15, 0, tzinfo=LONDON)
    live = [b for b in bars if b.ts < now]
    r = result_for(live, day, now=now)
    assert r.status == "pending" and r.nr7 and r.trade is None and [o.entry for o in r.orders] == [2004.8, 1995.2]
    holes = [b for b in bars if not (b.ts.date() == FIRST + timedelta(days=2) and b.ts.hour < 10)]
    assert result_for(holes, day).status == "data_gap"                     # dziura u dostawcy w oknie NR7
    no_range = [b for b in bars if b.ts.astimezone(LONDON).strftime("%H:%M") != "08:30"]
    gap = result_for(no_range, day)
    assert gap.status == "data_gap" and "3 z 4" in gap.note
    assert result_for(no_range, day, check_gaps=False).status != "data_gap"


def test_engine_file_is_frozen_for_the_version():
    v = store.VERSIONS["london-orb-nr7@1"]
    assert store.engine_sha256() == v.engine_sha256, (
        "Zmieniono tape/paper/engine.py: dodaj NOWĄ wersję strategii w store.VERSIONS (nowe id i skrót), "
        "nie podmieniaj skrótu istniejącej — przebiegi starej wersji muszą się zatrzymać.")


# ─── przebiegi ─────────────────────────────────────────────────────────────────

@pytest.fixture
def S(tmp_path):
    return make_sessionmaker(f"sqlite:///{tmp_path / 'p.db'}")


def start_run(s, day, balance=Decimal(10000), risk=Decimal("0.5"), account="a"):
    started = datetime.combine(day - timedelta(days=1), datetime.min.time(), tzinfo=NEW_YORK).replace(hour=12)
    return store.create_run(s, account, "london-orb-nr7@1", balance, risk, now=started.astimezone(timezone.utc))


def test_run_is_forward_only_idempotent_and_sized(S):
    bars, day = history(signal_overrides={"10:00": (2002, 2006, 2001, 2005), "15:45": (2010, 2011, 2009, 2010)})
    earlier, eday = history(signal_day=day - timedelta(days=1),
                            signal_overrides={"10:00": (2002, 2006, 2001, 2005)}, n_context=6)
    with S() as s:
        store.save_bars(s, "XAU", bars, "test")
        run = start_run(s, day)
        assert run.first_day == day.isoformat()
        now = datetime(day.year, day.month, day.day, 18, tzinfo=LONDON)
        assert store.process_run(s, run, now)["recorded"] == 1
        assert store.process_run(s, run, now)["recorded"] == 0             # drugi raz nic
        s.commit()
        d = s.scalars(select(store.PaperDay)).one()
        # ryzyko 0,5% z 10 000 = 50 USD; 1 lot = 100 oz × 8,8 USD = 880 USD → 0,05 lota
        assert d.status == "trade" and d.lots == Decimal("0.05")
        assert d.net == pytest.approx(5.2 - 0.40) and d.net_spread == pytest.approx(5.2 - 0.3)
        assert d.pnl_usd == Decimal("24.00") and store.equity(s, run) == Decimal("10024.00")
        p = store.progress(s, run)
        assert p["trades"] == 1 and p["passed"] is False and "1 z 100" in p["verdict"]


def test_days_before_the_run_never_trade(S):
    bars, day = history(signal_overrides={"10:00": (2002, 2006, 2001, 2005)})
    with S() as s:
        store.save_bars(s, "XAU", bars, "test")
        started = datetime(day.year, day.month, day.day, 6, tzinfo=LONDON).astimezone(timezone.utc)
        run = store.create_run(s, "a", "london-orb-nr7@1", Decimal(10000), Decimal("0.5"), now=started)
        store.process_run(s, run, started + timedelta(days=3))
        assert run.first_day > day.isoformat()                             # start w trakcie dnia → od następnego
        assert not s.scalars(select(store.PaperDay).where(store.PaperDay.day == day.isoformat())).all()


def test_tiny_account_counts_the_signal_but_takes_no_position(S):
    bars, day = history(signal_overrides={"10:00": (2002, 2006, 2001, 2005)})
    with S() as s:
        store.save_bars(s, "XAU", bars, "test")
        run = start_run(s, day, balance=Decimal(500), risk=Decimal("0.5"))
        store.process_run(s, run, datetime(day.year, day.month, day.day, 18, tzinfo=LONDON))
        d = s.scalars(select(store.PaperDay)).one()
        assert d.status == "trade" and d.lots == 0 and d.pnl_usd == 0 and "minimalnego lota" in d.note


def test_changed_engine_stops_runs_and_blocks_new_ones(S, monkeypatch):
    bars, day = history()
    with S() as s:
        store.save_bars(s, "XAU", bars, "test")
        run = start_run(s, day)
        monkeypatch.setattr(store, "engine_sha256", lambda: "0" * 64)
        assert store.process_run(s, run)["mismatch"] == 1 and run.status == "mismatch"
        with pytest.raises(store.PaperError, match="nowa wersja"):
            start_run(s, day)
        assert "nic już nie dowodzi" in store.progress(s, run)["verdict"]


def test_risk_and_balance_limits(S):
    with S() as s:
        for bal, risk in ((Decimal(10000), Decimal("2.5")), (Decimal(10000), Decimal("0.05")), (Decimal(50), Decimal(1))):
            with pytest.raises(store.PaperError):
                start_run(s, FIRST, balance=bal, risk=risk)


def test_progress_t_stat_and_verdict(S):
    with S() as s:
        run = start_run(s, FIRST)
        for i, x in enumerate([5.0, 7.0, 3.0, 9.0, 1.0] * 20):              # n = 100, średnio 5, sd ≈ 2,84
            s.add(store.PaperDay(run_id=run.id, day=f"2027-{1 + i // 28:02d}-{1 + i % 28:02d}", status="trade",
                                 net_bps=x))
        s.add(store.PaperDay(run_id=run.id, day="2028-01-01", status="no_nr7"))
        s.flush()
        p = store.progress(s, run)
        assert p["trades"] == 100 and p["mean_bps"] == pytest.approx(5.0)
        assert p["t_stat"] == pytest.approx(5.0 / (2.8427 / 10), rel=1e-3) and p["passed"] is True


# ─── API: izolacja od prawdziwych rachunków ───────────────────────────────────

def hdr(sub):
    return {"Authorization": f"Bearer {token(sub)}"}


def test_paper_book_stays_out_of_real_stats(tmp_path):
    url = f"sqlite:///{tmp_path / 'api.db'}"
    verify = LocalVerifier("https://unused/jwks.json", ISSUER, authorized_parties=("https://app.example.com",))
    c = TestClient(create_app(url, verifier=verify))
    c.post("/api/imports", headers=hdr("a"), files={"file": ("d.csv", io.BytesIO(deals_csv(12)), "text/csv")})
    before = c.get("/api/stats", headers=hdr("a")).json()["summary"]
    facts_before = c.get("/api/review", headers=hdr("a")).json()["trades"]
    run = c.post("/api/paper/runs", headers=hdr("a"), json={"version": "london-orb-nr7@1", "balance": 10000,
                                                            "risk_pct": 0.5}).json()
    bars, day = history(signal_overrides={"10:00": (2002, 2006, 2001, 2005)})
    S = make_sessionmaker(url)
    with S() as s:
        store.save_bars(s, "XAU", bars, "test")
        r = s.get(store.PaperRun, run["id"])
        r.first_day = day.isoformat()
        store.process_run(s, r, datetime(day.year, day.month, day.day, 18, tzinfo=LONDON))
        s.commit()
    assert c.get("/api/stats", headers=hdr("a")).json()["summary"] == before            # „wszystkie” bez paper
    assert c.get("/api/review", headers=hdr("a")).json()["trades"] == facts_before
    assert c.get("/api/portfolio", headers=hdr("a")).json()["deposits"] in (0, 0.0, None)
    paper_stats = c.get(f"/api/stats?book={run['book']}", headers=hdr("a")).json()["summary"]
    assert paper_stats["trades"] == 1                                                      # po jawnym wyborze — tak
    books = c.get("/api/books", headers=hdr("a")).json()
    assert {"id": run["book"], "kind": "paper"}.items() <= next(b for b in books if b["kind"] == "paper").items()
    detail = c.get(f"/api/paper/runs/{run['id']}", headers=hdr("a")).json()
    assert detail["days"][0]["status"] == "trade" and detail["progress"]["trades"] == 1
    # cudzy przebieg, wstrzykiwanie do rachunku paper, za duże ryzyko
    assert c.get(f"/api/paper/runs/{run['id']}", headers=hdr("b")).status_code == 404
    assert c.post(f"/api/paper/runs/{run['id']}/stop", headers=hdr("b")).status_code == 404
    imp = c.post("/api/imports", headers=hdr("a"), data={"book": run["book"]},
                 files={"file": ("d.csv", io.BytesIO(deals_csv(3, start=700)), "text/csv")})
    assert imp.status_code == 400
    assert c.post("/api/cashflows", headers=hdr("a"), json={"book": run["book"], "ts": "2026-09-01T00:00:00Z",
                                                            "amount": 1000}).status_code == 400
    assert c.post("/api/paper/runs", headers=hdr("a"), json={"version": "london-orb-nr7@1", "risk_pct": 5}).status_code == 422
    assert c.post(f"/api/paper/runs/{run['id']}/stop", headers=hdr("a")).json()["status"] == "stopped"


# ─── dostawcy świec M15 ────────────────────────────────────────────────────────

NOW = datetime(2026, 10, 7, 10, 7, tzinfo=timezone.utc)


def test_oanda_m15_uses_bid_and_spread_and_skips_incomplete():
    from tape.market import Oanda

    body = {"candles": [
        {"time": "2026-10-07T09:45:00.000000000Z", "complete": True,
         "bid": {"o": "2650.10", "h": "2651.00", "l": "2649.80", "c": "2650.50"},
         "ask": {"o": "2650.40", "h": "2651.30", "l": "2650.10", "c": "2650.80"}},
        {"time": "2026-10-07T10:00:00.000000000Z", "complete": False,
         "bid": {"o": "2650.50", "h": "2650.90", "l": "2650.20", "c": "2650.70"},
         "ask": {"o": "2650.80", "h": "2651.20", "l": "2650.50", "c": "2651.00"}}]}
    seen = []

    def opener(url, headers):
        seen.append(url)
        return json.dumps(body).encode()

    bars = Oanda("tok", opener=opener).fetch_m15("XAU", NOW)
    assert "granularity=M15" in seen[0] and "price=BA" in seen[0]
    assert bars == [Bar(datetime(2026, 10, 7, 9, 45, tzinfo=timezone.utc), 2650.10, 2651.00, 2649.80, 2650.50, 0.3)]


def test_twelvedata_m15_and_worker(S):
    from tape.market import GoldApi, TwelveData

    body = {"values": [{"datetime": "2026-10-07 09:45:00", "open": "1", "high": "2", "low": "0.5", "close": "1.5"},
                       {"datetime": "2026-10-07 10:00:00", "open": "1.5", "high": "2", "low": "1", "close": "1.8"}]}
    td = TwelveData("k", opener=lambda u, h: json.dumps(body).encode())
    bars = td.fetch_m15("XAU", NOW)
    assert [b.ts.minute for b in bars] == [45]                             # świeca 10:00 jeszcze trwa
    with S() as s:
        assert worker.run_once(s, td, NOW)["bars_XAU"] == 1
        assert worker.run_once(s, td, NOW)["bars_XAU"] == 0                # ponownie: aktualizacja, bez duplikatu
        assert "bars_XAU" not in worker.run_once(s, GoldApi(opener=lambda u, h: b"{}"), NOW)   # gold-api: brak świec
