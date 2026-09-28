"""
Configuration Module
Wszystkie ustawienia bota w jednym miejscu.

v4: + Market Scanner KOMBAJN (Pulse, Volatility, S/R, Sessions, Correlation)
v3: + GLM AI Analyst, + multi-asset (złoto, srebro, forex, indeksy globalne)
"""

import os
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class BotConfig:
    """Glowna konfiguracja bota sygnalowego."""

    # --- Discord ------------------------------------------------------------
    discord_webhook_url: str = ""  # Also reads from DISCORD_WEBHOOK env var
    discord_bot_name: str = "📊 Multi-Asset Signal Bot"
    discord_avatar_url: str = "https://cdn-icons-png.flaticon.com/512/6001/6001527.png"
    discord_role_id: Optional[str] = None        # ID roli do @mention
    mention_on_long: bool = True
    mention_on_short: bool = True
    quiet_hours: Optional[dict] = None           # {"start": 23, "end": 7} UTC

    # --- Exchange -----------------------------------------------------------
    exchange: str = "binance"
    rate_limit_ms: int = 500                      # ms miedzy requestami
    cache_ttl: int = 30                           # sekundy cache'u

    # --- Watchlist (Crypto) ---
    symbols: List[str] = field(default_factory=lambda: [
        "BTC/USDT",
        "ETH/USDT",
        "SOL/USDT",
        "BNB/USDT",
        "XRP/USDT",
        "DOGE/USDT",
        "ADA/USDT",
        "AVAX/USDT",
        "DOT/USDT",
        "LINK/USDT",
    ])

    timeframes: List[str] = field(default_factory=lambda: [
        "5m",
        "15m",
        "1h",
    ])

    # --- Multi-Asset Symbols ------------------------------------------------
    # Surowce
    commodity_symbols: List[str] = field(default_factory=lambda: [
        "XAU/USD",      # Zloto
        "XAG/USD",      # Srebro
    ])

    # Forex
    forex_symbols: List[str] = field(default_factory=lambda: [
        "EUR/USD",
    ])

    # Indeksy globalne
    index_symbols: List[str] = field(default_factory=lambda: [
        "SP500",        # S&P 500 (via SPY ETF)
        "NASDAQ",       # NASDAQ 100 (via QQQ ETF)
        "DAX",          # DAX (Niemcy)
        "WIG",          # WIG (GPW Polska)
    ])

    # --- Stochastic Settings ------------------------------------------------
    stoch_k_length: int = 7
    stoch_k_smooth: int = 3
    stoch_d_smooth: int = 2
    oversold_threshold: float = 20.0
    overbought_threshold: float = 80.0

    # --- Signal Filters -----------------------------------------------------
    require_crossover: bool = True               # K musi przeciac D
    rsi_filter: bool = False                     # Dodatkowy filtr RSI
    rsi_oversold: float = 35.0
    rsi_overbought: float = 65.0
    volume_filter: bool = False                  # Filtr wolumenu
    volume_mult: float = 1.5

    # --- Trend Filter -------------------------------------------------------
    trend_filter_mode: str = "block"               # "alert", "block", "off" — block = nie wysylaj sygnalow pod trend
    allow_stoch_only: bool = False                 # Najslabszy tier STOCH-ONLY (sama strefa K, bez NWO) — duzo szumu

    # --- Anti-Repaint -------------------------------------------------------
    use_closed_bar: bool = True                  # True = sygnaly na ZAMKNIETYM barze (anti-repaint); False = live (moze repaintowac)

    # --- Scanning -----------------------------------------------------------
    scan_interval: int = 60                      # Sekundy miedzy skanami
    candles_per_fetch: int = 500                 # Ile swiec pobierac (strategia NWO wymaga min. 120, EMA200 — 200)
    cooldown_per_signal: int = 300                # Sekundy cooldown dla tego samego sygnalu (5 min)

    # --- AI News Sentiment --------------------------------------------------
    use_sentiment: bool = False                  # Wlacz AI news sentiment filter
    cryptopanic_api_key: str = ""                # Also reads from CRYPTOPANIC_KEY env var
    finnhub_api_key: str = ""                    # Also reads from FINNHUB_KEY env var
    newsapi_key: str = ""                        # Also reads from NEWSAPI_KEY env var
    sentiment_refresh_interval: int = 300        # Sekundy miedzy refresh sentimentu
    sentiment_block_threshold: float = 0.5       # |score| > tego -> blokuj sygnal

    # --- Market Data Source -------------------------------------------------
    market_source: str = "both"                     # "crypto", "stocks", "both" — domyślnie both (crypto+tradfi)
    # crypto = tylko Binance (ccxt)
    # stocks = tylko YFinance
    # both = auto-wybierz zrodlo (crypto->Binance, reszta->YFinance)

    # --- Stock/ETF Symbols (YFinance) - DEPRECATED, use index_symbols -------
    stock_symbols: List[str] = field(default_factory=lambda: [
        "SP500",
        "US100",
    ])

    # --- GLM AI Analyst ----------------------------------------------------
    use_glm_analyst: bool = False                # Wlacz GLM AI Analyst
    glm_api_key: str = ""                        # Also reads from GLM_API_KEY env var
    glm_model: str = "glm-4-flash-250414"       # glm-4-flash-250414 (fast/cheap), glm-4, glm-4-plus
    glm_language: str = "pl"                     # pl, en

    # GLM features (can toggle individual features)
    glm_signal_scorer: bool = True               # Score each signal 1-10
    glm_daily_briefing: bool = True              # Market briefing co 6h
    glm_regime_detector: bool = True             # Classify market regime per pair
    glm_multi_tf_confluence: bool = True         # Multi-TF confluence analysis
    glm_eod_summary: bool = True                 # End-of-day summary

    # --- Market Scanner KOMBAJN --------------------------------------------
    use_market_scanner: bool = True              # Wlacz Market Scanner KOMBAJN
    scanner_pulse: bool = True                   # Market Pulse co 1h
    scanner_volatility: bool = True              # Volatility Scanner
    scanner_sr: bool = True                      # Support/Resistance Monitor
    scanner_sessions: bool = True                # Session Reporter (Azja/Europa/US)
    scanner_correlation: bool = True             # Correlation Alert
    scanner_pulse_interval: int = 3600           # Co ile sekund Market Pulse (default: 1h)
    scanner_volatility_threshold: float = 2.0    # current/avg vol > tego = alert
    scanner_sr_lookback: int = 50                # Ile swiec do detekcji S/R
    scanner_sr_proximity_pct: float = 1.0        # % odleglosci do S/R alert
    scanner_corr_threshold: float = 0.3          # Min rozstep korelacji do alertu

    # --- Breaking News Monitor -----------------------------------------------
    use_news_monitor: bool = True                # Wlacz Breaking News Monitor (RSS)
    news_check_interval: int = 900               # Co ile sekund sprawdzac RSS (15 min)

    # --- Fear & Greed Index --------------------------------------------------
    use_fear_greed: bool = True                  # Wlacz Fear & Greed Monitor
    fear_greed_interval: int = 21600             # Co ile sekund sprawdzac (6h)
    fear_greed_alert_threshold: int = 15         # Zmiana > N punktow = alert

    # --- Funding Rate Monitor ------------------------------------------------
    use_funding_rate: bool = True                # Wlacz Funding Rate Monitor (crypto)
    funding_elevated_threshold: float = 0.0005   # > 0.05% = elevated
    funding_extreme_threshold: float = 0.001     # > 0.1% = extreme

    # --- Whale & Liquidation Alerts ------------------------------------------
    use_whale_alerts: bool = True                # Wlacz Whale/Liquidation Monitor

    # --- Economic Calendar ---------------------------------------------------
    use_econ_calendar: bool = True               # Wlacz Economic Calendar
    econ_alert_hours_before: int = 24            # Alert N godzin przed eventem

    # --- REST API ------------------------------------------------------------
    use_api: bool = False                        # Wlacz FastAPI REST API
    api_host: str = "0.0.0.0"                   # API bind address
    api_port: int = 8080                         # API port

    # --- Position Tracking --------------------------------------------------
    use_position_tracking: bool = True           # Sledzenie pozycji (INFO, nie egzekucja!)
    auto_open_positions: bool = False             # False = ALERT ONLY, True = auto-otwieraj
    position_db_path: str = ""                   # Auto-detected
    apply_slippage: bool = False                 # False = ceny bez korekty; True = realistyczne PnL dla backtestu/auto-trade
    slippage_pct: float = 0.001                  # 0.1% gdy apply_slippage=True
    default_position_size_usd: float = 100       # Domyslny rozmiar pozycji (USD)
    max_open_positions: int = 10                  # Max otwartych pozycji
    position_timeout_hours: float = 72           # Max czas otwartej pozycji (godziny)

    # --- Status Updates -----------------------------------------------------
    status_interval: int = 3600                  # Co ile sekund wysylac status na Discord
    log_level: str = "INFO"                      # DEBUG, INFO, WARNING, ERROR

    def __post_init__(self):
        """Resolve env vars for secrets and auto-detect DB path."""
        if not self.discord_webhook_url:
            self.discord_webhook_url = os.getenv("DISCORD_WEBHOOK", "")
        if not self.cryptopanic_api_key:
            self.cryptopanic_api_key = os.getenv("CRYPTOPANIC_KEY", "")
        if not self.finnhub_api_key:
            self.finnhub_api_key = os.getenv("FINNHUB_KEY", "")
        if not self.newsapi_key:
            self.newsapi_key = os.getenv("NEWSAPI_KEY", "")
        if not self.glm_api_key:
            self.glm_api_key = os.getenv("GLM_API_KEY", "")
        if not self.position_db_path:
            if os.path.isdir("/app/data"):
                self.position_db_path = "/app/data/positions.db"
            else:
                self.position_db_path = "positions.db"

    def get_all_symbols(self) -> List[str]:
        """Get combined list of all monitored symbols."""
        all_symbols = list(self.symbols)
        
        if self.market_source in ("stocks", "both"):
            for sym in self.commodity_symbols:
                if sym not in all_symbols:
                    all_symbols.append(sym)
            for sym in self.forex_symbols:
                if sym not in all_symbols:
                    all_symbols.append(sym)
            for sym in self.index_symbols:
                if sym not in all_symbols:
                    all_symbols.append(sym)
            # Note: stock_symbols is deprecated, use index_symbols instead
            # But keep for backward compatibility
            for sym in self.stock_symbols:
                if sym not in all_symbols:
                    all_symbols.append(sym)
        
        return all_symbols

    def validate(self) -> List[str]:
        """Waliduj konfiguracje. Zwraca liste bledow."""
        errors = []
        if not self.discord_webhook_url:
            errors.append("discord_webhook_url jest wymagany!")
        elif not self.discord_webhook_url.startswith("https://discord.com/api/webhooks/") and \
           not self.discord_webhook_url.startswith("https://discordapp.com/api/webhooks/"):
            errors.append(f"discord_webhook_url nie wyglada na prawidlowy webhook Discord")
        if self.scan_interval < 10:
            errors.append("scan_interval musi byc >= 10 sekund")
        if not self.symbols:
            errors.append("symbols nie moze byc puste")
        if not self.timeframes:
            errors.append("timeframes nie moze byc puste")
        if self.trend_filter_mode not in ("alert", "block", "off"):
            errors.append(f"trend_filter_mode musi byc 'alert', 'block' lub 'off', jest '{self.trend_filter_mode}'")
        if self.market_source not in ("crypto", "stocks", "both"):
            errors.append(f"market_source musi byc 'crypto', 'stocks' lub 'both', jest '{self.market_source}'")
        if self.use_glm_analyst and not self.glm_api_key:
            errors.append("use_glm_analyst=True ale glm_api_key jest puste! Ustaw GLM_API_KEY")
        return errors

    @staticmethod
    def _mask_secret(secret: str, show_chars: int = 8) -> str:
        """Maskuje sekret do logów — pokazuje tylko pierwsze N znaków."""
        if not secret:
            return "(not set)"
        if len(secret) <= show_chars:
            return "***"
        return f"{secret[:show_chars]}..."

    def summary(self) -> str:
        """Zwraca podsumowanie konfiguracji z zamaskowanymi sekretami."""
        trend_icon = {"alert": "!!", "block": "X", "off": "-"}.get(self.trend_filter_mode, "?")
        all_syms = self.get_all_symbols()
        crypto_count = len(self.symbols)
        tradfi_count = len(all_syms) - crypto_count
        
        return (
            f"Exchange: {self.exchange}\n"
            f"Crypto: {', '.join(self.symbols)}\n"
            f"Surowce: {', '.join(self.commodity_symbols)}\n"
            f"Forex: {', '.join(self.forex_symbols)}\n"
            f"Indeksy: {', '.join(self.index_symbols)}\n"
            f"Total symbols: {len(all_syms)} (crypto={crypto_count}, tradfi={tradfi_count})\n"
            f"Timeframes: {', '.join(self.timeframes)}\n"
            f"Stochastic: ({self.stoch_k_length}, {self.stoch_k_smooth}, {self.stoch_d_smooth})\n"
            f"Oversold: < {self.oversold_threshold} | Overbought: > {self.overbought_threshold}\n"
            f"Crossover required: {self.require_crossover}\n"
            f"Trend filter: {trend_icon} {self.trend_filter_mode}\n"
            f"Scan interval: {self.scan_interval}s\n"
            f"Market: {self.market_source}\n"
            f"AI Sentiment: {'YES' if self.use_sentiment else 'NO'}\n"
            f"GLM AI Analyst: {'YES' if self.use_glm_analyst else 'NO'} (model={self.glm_model})\n"
            f"GLM API Key: {self._mask_secret(self.glm_api_key)}\n"
            f"CryptoPanic Key: {self._mask_secret(self.cryptopanic_api_key)}\n"
            f"Finnhub Key: {self._mask_secret(self.finnhub_api_key)}\n"
            f"  - Signal Scorer: {'YES' if self.glm_signal_scorer else 'NO'}\n"
            f"  - Daily Briefing: {'YES' if self.glm_daily_briefing else 'NO'}\n"
            f"  - Regime Detector: {'YES' if self.glm_regime_detector else 'NO'}\n"
            f"  - Multi-TF Confluence: {'YES' if self.glm_multi_tf_confluence else 'NO'}\n"
            f"  - EOD Summary: {'YES' if self.glm_eod_summary else 'NO'}\n"
            f"Market Scanner: {'YES' if self.use_market_scanner else 'NO'}\n"
            f"  - Pulse: {'YES' if self.scanner_pulse else 'NO'} ({self.scanner_pulse_interval}s)\n"
            f"  - Volatility: {'YES' if self.scanner_volatility else 'NO'} (>{self.scanner_volatility_threshold}x)\n"
            f"  - S/R Monitor: {'YES' if self.scanner_sr else 'NO'}\n"
            f"  - Sessions: {'YES' if self.scanner_sessions else 'NO'}\n"
            f"  - Correlation: {'YES' if self.scanner_correlation else 'NO'}\n"
            f"Position tracking: {'YES' if self.use_position_tracking else 'NO'}\n"
            f"Discord: {'YES' if self.discord_webhook_url else 'NO'}"
        )


# ==============================================================================
# PRESET CONFIGURATIONS
# ==============================================================================

def config_aggressive() -> BotConfig:
    """Agresywna konfiguracja - wiecej sygnalow, luzniejsze filtry."""
    return BotConfig(
        oversold_threshold=25.0,
        overbought_threshold=75.0,
        require_crossover=False,
        scan_interval=30,
        timeframes=["5m", "15m"],
        cooldown_per_signal=180,
    )


def config_conservative() -> BotConfig:
    """Konserwatywna konfiguracja - mniej sygnalow, mocniejsze filtry."""
    return BotConfig(
        oversold_threshold=15.0,
        overbought_threshold=85.0,
        require_crossover=True,
        rsi_filter=True,
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        volume_filter=True,
        scan_interval=120,
        timeframes=["1h", "4h"],
        cooldown_per_signal=600,
    )


def config_scalping() -> BotConfig:
    """Konfiguracja pod skalping - niskie TF, szybkie skanowanie."""
    return BotConfig(
        symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        timeframes=["1m", "5m"],
        scan_interval=15,
        candles_per_fetch=300,                   # min. 120 dla strategii NWO
        require_crossover=True,
        cooldown_per_signal=60,
    )


def config_multi_asset() -> BotConfig:
    """Konfiguracja multi-asset - crypto + surowce + forex + indeksy + KOMBAJN."""
    return BotConfig(
        market_source="both",
        timeframes=["15m", "1h", "4h"],
        scan_interval=90,
        use_glm_analyst=True,
        use_market_scanner=True,
    )
