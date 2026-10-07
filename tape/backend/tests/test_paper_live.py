from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tape.api import create_app
from tape.db import FillRow, make_sessionmaker
from tape.paper import live, store
from tape.paper.engine import LONDON
from test_auth import ISSUER, LocalVerifier
from test_paper import hdr, history, start_run

LONG = {"10:00": (2002, 2006, 2001, 2005)}                     # wybicie w górę: kupno stop 2004.8, SL 1996
STOPPED = {"10:00": (2003, 2005, 1995, 1996)}                  # wejście i SL w tej samej świecy


@pytest.fixture
def S(tmp_path):
    return make_sessionmaker(f"sqlite:///{tmp_path / 'live.db'}")


def at(day, hh, mm=0):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=LONDON)


def setup(S, overrides=None, last_width=4.0, upto=None):
    """Zapisuje CAŁY dzień sygnału (także przyszłe świece) — podgląd musi sam uciąć to, co jeszcze się nie zamknęło.
    upto=(hh, mm): worker spóźniony — w bazie tylko świece otwarte przed tą godziną Londynu."""
    bars, day = history(last_width=last_width, signal_overrides=overrides)
    if upto:
        bars = [b for b in bars if b.ts < at(day, *upto)]
    with S() as s:
        store.save_bars(s, "XAU", bars, "test")
        run_id = start_run(s, day).id
        s.commit()
    return run_id, day


def look(S, run_id, now):
    with S() as s:
        run = s.get(store.PaperRun, run_id)
        before = (run.status, run.last_processed_at)
        out = live.preview(s, run, now)
        assert not (s.new or s.dirty or s.deleted)                              # podgląd tylko czyta
        assert (run.status, run.last_processed_at) == before
        assert s.scalar(select(func.count()).select_from(store.PaperDay)) == 0
        assert s.scalar(select(func.count()).select_from(FillRow)) == 0
        return out


@pytest.mark.parametrize("overrides,hh,mm,status", [
    (LONG, 8, 40, "waiting_for_range"),           # zakres 08:00–09:00 jeszcze trwa
    (None, 9, 30, "orders_working"),
    (LONG, 11, 0, "in_position"),
    (STOPPED, 11, 0, "stopped_out"),
    (None, 13, 0, "flat_awaiting_close"),          # bez wypełnienia do 12:00 — zlecenia wygasły
    (LONG, 16, 5, "flat_awaiting_close"),          # zamknięcie o 16:00, świeca z 16:00 jeszcze trwa
])
def test_preview_status(S, overrides, hh, mm, status):
    run_id, day = setup(S, overrides)
    p = look(S, run_id, at(day, hh, mm))
    assert p["provisional"] is True and p["status"] == status and p["day"] == day.isoformat()
    if status == "waiting_for_range":
        assert p["orders"] == [] and p["entry"] is None
    else:
        assert [o["entry"] for o in p["orders"]] == [2004.8, 1995.2]
    if (hh, status) == (16, "flat_awaiting_close"):
        assert (p["reason"], p["exit"], p["entry"]) == ("time", 2000, 2004.8)


def test_in_position_numbers_and_no_look_ahead(S):
    # w danych jest późniejszy SL o 14:00 — o 11:00 nie wolno go jeszcze widzieć
    run_id, day = setup(S, {**LONG, "14:00": (2000, 2001, 1990, 1992)})
    p = look(S, run_id, at(day, 11))
    assert p["status"] == "in_position" and p["exit"] is None and p["reason"] is None
    assert (p["direction"], p["entry"], p["stop"], p["mark"]) == (1, 2004.8, 1996, 2000)
    assert p["entry_time"] == at(day, 10).astimezone(timezone.utc).isoformat()
    assert p["lots"] == 0.05 and p["net"] == pytest.approx(-5.2) and p["pnl_usd"] == -26.0   # 5 oz × −5,2
    assert p["as_of"] == at(day, 11).astimezone(timezone.utc).isoformat()
    assert look(S, run_id, at(day, 14, 30))["status"] == "stopped_out"


def test_status_follows_stored_bars_not_the_clock(S):
    run_id, day = setup(S, upto=(11, 30))                                        # brak świec 11:30 i 11:45
    assert look(S, run_id, at(day, 12, 3))["status"] == "orders_working"        # 11:45 mogła jeszcze wypełnić
    run_id, day = setup(S, LONG, upto=(15, 30))
    p = look(S, run_id, at(day, 16, 5))
    assert p["status"] == "in_position" and p["exit"] is None


@pytest.mark.parametrize("overrides", [LONG, STOPPED])
def test_preview_matches_official_result(S, overrides):
    run_id, day = setup(S, overrides)
    p = look(S, run_id, at(day, 11))
    with S() as s:
        run = s.get(store.PaperRun, run_id)
        assert live.preview(s, run, at(day, 16, 15)) is None                     # po 16:15 rozlicza process_run
        store.process_run(s, run, at(day, 18))
        d = s.scalars(select(store.PaperDay)).one()
    assert (d.direction, d.entry, d.stop) == (p["direction"], p["entry"], p["stop"])
    assert d.entry_time.isoformat() == p["entry_time"] and float(d.lots) == p["lots"]
    if p["status"] == "stopped_out":
        assert (d.exit, d.reason, d.exit_time.isoformat(), float(d.pnl_usd)) == (
            p["exit"], "sl", p["exit_time"], p["pnl_usd"])


def test_no_preview_for_inactive_stopped_or_changed_runs(S, monkeypatch):
    run_id, day = setup(S, LONG, last_width=12.0)                                # wczoraj nie NR7
    assert look(S, run_id, at(day, 11)) is None
    run_id, day = setup(S, LONG)
    with S() as s:                                                               # dzień przed startem przebiegu
        run = s.get(store.PaperRun, run_id)
        run.first_day = (day + timedelta(days=1)).isoformat()
        s.commit()
    assert look(S, run_id, at(day, 11)) is None
    for status in ("stopped", "mismatch"):
        with S() as s:
            run = s.get(store.PaperRun, run_id)
            run.first_day, run.status = day.isoformat(), status
            s.commit()
        assert look(S, run_id, at(day, 11)) is None
    with S() as s:
        s.get(store.PaperRun, run_id).status = "active"
        s.commit()
    assert look(S, run_id, at(day, 11))["status"] == "in_position"
    monkeypatch.setattr(store, "engine_sha256", lambda: "0" * 64)              # kod zmieniony: brak podglądu,
    assert look(S, run_id, at(day, 11)) is None                                  # ale status zmienia tylko worker


def test_api_returns_live_key(tmp_path):
    url = f"sqlite:///{tmp_path / 'api.db'}"
    verify = LocalVerifier("https://unused/jwks.json", ISSUER, authorized_parties=("https://app.example.com",))
    c = TestClient(create_app(url, verifier=verify))
    run = c.post("/api/paper/runs", headers=hdr("a"), json={"version": "london-orb-nr7@1", "balance": 10000,
                                                            "risk_pct": 0.5}).json()
    detail = c.get(f"/api/paper/runs/{run['id']}", headers=hdr("a")).json()
    assert "live" in detail and detail["live"] is None                          # brak świec → brak podglądu
