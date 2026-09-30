#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  Entrypoint for Docker — przekazuje env vars jako CLI args
#  v3: + USE_CLOSED_BAR, APPLY_SLIPPAGE, SLIPPAGE_PCT, AUTO_TRADE
# ═══════════════════════════════════════════════════════════

set -e

echo "🚀 Crypto Signal Bot — Starting..."
echo "   Mode: 📡 ALERT ONLY (no execution)"
echo "   Symbols: ${SYMBOLS:-BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT,DOGE/USDT,ADA/USDT,AVAX/USDT,DOT/USDT,LINK/USDT}"
echo "   Timeframes: ${TIMEFRAMES:-5m,15m,1h}"
echo "   Trend filter: ${TREND_FILTER:-block}"
echo "   Market: ${MARKET:-both}"
echo "   Closed-bar: ${USE_CLOSED_BAR:-true}"
echo "   AI Sentiment: ${SENTIMENT:-false}"
echo "   GLM AI Analyst: $([ -n "$GLM_API_KEY" ] && echo ✅ || echo ❌)"
echo "═════════════════════════════════════════════════════════"

# Build command using bash array (proper quoting)
ARGS=()

# Webhook is read from env var by config.py — only pass --test if not set
if [ -z "$DISCORD_WEBHOOK" ]; then
    ARGS+=("--test")
    echo "⚠️ No DISCORD_WEBHOOK set — running in TEST mode"
fi

# Symbols
if [ -n "$SYMBOLS" ]; then
    ARGS+=("--symbols" "$SYMBOLS")
fi

# Timeframes
if [ -n "$TIMEFRAMES" ]; then
    ARGS+=("--timeframes" "$TIMEFRAMES")
fi

# Scan interval
if [ -n "$SCAN_INTERVAL" ]; then
    ARGS+=("--interval" "$SCAN_INTERVAL")
fi

# Trend filter
if [ -n "$TREND_FILTER" ]; then
    ARGS+=("--trend-filter" "$TREND_FILTER")
fi

if [ "$STOCH_ONLY" = "true" ]; then
    ARGS+=("--stoch-only")
fi

# Market source
if [ -n "$MARKET" ]; then
    ARGS+=("--market" "$MARKET")
fi

# Sentiment
if [ "$SENTIMENT" = "true" ]; then
    ARGS+=("--sentiment")
fi

# News API keys (jeśli explicit override potrzebne — normalnie czyta z env)
if [ -n "$CRYPTOPANIC_KEY" ]; then
    ARGS+=("--cryptopanic-key" "$CRYPTOPANIC_KEY")
fi
if [ -n "$FINNHUB_KEY" ]; then
    ARGS+=("--finnhub-key" "$FINNHUB_KEY")
fi

# GLM AI Analyst
if [ -n "$GLM_API_KEY" ]; then
    ARGS+=("--glm-key" "$GLM_API_KEY")
fi
if [ -n "$GLM_MODEL" ]; then
    ARGS+=("--glm-model" "$GLM_MODEL")
fi
if [ -n "$GLM_LANG" ]; then
    ARGS+=("--glm-lang" "$GLM_LANG")
fi
if [ "$GLM_DISABLED" = "true" ]; then
    ARGS+=("--no-glm")
fi

# Position tracking & auto-trade
if [ -n "$POSITION_SIZE" ]; then
    ARGS+=("--position-size" "$POSITION_SIZE")
fi
if [ "$AUTO_TRADE" = "true" ]; then
    ARGS+=("--auto-trade")
fi

# ── v5 anti-repaint / slippage flags ────────────────────────
# USE_CLOSED_BAR=false → bot.py używa --live-bar
if [ "$USE_CLOSED_BAR" = "false" ]; then
    ARGS+=("--live-bar")
fi
if [ "$APPLY_SLIPPAGE" = "true" ]; then
    ARGS+=("--apply-slippage")
fi
if [ -n "$SLIPPAGE_PCT" ]; then
    ARGS+=("--slippage-pct" "$SLIPPAGE_PCT")
fi
# ────────────────────────────────────────────────────────────

# Market Scanner
if [ "$SCANNER_DISABLED" = "true" ]; then
    ARGS+=("--no-scanner")
fi
if [ -n "$SCANNER_PULSE_INTERVAL" ]; then
    ARGS+=("--scanner-pulse" "$SCANNER_PULSE_INTERVAL")
fi
if [ "$SCANNER_PULSE_DISABLED" = "true" ]; then
    ARGS+=("--no-scanner-pulse")
fi
if [ "$SCANNER_VOL_DISABLED" = "true" ]; then
    ARGS+=("--no-scanner-vol")
fi
if [ "$SCANNER_SR_DISABLED" = "true" ]; then
    ARGS+=("--no-scanner-sr")
fi
if [ "$SCANNER_SESSIONS_DISABLED" = "true" ]; then
    ARGS+=("--no-scanner-sessions")
fi
if [ "$SCANNER_CORR_DISABLED" = "true" ]; then
    ARGS+=("--no-scanner-corr")
fi

# KOMBAJN features
if [ "$USE_NEWS_MONITOR" = "false" ]; then
    ARGS+=("--no-news")
fi
if [ "$USE_FEAR_GREED" = "false" ]; then
    ARGS+=("--no-fng")
fi
if [ "$USE_FUNDING_RATE" = "false" ]; then
    ARGS+=("--no-funding")
fi
if [ "$USE_WHALE_ALERTS" = "false" ]; then
    ARGS+=("--no-whale")
fi
if [ "$USE_ECON_CALENDAR" = "false" ]; then
    ARGS+=("--no-econ")
fi

# REST API
if [ "$USE_API" = "true" ]; then
    ARGS+=("--api")
fi
if [ -n "$API_PORT" ]; then
    ARGS+=("--api-port" "$API_PORT")
fi

# Log level
if [ -n "$LOG_LEVEL" ]; then
    ARGS+=("--log" "$LOG_LEVEL")
fi

exec python3 bot.py "${ARGS[@]}"
