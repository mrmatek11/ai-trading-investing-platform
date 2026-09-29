"""HTTP API Tape (FastAPI).

MVP jednego użytkownika: konto „default”, opcjonalny token TAPE_API_TOKEN (nagłówek Authorization: Bearer).
Logowanie użytkowników (Clerk, JWT) wchodzi w kolejnym kroku — zob. PRODUCT_SPEC.md 6.5.

Uruchomienie: uvicorn tape.api:create_app --factory --reload
"""

from __future__ import annotations

import hmac
import json
import os
from datetime import datetime, timezone
from typing import Optional

from decimal import Decimal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from .db import ImportRow, load_fills, make_sessionmaker, store_fills
from .engine import stats
from .engine import prop, risk
from .engine.positions import build_positions
from .importers import BROKERS, generic, parse_file
from .news.bias import aggregate, event_to_dict
from .news.sample import sample_events

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class SizeRequest(BaseModel):
    balance: Decimal = Field(gt=0)
    risk_pct: Decimal = Field(gt=0, le=100)
    entry: Decimal = Field(gt=0)
    stop: Decimal = Field(gt=0)
    contract_size: Decimal = Field(Decimal(100), gt=0)
    lot_step: Decimal = Field(Decimal("0.01"), gt=0)
    min_lot: Decimal = Field(Decimal("0.01"), gt=0)
    max_lot: Decimal | None = Field(None, gt=0)
    daily_range: Decimal | None = Field(None, gt=0)
    daily_loss_limit: Decimal | None = Field(None, gt=0)


class PropRequest(BaseModel):
    initial_balance: Decimal = Field(gt=0)
    daily_loss_pct: Decimal = Field(Decimal(5), gt=0, le=100)
    max_drawdown_pct: Decimal = Field(Decimal(10), gt=0, le=100)
    drawdown_type: str = Field("static", pattern="^(static|trailing)$")
    profit_target_pct: Decimal | None = Field(Decimal(10), gt=0)
    day_tz: str = "Europe/Prague"
    account: str = "default"


def create_app(database_url: Optional[str] = None) -> FastAPI:
    Session = make_sessionmaker(database_url)
    token = os.getenv("TAPE_API_TOKEN", "")

    async def auth(request: Request):
        if not token or request.url.path == "/api/health":
            return
        header = request.headers.get("authorization", "")
        if not hmac.compare_digest(header, f"Bearer {token}"):
            raise HTTPException(status_code=401, detail="Brak lub niepoprawny token")

    app = FastAPI(title="Tape API", version="0.1.0", dependencies=[Depends(auth)])

    def positions_for(account: str):
        with Session() as s:
            return build_positions(load_fills(s, account))

    @app.get("/api/health")
    def health():
        return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

    @app.post("/api/imports")
    async def import_file(
        file: UploadFile = File(...),
        broker: Optional[str] = Form(None),
        tz: Optional[str] = Form(None),
        mapping: Optional[str] = Form(None),
        account: str = Form("default"),
    ):
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Plik większy niż 10 MB")
        if broker and broker not in BROKERS and broker != "generic":
            raise HTTPException(status_code=400, detail=f"Nieznany broker: {broker}")
        parsed_mapping = None
        if mapping:
            try:
                parsed_mapping = generic.Mapping(**json.loads(mapping))
            except (ValueError, TypeError) as exc:
                raise HTTPException(status_code=400, detail=f"Niepoprawne mapowanie: {exc}") from exc
        result = parse_file(data, file.filename or "upload.csv", broker=broker if broker != "generic" else None,
                            tz=tz, mapping=parsed_mapping)
        source = result.detected or (broker or "unknown")
        with Session() as s:
            new, dup = store_fills(s, account, source, result.fills) if result.fills else (0, 0)
            s.add(ImportRow(account=account, source=source, filename=file.filename or "", new=new,
                            duplicates=dup, errors=result.errors[:200]))
            s.commit()
        return {"detected": result.detected or None, "fills": len(result.fills), "new": new,
                "duplicates": dup, "errors": result.errors[:50], "error_count": len(result.errors)}

    @app.get("/api/positions")
    def positions(account: str = "default", limit: int = 200):
        items = positions_for(account)
        items.sort(key=lambda p: p.closed_at or p.opened_at, reverse=True)
        return [{
            "symbol": p.symbol, "direction": "long" if p.direction == 1 else "short",
            "opened_at": p.opened_at.isoformat(), "closed_at": p.closed_at.isoformat() if p.closed_at else None,
            "qty": str(p.qty), "avg_entry": str(round(p.avg_entry, 5)),
            "avg_exit": str(round(p.avg_exit, 5)) if p.avg_exit is not None else None,
            "net_pnl": float(round(p.net_pnl, 2)), "fees": float(round(p.fees, 2)),
            "r_multiple": float(round(p.r_multiple, 2)) if p.r_multiple is not None else None,
            "initial_stop": str(p.initial_stop) if p.initial_stop is not None else None,
        } for p in items[:limit]]

    @app.get("/api/stats")
    def get_stats(account: str = "default"):
        items = positions_for(account)
        return {
            "summary": stats.as_dict(stats.summarize(items)),
            "equity": stats.equity_curve(items),
            "segments": [stats.as_dict(x) for x in stats.segments(items)],
        }

    @app.post("/api/tools/position-size")
    def size(req: SizeRequest):
        try:
            r = risk.position_size(req.balance, req.risk_pct, req.entry, req.stop, req.contract_size,
                                   req.lot_step, req.min_lot, req.max_lot, req.daily_range, req.daily_loss_limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        f = lambda d: float(d) if d is not None else None  # noqa: E731
        return {"lots": f(r.lots), "risk_budget": f(r.risk_budget), "risk_actual": f(r.risk_actual),
                "stop_distance": f(r.stop_distance), "value_per_point": f(r.value_per_point),
                "notional": f(r.notional), "min_lot_risk": f(r.min_lot_risk),
                "daily_range_loss": f(r.daily_range_loss), "daily_limit_share": f(r.daily_limit_share),
                "warnings": list(r.warnings)}

    @app.post("/api/prop/evaluate")
    def prop_evaluate(req: PropRequest):
        try:
            rules = prop.PropRules(req.initial_balance, req.daily_loss_pct, req.max_drawdown_pct,
                                   req.drawdown_type, req.profit_target_pct, req.day_tz, name="Twoje konto")
        except Exception as exc:  # np. nieznana strefa czasowa
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        items = positions_for(req.account)
        try:
            report = prop.evaluate(items, rules)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sims = prop.simulate(items, req.initial_balance)
        return {"report": prop.report_to_dict(report),
                "simulation": {k: {"name": v.rules.name, "status": v.status, "breach": v.breach,
                                   "breach_day": v.breach_day.isoformat() if v.breach_day else None,
                                   "passed_day": v.passed_day.isoformat() if v.passed_day else None}
                               for k, v in sims.items()},
                "note": "Liczone na saldzie po zamknięciu transakcji; firmy liczą też equity z otwartymi pozycjami."}

    @app.get("/api/events")
    def events():
        now = datetime.now(timezone.utc)
        return [event_to_dict(e, now) for e in sample_events(now)]

    @app.get("/api/bias")
    def bias():
        now = datetime.now(timezone.utc)
        evts = sample_events(now)
        out = {}
        for asset in ("XAU", "XAG"):
            b = aggregate(evts, asset, now)
            out[asset] = {"score": b.score, "label": b.label, "strength": b.strength,
                          "events_used": b.events_used,
                          "drivers": [d.__dict__ for d in b.drivers]}
        # Trafność liczona z logu ocen (append-only); dopóki go nie ma — uczciwie: brak danych.
        out["track_record"] = {"available": False, "note": "Trafność pojawi się po okresie działania w trybie shadow."}
        out["sample"] = True
        return out

    return app
