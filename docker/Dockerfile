FROM python:3.11-slim

LABEL maintainer="Crypto Signal Bot v5"
LABEL description="KOMBAJN DO RYNKU — NWO + Stoch + CVD + GLM AI + Market Scanner + Breaking News"

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Workdir
WORKDIR /app

# Copy project (structured)
COPY bot.py .
COPY core/ core/
COPY strategy/ strategy/
COPY fetchers/ fetchers/
COPY analysis/ analysis/
COPY notifiers/ notifiers/
COPY tracking/ tracking/
COPY api/ api/
COPY docker/entrypoint.sh ./
RUN chmod +x entrypoint.sh

# Create data dir for SQLite
RUN mkdir -p /app/data

# Environment defaults (override with .env or docker-compose)
ENV DISCORD_WEBHOOK="" \
    SYMBOLS="BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT,ADA/USDT,DOGE/USDT,AVAX/USDT,DOT/USDT,LINK/USDT" \
    TIMEFRAMES="5m,15m,1h" \
    SCAN_INTERVAL=60 \
    TREND_FILTER=alert \
    MARKET=crypto \
    GLM_API_KEY="" \
    GLM_MODEL=glm-4-flash \
    SCANNER_PULSE_INTERVAL=3600 \
    USE_NEWS_MONITOR=true \
    USE_FEAR_GREED=true \
    USE_FUNDING_RATE=true \
    USE_WHALE_ALERTS=true \
    USE_ECON_CALENDAR=true \
    USE_API=false \
    API_PORT=8080 \
    LOG_LEVEL=INFO \
    TZ=Europe/Warsaw

# Healthcheck
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
    CMD pgrep -f "bot.py" || exit 1

# Run
ENTRYPOINT ["./entrypoint.sh"]
