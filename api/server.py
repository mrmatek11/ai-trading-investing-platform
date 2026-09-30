"""
FastAPI REST API — KOMBAJN DO RYNKU
═════════════════════════════════════════════════════════════════════════

REST API do integracji bota z innymi backendami Python.

Endpoints:
  GET  /api/health          — Health check
  GET  /api/stats           — Bot statistics
  GET  /api/signals         — Recent signals (z cooldown history)
  GET  /api/positions       — Open positions
  GET  /api/positions/closed — Closed positions
  GET  /api/market/pulse    — Market Pulse data
  GET  /api/market/regime   — Market regime per symbol
  GET  /api/market/fng      — Fear & Greed Index
  GET  /api/market/funding  — Funding rates
  GET  /api/scanner/alerts  — Active scanner alerts
  GET  /api/config          — Current config
  POST /api/config/update   — Update config at runtime

Uzycie:
  python bot.py --api --webhook URL
  # API dostepne na http://localhost:8080/api/
"""

import hmac
import logging
import os
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# FastAPI jest opcjonalne — bot dziala bez niego
try:
    from fastapi import Depends, FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    FastAPI = None


def create_api_app(bot_instance=None) -> Any:
    """
    Stworz FastAPI app z referencja do bota.

    Args:
        bot_instance: StochSignalBot instance (dostep do stats, config, etc.)

    Returns:
        FastAPI app lub None jesli FastAPI nie zainstalowane
    """
    if not FASTAPI_AVAILABLE:
        logger.warning("FastAPI/uvicorn nie zainstalowane! pip install fastapi uvicorn")
        return None

    # Auth: jesli ustawiony API_KEY, kazdy endpoint poza /api/health wymaga
    # naglowka X-API-Key. Bez API_KEY endpointy do odczytu dzialaja (lokalnie),
    # ale zapis konfiguracji jest wylaczony.
    api_key = os.getenv("API_KEY", "")
    if not api_key:
        logger.warning("API_KEY nie ustawiony — API bez autoryzacji, /api/config/update wylaczony")

    async def require_api_key(request: Request):
        if not api_key or request.url.path == "/api/health":
            return
        if not hmac.compare_digest(request.headers.get("x-api-key", ""), api_key):
            raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

    app = FastAPI(
        title="Crypto Signal Bot API",
        description="REST API for KOMBAJN DO RYNKU — Multi-Asset Signal Bot",
        version="5.0.0",
        dependencies=[Depends(require_api_key)],
    )

    # CORS — API_CORS_ORIGINS="https://a.com,https://b.com" (domyslnie: *).
    # Bez credentials: autoryzacja idzie naglowkiem, nie cookies.
    cors_origins = [o.strip() for o in os.getenv("API_CORS_ORIGINS", "*").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["X-API-Key", "Content-Type"],
    )

    # Referencja do bota
    _bot = bot_instance

    @app.get("/api/health")
    async def health_check():
        """Health check — czy bot zyje?"""
        return {
            "status": "alive" if (_bot and _bot._running) else "stopped",
            "version": "5.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/api/stats")
    async def get_stats():
        """Bot statistics — skany, sygnaly, bledy, etc."""
        if not _bot:
            raise HTTPException(status_code=503, detail="Bot not initialized")
        return _bot.stats

    @app.get("/api/signals")
    async def get_signals():
        """Recent signal history (z ostatnich 24h cooldown keys)."""
        if not _bot:
            raise HTTPException(status_code=503, detail="Bot not initialized")

        now = datetime.now(timezone.utc)
        cooldown_data = {}
        for key, entry in _bot._cooldowns.items():
            from datetime import timedelta
            ts = entry["ts"] if isinstance(entry, dict) else entry
            strength = entry.get("strength") if isinstance(entry, dict) else None
            source = entry.get("source") if isinstance(entry, dict) else None
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            cooldown_data[key] = {
                "last_signal": dt.isoformat(),
                "cooldown_until": (dt + timedelta(seconds=_bot.config.cooldown_per_signal)).isoformat(),
                "is_on_cooldown": (datetime.now(timezone.utc).timestamp() - ts) < _bot.config.cooldown_per_signal,
                "strength": strength,
                "source": source,
            }

        return {
            "total_signals_sent": _bot._signals_sent,
            "active_cooldowns": cooldown_data,
            "scan_count": _bot._scan_count,
        }

    @app.get("/api/positions")
    async def get_positions():
        """Otwarte pozycje."""
        if not _bot or not _bot.position_tracker:
            raise HTTPException(status_code=503, detail="Position tracker not available")

        positions = _bot.position_tracker.get_open_positions()
        return {
            "count": len(positions),
            "positions": [
                {
                    "id": p.id,
                    "symbol": p.symbol,
                    "direction": p.direction,
                    "entry_price": p.entry_price,
                    "sl": p.sl,
                    "tp": p.tp,
                    "opened_at": p.opened_at,
                    "timeframe": p.timeframe,
                    "strategy": p.strategy,
                }
                for p in positions
            ],
        }

    @app.get("/api/positions/closed")
    async def get_closed_positions(limit: int = 20):
        """Zamkniete pozycje."""
        if not _bot or not _bot.position_tracker:
            raise HTTPException(status_code=503, detail="Position tracker not available")

        positions = _bot.position_tracker.get_closed_positions(limit=limit)
        return {
            "count": len(positions),
            "positions": [
                {
                    "id": p.id,
                    "symbol": p.symbol,
                    "direction": p.direction,
                    "entry_price": p.entry_price,
                    "close_price": p.close_price,
                    "pnl": p.pnl,
                    "close_reason": p.close_reason,
                }
                for p in positions
            ],
        }

    @app.get("/api/market/pulse")
    async def get_market_pulse():
        """Market Pulse — szybki snapshot rynku."""
        if not _bot or not _bot.market_scanner:
            raise HTTPException(status_code=503, detail="Market scanner not available")

        stats = _bot.market_scanner.stats
        return {
            "scanner_stats": stats,
            "scan_count": _bot._scan_count,
        }

    @app.get("/api/market/regime")
    async def get_regime():
        """Market regime per symbol (ostatnio wykryty)."""
        if not _bot:
            raise HTTPException(status_code=503, detail="Bot not available")

        regimes = {}
        if _bot.glm_analyst:
            regimes = _bot.glm_analyst.stats

        return {"regimes": regimes}

    @app.get("/api/market/fng")
    async def get_fear_greed():
        """Fear & Greed Index (ostatni odczyt)."""
        if not _bot or not hasattr(_bot, '_fear_greed_monitor') or not _bot._fear_greed_monitor:
            raise HTTPException(status_code=503, detail="Fear & Greed monitor not available")

        return _bot._fear_greed_monitor.stats

    @app.get("/api/market/funding")
    async def get_funding_rates():
        """Funding rates (ostatni odczyt)."""
        if not _bot or not hasattr(_bot, '_funding_monitor') or not _bot._funding_monitor:
            raise HTTPException(status_code=503, detail="Funding monitor not available")

        return _bot._funding_monitor.stats

    @app.get("/api/scanner/alerts")
    async def get_scanner_alerts():
        """Active scanner alerts / stats."""
        result = {}
        if _bot and _bot.market_scanner:
            result["market_scanner"] = _bot.market_scanner.stats
        if _bot and hasattr(_bot, '_whale_monitor') and _bot._whale_monitor:
            result["whale_alerts"] = _bot._whale_monitor.stats
        if _bot and hasattr(_bot, '_news_monitor') and _bot._news_monitor:
            result["news_monitor"] = _bot._news_monitor.stats
        return result

    @app.get("/api/config")
    async def get_config():
        """Aktualna konfiguracja bota."""
        if not _bot:
            raise HTTPException(status_code=503, detail="Bot not initialized")
        return _bot.config.summary()

    @app.post("/api/config/update")
    async def update_config(updates: Dict):
        """Update config at runtime (limited fields)."""
        if not api_key:
            raise HTTPException(status_code=403, detail="Set API_KEY to enable config updates")
        if not _bot:
            raise HTTPException(status_code=503, detail="Bot not initialized")

        allowed_fields = {
            "scan_interval", "cooldown_per_signal",
            "oversold_threshold", "overbought_threshold",
        }

        updated = {}
        for key, value in updates.items():
            if key in allowed_fields and hasattr(_bot.config, key):
                setattr(_bot.config, key, value)
                updated[key] = value

        if not updated:
            raise HTTPException(status_code=400, detail=f"No valid fields. Allowed: {allowed_fields}")

        logger.info(f"Config updated: {updated}")
        return {"status": "ok", "updated": updated}

    return app


def run_api_server(bot_instance=None, host: str = "0.0.0.0", port: int = 8080):
    """Uruchom API server (blocking)."""
    if not FASTAPI_AVAILABLE:
        logger.error("FastAPI/uvicorn nie zainstalowane! pip install fastapi uvicorn")
        return

    app = create_api_app(bot_instance)
    if app:
        logger.info(f"API server: http://{host}:{port}/api/")
        uvicorn.run(app, host=host, port=port, log_level="warning")
