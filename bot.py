#!/usr/bin/env python3
"""
Crypto Signal Bot v5 — KOMBAJN DO RYNKU
Multi-Asset Signal Bot: NWO + Stoch(7,3,2) + CVD + GLM AI + Market Scanner

Neural Weight Oscillator (Zeiierman) + Stochastic + CVD
Monitoruje krypto, surowce, forex i indeksy na zywo
Wysyla alerty na Discord z AI news sentiment i GLM AI Analyst

v5: Reorganizacja do pakietow + Breaking News + Fear&Greed + Funding Rate
    + Whale/Liquidation Alerts + Economic Calendar + REST API
v4: + Market Scanner KOMBAJN (Pulse, Vol, S/R, Sessions, Correlation)
v3: + GLM AI Analyst (Scorer, Briefing, Regime, Confluence, EOD)
    + Multi-asset (zloto, srebro, EUR/USD, WIG, S&P500, DAX, Nikkei)

Uzycie:
  python bot.py --test --scan
  python bot.py --webhook URL --strategy nwo_stoch_cvd
  python bot.py --config aggressive --sentiment --glm-key KEY
  python bot.py --market both --symbols BTC/USDT,XAU/USD,SP500
  python bot.py --api  # Start with REST API on port 8080
"""

import sys
import time
import signal
import argparse
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from core.config import BotConfig, config_aggressive, config_conservative, config_scalping, config_multi_asset
from strategy.signal_detector import SignalDetector, Signal
from fetchers.binance import DataFetcher
from notifiers.discord import DiscordNotifier
from strategy.custom_strategy import STRATEGY_REGISTRY, get_sentiment_engine

# --- Logging ---

def setup_logging(level: str = "INFO"):
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    datefmt = "%H:%M:%S"
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format=fmt, datefmt=datefmt)
    return logging.getLogger("MultiAssetBot")

logger = setup_logging()


# ==============================================================================
# MAIN BOT CLASS
# ==============================================================================

class StochSignalBot:
    """
    Glowna klasa bota sygnalowego.
    Petla: pobierz dane -> wykryj sygnaly -> GLM AI ocen -> Market Scanner -> Discord -> czekaj.
    KOMBAJN: caly czas analizuje rynek, nie tylko czeka na sygnaly.
    """

    def __init__(self, config: BotConfig, test_mode: bool = False):
        self.config = config
        self.test_mode = test_mode
        self._running = False
        self._scan_count = 0
        self._signals_sent = 0
        self._errors = 0
        self._cooldowns: Dict[str, float] = {}
        self._last_status_time = 0

        # Inicjalizuj detektor
        self.detector = SignalDetector(
            stoch_k_length=config.stoch_k_length,
            stoch_k_smooth=config.stoch_k_smooth,
            stoch_d_smooth=config.stoch_d_smooth,
            oversold_threshold=config.oversold_threshold,
            overbought_threshold=config.overbought_threshold,
            require_crossover=config.require_crossover,
            rsi_filter=config.rsi_filter,
            rsi_oversold=config.rsi_oversold,
            rsi_overbought=config.rsi_overbought,
            volume_filter=config.volume_filter,
            volume_mult=config.volume_mult,
            use_closed_bar=config.use_closed_bar,
        )
        # Propagate closed-bar mode do custom_strategy
        try:
            from strategy.custom_strategy import set_closed_bar_mode, set_stoch_only_enabled
            set_closed_bar_mode(config.use_closed_bar)
            set_stoch_only_enabled(config.allow_stoch_only)
        except ImportError:
            pass

        # --- Data fetcher (crypto / stocks / unified) ---
        self._yf_fetcher = None
        self._unified_fetcher = None

        if config.market_source in ("stocks", "both"):
            try:
                from fetchers.yfinance import YFinanceDataFetcher, UnifiedDataFetcher
                if config.market_source == "both":
                    self._unified_fetcher = UnifiedDataFetcher()
                    self.fetcher = self._unified_fetcher
                    logger.info("Unified data fetcher: Crypto (Binance) + TradFi (YFinance)")
                else:
                    self._yf_fetcher = YFinanceDataFetcher()
                    self.fetcher = self._yf_fetcher
                    logger.info("Stock data fetcher: YFinance (multi-asset)")
            except ImportError:
                logger.warning("yfinance nie zainstalowany! Fallback do Binance. pip install yfinance")
                self.fetcher = DataFetcher(
                    exchange_id=config.exchange,
                    rate_limit_ms=config.rate_limit_ms,
                    cache_ttl_seconds=config.cache_ttl,
                    candles_per_fetch=config.candles_per_fetch,
                )
        else:
            self.fetcher = DataFetcher(
                exchange_id=config.exchange,
                rate_limit_ms=config.rate_limit_ms,
                cache_ttl_seconds=config.cache_ttl,
                candles_per_fetch=config.candles_per_fetch,
            )

        # --- Discord notifier ---
        if not test_mode and config.discord_webhook_url:
            self.notifier = DiscordNotifier(
                webhook_url=config.discord_webhook_url,
                bot_name=config.discord_bot_name,
                avatar_url=config.discord_avatar_url,
                mention_role_id=config.discord_role_id,
                mention_on_long=config.mention_on_long,
                mention_on_short=config.mention_on_short,
                quiet_hours=config.quiet_hours,
            )
        else:
            self.notifier = None

        # --- Position tracker ---
        self.position_tracker = None
        if config.use_position_tracking:
            try:
                from tracking.position_tracker import PositionTracker
                self.position_tracker = PositionTracker(
                    db_path=config.position_db_path,
                    timeout_hours=config.position_timeout_hours,
                    default_size_usd=config.default_position_size_usd,
                    max_open=config.max_open_positions,
                    apply_slippage=config.apply_slippage,
                    slippage_pct=config.slippage_pct,
                )
                slip_state = f"slip={config.slippage_pct*100:.2f}%" if config.apply_slippage else "slip=OFF"
                logger.info(f"Position tracker: ENABLED (db={config.position_db_path}, {slip_state})")
            except Exception as e:
                logger.warning(f"Position tracker init failed: {e}")

        # --- Sentiment engine ---
        self.sentiment_engine = None
        if config.use_sentiment:
            try:
                from analysis.news_sentiment import SentimentEngine
                self.sentiment_engine = SentimentEngine(
                    cryptopanic_key=config.cryptopanic_api_key,
                    finnhub_key=config.finnhub_api_key,
                    newsapi_key=config.newsapi_key,
                    refresh_interval=config.sentiment_refresh_interval,
                )
                logger.info("AI Sentiment filter: ENABLED")
                # Propagate sentiment flag to custom_strategy module
                try:
                    from strategy.custom_strategy import set_sentiment_enabled
                    set_sentiment_enabled(True)
                    logger.info("Sentiment filter propagated to strategy")
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"Sentiment engine init failed: {e}")

        # --- GLM AI Analyst ---
        self.glm_analyst = None
        if config.use_glm_analyst and config.glm_api_key:
            try:
                from analysis.glm_analyst import GLMAnalyst
                self.glm_analyst = GLMAnalyst(
                    api_key=config.glm_api_key,
                    model=config.glm_model,
                    enabled=True,
                    language=config.glm_language,
                    signal_scorer=config.glm_signal_scorer,
                    daily_briefing=config.glm_daily_briefing,
                    regime_detector=config.glm_regime_detector,
                    multi_tf_confluence=config.glm_multi_tf_confluence,
                    eod_summary=config.glm_eod_summary,
                )
                logger.info(f"GLM AI Analyst: ENABLED (model={config.glm_model})")
            except Exception as e:
                logger.warning(f"GLM AI Analyst init failed: {e}")

        # --- Market Scanner (KOMBAJN) ---
        self.market_scanner = None
        if config.use_market_scanner:
            try:
                from analysis.market_scanner import MarketScanner
                self.market_scanner = MarketScanner(
                    pulse_interval=config.scanner_pulse_interval,
                    volatility_threshold=config.scanner_volatility_threshold,
                    sr_lookback=config.scanner_sr_lookback,
                    sr_proximity_pct=config.scanner_sr_proximity_pct,
                    corr_divergence_threshold=config.scanner_corr_threshold,
                    enabled_pulse=config.scanner_pulse,
                    enabled_volatility=config.scanner_volatility,
                    enabled_sr=config.scanner_sr,
                    enabled_sessions=config.scanner_sessions,
                    enabled_correlation=config.scanner_correlation,
                )
                logger.info(f"Market Scanner KOMBAJN: ENABLED (pulse={config.scanner_pulse_interval}s)")
            except Exception as e:
                logger.warning(f"Market Scanner init failed: {e}")

        # --- Breaking News Monitor ---
        self._news_monitor = None
        if config.use_news_monitor:
            try:
                from analysis.news_monitor import NewsMonitor
                self._news_monitor = NewsMonitor(
                    check_interval=config.news_check_interval,
                )
                logger.info(f"Breaking News Monitor: ENABLED (interval={config.news_check_interval}s)")
            except Exception as e:
                logger.warning(f"News Monitor init failed: {e}")

        # --- Fear & Greed Index ---
        self._fear_greed_monitor = None
        if config.use_fear_greed:
            try:
                from analysis.fear_greed import FearGreedMonitor
                self._fear_greed_monitor = FearGreedMonitor(
                    check_interval=config.fear_greed_interval,
                    alert_threshold=config.fear_greed_alert_threshold,
                )
                logger.info("Fear & Greed Monitor: ENABLED")
            except Exception as e:
                logger.warning(f"Fear & Greed init failed: {e}")

        # --- Funding Rate Monitor ---
        self._funding_monitor = None
        if config.use_funding_rate:
            try:
                from analysis.funding_rate import FundingRateMonitor
                self._funding_monitor = FundingRateMonitor(
                    elevated_threshold=config.funding_elevated_threshold,
                    extreme_threshold=config.funding_extreme_threshold,
                )
                logger.info("Funding Rate Monitor: ENABLED")
            except Exception as e:
                logger.warning(f"Funding Rate init failed: {e}")

        # --- Whale & Liquidation Alerts ---
        self._whale_monitor = None
        if config.use_whale_alerts:
            try:
                from analysis.whale_alerts import WhaleLiquidationMonitor
                self._whale_monitor = WhaleLiquidationMonitor()
                logger.info("Whale/Liquidation Monitor: ENABLED")
            except Exception as e:
                logger.warning(f"Whale/Liquidation init failed: {e}")

        # --- Economic Calendar ---
        self._econ_calendar = None
        if config.use_econ_calendar:
            try:
                from analysis.economic_calendar import EconomicCalendar
                self._econ_calendar = EconomicCalendar(
                    alert_hours_before=config.econ_alert_hours_before,
                )
                logger.info("Economic Calendar: ENABLED")
            except Exception as e:
                logger.warning(f"Economic Calendar init failed: {e}")

        # --- Custom strategy ---
        self.custom_strategy_fn = None

        # --- Signal Performance Tracker + Self-Learning Filter ---
        self.signal_tracker = None
        self.self_learning_filter = None
        try:
            from tracking.signal_performance import SignalPerformanceTracker, SelfLearningFilter
            self.signal_tracker = SignalPerformanceTracker(
                db_path=config.position_db_path.replace("positions.db", "signal_performance.db")
            )
            self.self_learning_filter = SelfLearningFilter(self.signal_tracker)
            logger.info("Signal Performance Tracker + Self-Learning Filter: ENABLED")
        except Exception as e:
            logger.warning(f"Signal tracker init failed: {e}")

        # --- Apply trend filter mode ---
        if config.trend_filter_mode:
            try:
                import strategy.custom_strategy as custom_strategy
                custom_strategy.TREND_FILTER_MODE = config.trend_filter_mode
                logger.info(f"Trend filter mode: {config.trend_filter_mode}")
            except Exception:
                pass

    def run(self):
        """Uruchom glowna petle bota."""
        self._running = True

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        all_syms = self.config.get_all_symbols()
        
        logger.info("=" * 60)
        logger.info("  MULTI-ASSET SIGNAL BOT v5 (GLM AI + KOMBAJN)")
        logger.info(f"  Stochastic ({self.config.stoch_k_length}, {self.config.stoch_k_smooth}, {self.config.stoch_d_smooth})")
        logger.info(f"  Symbols: {', '.join(all_syms)}")
        logger.info(f"  Timeframes: {', '.join(self.config.timeframes)}")
        logger.info(f"  Interval: {self.config.scan_interval}s")
        logger.info(f"  Market: {self.config.market_source}")
        logger.info(f"  Trend filter: {self.config.trend_filter_mode}")
        logger.info(f"  GLM AI Analyst: {'ENABLED' if self.glm_analyst else 'DISABLED'}")
        logger.info(f"  Market Scanner: {'ENABLED' if self.market_scanner else 'DISABLED'}")
        logger.info(f"  Mode: {'AUTO-TRADE' if self.config.auto_open_positions else 'ALERT ONLY'}")
        logger.info("=" * 60)

        # Wiadomosc powitalna na Discord
        if self.notifier and not self.test_mode:
            try:
                self.notifier.send_startup_message({
                    "symbols": all_syms,
                    "timeframes": self.config.timeframes,
                    "stoch_k_length": self.config.stoch_k_length,
                    "stoch_k_smooth": self.config.stoch_k_smooth,
                    "stoch_d_smooth": self.config.stoch_d_smooth,
                    "oversold_threshold": self.config.oversold_threshold,
                    "overbought_threshold": self.config.overbought_threshold,
                    "scan_interval": self.config.scan_interval,
                    "require_crossover": self.config.require_crossover,
                    "strategy_name": "NWO + Stoch + CVD v3 + GLM AI",
                    "use_training": True,
                    "glm_analyst": self.glm_analyst is not None,
                    "commodity_symbols": self.config.commodity_symbols,
                    "forex_symbols": self.config.forex_symbols,
                    "index_symbols": self.config.index_symbols,
                })
            except Exception as e:
                logger.warning(f"Nie mozna wyslac wiadomosci powitalnej: {e}")

        # --- IMMEDIATE FIRST SCAN ---
        # Send first Market Pulse right away so user sees the bot is alive
        # (otherwise bot can run for 1h+ with zero Discord output)
        if self.market_scanner and self.notifier and not self.test_mode:
            try:
                logger.info("Sending immediate first Market Pulse...")
                tf = self.config.timeframes[-1] if self.config.timeframes else "1h"
                pulse = self.market_scanner.generate_pulse(self.fetcher, all_syms, tf)
                if pulse:
                    embed = self.market_scanner.format_pulse_discord(pulse)
                    self.notifier.send_custom_embed(embed)
                    logger.info("First Market Pulse sent to Discord")
                else:
                    logger.warning("First Market Pulse returned None — data fetch may be failing!")
                    # Send a diagnostic message so user knows something's wrong
                    self.notifier.send_custom_embed({
                        "title": "⚠️ First Scan — No Data",
                        "color": 0xFF9800,
                        "fields": [
                            {"name": "Problem", "value": "Market Pulse returned no data. Check if Binance/YFinance is reachable.", "inline": False},
                            {"name": "Symbols", "value": ', '.join(all_syms[:10]), "inline": False},
                            {"name": "Fetcher", "value": type(self.fetcher).__name__, "inline": True},
                        ],
                        "footer": {"text": "Diagnostic — bot is running but may not receive data"},
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
            except Exception as e:
                logger.warning(f"First Market Pulse error: {e}")

        # Glowna petla
        while self._running:
            try:
                self._scan_cycle()
            except KeyboardInterrupt:
                break
            except Exception as e:
                self._errors += 1
                logger.error(f"Blad w cyklu skanowania: {e}")
                if self.notifier and self._errors <= 3:
                    try:
                        self.notifier.send_error(str(e))
                    except Exception:
                        pass

            # Czekaj do nastepnego skanu
            if self._running:
                wait_until_next = self._calculate_wait()
                if wait_until_next > 0:
                    waited = 0
                    while waited < wait_until_next and self._running:
                        time.sleep(min(5, wait_until_next - waited))
                        waited += 5

        logger.info("Bot zatrzymany.")

    def _scan_cycle(self):
        """Pojedynczy cykl skanowania wszystkich par i timeframe'ow."""
        self._scan_count += 1
        cycle_start = time.time()
        total_signals = 0

        logger.info(f"{'─'*50}")
        logger.info(f"Skan #{self._scan_count} — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")

        # --- Session Reporter (EARLY — before data-heavy scanning) ---
        # Run session check first so we never miss market open/close alerts
        # even if data fetching takes a long time
        if self.market_scanner and self.notifier and not self.test_mode:
            try:
                session_change = self.market_scanner.check_session_change()
                if session_change:
                    event_type, session_key, session_info = session_change
                    embed = self.market_scanner.format_session_discord(event_type, session_key, session_info)
                    self.notifier.send_custom_embed(embed)
                    logger.info(f"Session alert: {session_info.name} {event_type}")
            except Exception as e:
                logger.warning(f"Early session check error: {e}")

        # --- Position tracker: check SL/TP closes (intra-bar) ---
        if self.position_tracker:
            try:
                current_prices = self._get_current_prices()
                current_bars = self._get_current_bars()
                closed_positions = self.position_tracker.check_closes(current_prices, current_bars)
                for pos in closed_positions:
                    emoji = "WIN" if pos.pnl and pos.pnl > 0 else "LOSS"
                    logger.info(f"  {emoji} POSITION CLOSED: {pos.direction} {pos.symbol} @ ${pos.close_price:,.2f} | PnL: ${pos.pnl:+,.2f}")
                    if self.notifier and not self.test_mode:
                        try:
                            self._send_position_close_notification(pos)
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"Position check error: {e}")  # OK — may have no positions

        # --- Scan symbols ---
        all_symbols = self.config.get_all_symbols()

        for timeframe in self.config.timeframes:
            logger.info(f"  TF: {timeframe}")

            for symbol in all_symbols:
                # Skip invalid TF/symbol combinations
                if not self._is_valid_tf_for_symbol(symbol, timeframe):
                    continue

                try:
                    signals = self._check_symbol(symbol, timeframe)
                    if signals:
                        for sig in signals:
                            # Check cooldown — silniejszy sygnal moze nadpisac slabszy
                            cooldown_key = f"{sig.symbol}_{sig.timeframe}_{sig.signal_type}"
                            sig_strength = self._signal_strength(sig)
                            if self._is_on_cooldown(cooldown_key):
                                prev_strength = self._cooldown_strength(cooldown_key)
                                if sig_strength <= prev_strength:
                                    logger.debug(f"    {symbol}: sygnal {sig.signal_type} na cooldown (strength {sig_strength} <= prev {prev_strength})")
                                    continue
                                else:
                                    logger.info(f"    {symbol}: {sig.signal_type} OVERRIDE — nowy strength {sig_strength} > prev {prev_strength}")

                            # --- GLM AI Signal Scorer ---
                            if self.glm_analyst and self.config.glm_signal_scorer:
                                try:
                                    score = self.glm_analyst.score_signal({
                                        "symbol": sig.symbol,
                                        "direction": sig.signal_type,
                                        "price": sig.price,
                                        "stoch_k": sig.k_value,
                                        "stoch_d": sig.d_value,
                                        "nwo_osc": sig.extra_data.get("osc", 0),
                                        "nwo_histogram": sig.extra_data.get("histogram", 0),
                                        "cvd": sig.extra_data.get("cvd", 0),
                                        "trend": sig.extra_data.get("trend", "?"),
                                        "atr": sig.extra_data.get("atr", 0),
                                        "sl": sig.extra_data.get("sl", 0),
                                        "tp": sig.extra_data.get("tp", 0),
                                        "source": sig.extra_data.get("source", ""),
                                        "confidence": sig.extra_data.get("confidence", "LOW"),
                                        "against_trend": sig.extra_data.get("against_trend", False),
                                    })
                                    if score:
                                        sig.extra_data["glm_score"] = score.score
                                        sig.extra_data["glm_recommendation"] = score.recommendation
                                        sig.extra_data["glm_analysis"] = score.analysis
                                        sig.extra_data["glm_key_factors"] = score.key_factors
                                        sig.extra_data["glm_risks"] = score.risks
                                        
                                        # Skip if GLM says SKIP and score <= 2
                                        if score.recommendation == "SKIP" and score.score <= 2:
                                            logger.debug(f"    {symbol}: GLM SKIP (score={score.score})")
                                            continue
                                except Exception as e:
                                    logger.debug(f"GLM scorer error: {e}")

                            # --- GLM AI: Market Regime Detector ---
                            if self.glm_analyst and self.config.glm_regime_detector and not self.test_mode:
                                try:
                                    regime_data = {
                                        "price": sig.price,
                                        "stoch_k": sig.k_value,
                                        "stoch_d": sig.d_value,
                                        "nwo_osc": sig.extra_data.get("osc", 0),
                                        "nwo_histogram": sig.extra_data.get("histogram", 0),
                                        "cvd": sig.extra_data.get("cvd", 0),
                                        "atr": sig.extra_data.get("atr", 0),
                                        "trend": sig.extra_data.get("trend", "?"),
                                    }
                                    regime = self.glm_analyst.detect_regime(sig.symbol, regime_data)
                                    if regime:
                                        sig.extra_data["glm_regime"] = regime.regime
                                        sig.extra_data["glm_regime_bias"] = regime.bias
                                except Exception:
                                    pass

                            # --- GLM AI: Multi-TF Confluence ---
                            if self.glm_analyst and self.config.glm_multi_tf_confluence and not self.test_mode:
                                try:
                                    tf_data = {}
                                    for tf in self.config.timeframes:
                                        try:
                                            tf_df = self.fetcher.fetch_ohlcv(sig.symbol, tf)
                                            if not tf_df.empty and len(tf_df) >= 120:
                                                from strategy.custom_strategy import get_current_nwo_state
                                                tf_state = get_current_nwo_state(tf_df, sig.symbol, tf)
                                                if tf_state:
                                                    tf_data[tf] = {
                                                        "stoch_k": tf_state.get("stoch_k", 0) or 0,
                                                        "stoch_d": tf_state.get("stoch_d", 0) or 0,
                                                        "nwo_osc": tf_state.get("osc", 0) or 0,
                                                        "nwo_histogram": tf_state.get("histogram", 0) or 0,
                                                        "cvd": tf_state.get("cvd", 0) or 0,
                                                        "trend": tf_state.get("trend", "?"),
                                                        "price": tf_state.get("price", 0),
                                                    }
                                        except Exception:
                                            continue
                                    if len(tf_data) >= 2:
                                        confluence = self.glm_analyst.analyze_confluence(sig.symbol, tf_data)
                                        if confluence:
                                            sig.extra_data["glm_confluence_score"] = confluence.score
                                            sig.extra_data["glm_confluence_direction"] = confluence.direction
                                            sig.extra_data["glm_confluence_strongest_tf"] = confluence.strongest_tf
                                except Exception:
                                    pass

                            # --- Self-Learning Filter ---
                            if self.self_learning_filter:
                                filter_data = {
                                    "source": sig.extra_data.get("source", "STOCH-ONLY"),
                                    "timeframe": sig.timeframe,
                                    "against_trend": sig.extra_data.get("against_trend", False),
                                }
                                should_send, filter_reason = self.self_learning_filter.should_send(filter_data)
                                if not should_send:
                                    logger.info(f"    FILTERED: {symbol} {sig.signal_type} — {filter_reason}")
                                    continue

                            # --- Record signal PRZED wysłaniem ---
                            record_id = None
                            if self.signal_tracker:
                                record_id = self.signal_tracker.record_signal({
                                    "symbol": sig.symbol,
                                    "timeframe": sig.timeframe,
                                    "direction": sig.signal_type,
                                    "source": sig.extra_data.get("source", "STOCH-ONLY"),
                                    "price": sig.price,
                                    "sl": sig.extra_data.get("sl", 0),
                                    "tp": sig.extra_data.get("tp", 0),
                                    "stoch_k": sig.k_value,
                                    "stoch_d": sig.d_value,
                                    "nwo_histogram": sig.extra_data.get("histogram", 0),
                                    "cvd": sig.extra_data.get("cvd", 0),
                                    "trend": sig.extra_data.get("trend", "?"),
                                    "against_trend": sig.extra_data.get("against_trend", False),
                                    "confidence": sig.extra_data.get("confidence", "MEDIUM"),
                                    "glm_score": sig.extra_data.get("glm_score"),
                                    "glm_recommendation": sig.extra_data.get("glm_recommendation"),
                                })
                                sig.extra_data["perf_record_id"] = record_id

                            # Send to Discord
                            sent = False
                            if self.notifier and not self.test_mode:
                                sent = self.notifier.send_signal(sig)
                            elif self.test_mode:
                                sent = True

                            if sent:
                                self._signals_sent += 1
                                self._cooldowns[cooldown_key] = {
                                    "ts": time.time(),
                                    "strength": sig_strength,
                                    "source": sig.extra_data.get("source", "?"),
                                }
                                total_signals += 1

                                glm_tag = ""
                                if sig.extra_data.get("glm_score"):
                                    glm_tag = f" | GLM: {sig.extra_data['glm_score']}/10 {sig.extra_data['glm_recommendation']}"
                                
                                risk_tag = " RISKY" if sig.extra_data.get("against_trend") else ""
                                logger.info(f"    {sig.emoji} {symbol}: {sig.signal_type}{risk_tag} @ ${sig.price:,.2f} | K={sig.k_value} D={sig.d_value}{glm_tag}")

                                # Auto-open position (ONLY if auto_open_positions=True)
                                if self.position_tracker and self.config.auto_open_positions and not sig.extra_data.get("against_trend", False):
                                    try:
                                        self.position_tracker.open_position(
                                            symbol=sig.symbol,
                                            direction=sig.signal_type,
                                            entry_price=sig.price,
                                            sl=sig.extra_data.get("sl", 0),
                                            tp=sig.extra_data.get("tp", 0),
                                            timeframe=sig.timeframe,
                                            strategy=sig.strategy_name,
                                            signal_reason=sig.reason,
                                            atr=sig.extra_data.get("atr", 0),
                                            risk_level=sig.extra_data.get("risk_level", "NORMAL"),
                                        )
                                    except Exception as e:
                                        logger.debug(f"Position open error: {e}")

                except Exception as e:
                    logger.error(f"    X {symbol} {timeframe}: {e}")
                    continue

        elapsed = time.time() - cycle_start
        logger.info(f"  Skan zakonczony w {elapsed:.1f}s — sygnalow: {total_signals}")

        # --- GLM AI: Daily Briefing ---
        if self.glm_analyst and self.config.glm_daily_briefing:
            try:
                if self.glm_analyst.should_send_briefing():
                    self._send_daily_briefing()
            except Exception as e:
                logger.warning(f"GLM briefing error: {e}")

        # --- GLM AI: End-of-Day Summary ---
        if self.glm_analyst and self.config.glm_eod_summary:
            try:
                if self.glm_analyst.should_send_eod():
                    self._send_eod_summary()
            except Exception as e:
                logger.warning(f"GLM EOD error: {e}")

        # --- Market Scanner KOMBAJN ---
        if self.market_scanner and not self.test_mode:
            try:
                self._run_market_scanner(all_symbols)
            except Exception as e:
                logger.warning(f"Market Scanner error: {e}")

        # --- Breaking News Monitor ---
        if self._news_monitor and not self.test_mode:
            try:
                self._run_news_monitor()
            except Exception as e:
                logger.warning(f"News Monitor error: {e}")

        # --- Fear & Greed Index ---
        if self._fear_greed_monitor and not self.test_mode:
            try:
                self._run_fear_greed()
            except Exception as e:
                logger.warning(f"Fear & Greed error: {e}")

        # --- Funding Rate Monitor (co 10 cykli) ---
        if self._funding_monitor and not self.test_mode and self._scan_count % 10 == 0:
            try:
                self._run_funding_monitor()
            except Exception as e:
                logger.debug(f"Funding Rate error: {e}")  # OK — crypto only

        # --- Whale & Liquidation Alerts ---
        if self._whale_monitor and not self.test_mode:
            try:
                self._run_whale_monitor(all_symbols)
            except Exception as e:
                logger.debug(f"Whale/Liquidation error: {e}")  # OK — not critical

        # --- Economic Calendar (co cykl) ---
        if self._econ_calendar and not self.test_mode:
            try:
                self._run_econ_calendar()
            except Exception as e:
                logger.debug(f"Econ Calendar error: {e}")  # OK — not critical

        # --- Clean stale cooldowns ---
        if self._scan_count % 20 == 0:
            now = time.time()
            stale = []
            for k, v in self._cooldowns.items():
                ts = v["ts"] if isinstance(v, dict) else v
                if now - ts > self.config.cooldown_per_signal:
                    stale.append(k)
            for k in stale:
                del self._cooldowns[k]

        # --- NWO weights persistence (co 10 cykli ~10 min) ---
        if self._scan_count % 10 == 0:
            try:
                from strategy.custom_strategy import save_all_nwo_weights
                save_all_nwo_weights()
            except Exception:
                pass

        # --- Signal Performance Weekly Report (co 168 cykli ~3h, docelowo 1/tydzień) ---
        if self.signal_tracker and self.notifier and self._scan_count % 168 == 0 and not self.test_mode:
            try:
                embed = self.signal_tracker.format_discord_embed(days=7)
                self.notifier.send_custom_embed(embed)
                logger.info("Weekly Signal Performance Report sent to Discord")
            except Exception:
                pass

        # --- Periodic stats ---
        if self.position_tracker and self._scan_count % 10 == 0:
            try:
                stats = self.position_tracker.get_stats()
                if stats["total_trades"] > 0:
                    logger.info(f"  Positions: {stats['total_trades']} trades | WR: {stats['win_rate']}% | PnL: ${stats['total_pnl']:+,.2f}")
            except Exception:
                pass

        # Status update
        if self.notifier and not self.test_mode:
            if time.time() - self._last_status_time > self.config.status_interval:
                self._send_status_update()
                self._last_status_time = time.time()

    def _check_symbol(self, symbol: str, timeframe: str) -> List[Signal]:
        """Sprawdz jedna pare na jednym timeframe i zwroc sygnaly."""
        try:
            df = self.fetcher.fetch_ohlcv(symbol, timeframe)
            if df.empty:
                # Log on first few scans so user knows data is missing
                if self._scan_count <= 3:
                    logger.warning(f"No data for {symbol} {timeframe} — fetcher returned empty DataFrame")
                return []
        except Exception as e:
            # Log data fetch errors visibly (first 3 scans only to avoid spam)
            if self._scan_count <= 3:
                logger.warning(f"Data fetch error for {symbol} {timeframe}: {e}")
            return []

        # Diagnostic: log bar count on first scan to catch candles_per_fetch issues
        if self._scan_count == 1:
            strategy_min = 120  # strategy_nwo_stoch_cvd minimum
            bar_count = len(df)
            status = "OK" if bar_count >= strategy_min else f"TOO FEW (need {strategy_min}+)"
            logger.info(f"    {symbol} {timeframe}: {bar_count} bars fetched — {status}")

        if self.custom_strategy_fn:
            return self.custom_strategy_fn(df, symbol, timeframe)
        else:
            return self.detector.detect(df, symbol, timeframe)

    def _is_valid_tf_for_symbol(self, symbol: str, timeframe: str) -> bool:
        """Check if timeframe is valid for symbol based on asset class."""
        try:
            from fetchers.yfinance import get_asset_class, ASSET_TF_OVERRIDES
            asset_class = get_asset_class(symbol)
            if asset_class in ASSET_TF_OVERRIDES:
                return timeframe in ASSET_TF_OVERRIDES[asset_class]
        except ImportError:
            pass
        return True

    def _get_current_prices(self) -> Dict[str, float]:
        """Pobierz aktualne ceny dla otwartych pozycji."""
        prices = {}
        if not self.position_tracker:
            return prices

        open_positions = self.position_tracker.get_open_positions()
        symbols_needed = list(set(p.symbol for p in open_positions))

        for symbol in symbols_needed:
            try:
                price = self.fetcher.get_latest_price(symbol) if hasattr(self.fetcher, 'get_latest_price') else None
                if price is not None:
                    prices[symbol] = price
                else:
                    df = self.fetcher.fetch_ohlcv(symbol, self.config.timeframes[0])
                    if not df.empty:
                        prices[symbol] = df['close'].iloc[-1]
            except Exception:
                pass

        return prices

    def _get_current_bars(self) -> Dict[str, Dict[str, float]]:
        """
        Pobierz aktualny BAR (high/low/close) najnizszego TF dla otwartych pozycji.
        Uzywane przez position_tracker do INTRA-BAR detekcji SL/TP — knoty swiec.
        """
        bars = {}
        if not self.position_tracker:
            return bars
        open_positions = self.position_tracker.get_open_positions()
        symbols_needed = list(set(p.symbol for p in open_positions))
        # Najnizszy timeframe (najwyzsza rozdzielczosc) — wieksza szansa zlapania wybicia
        tf = self.config.timeframes[0] if self.config.timeframes else "5m"
        for symbol in symbols_needed:
            try:
                df = self.fetcher.fetch_ohlcv(symbol, tf)
                if not df.empty:
                    bars[symbol] = {
                        "high": float(df['high'].iloc[-1]),
                        "low": float(df['low'].iloc[-1]),
                        "close": float(df['close'].iloc[-1]),
                    }
            except Exception:
                pass
        return bars

        return prices

    def _send_daily_briefing(self):
        """Generate and send GLM AI daily briefing."""
        if not self.glm_analyst or not self.notifier:
            return
        
        try:
            all_symbols = self.config.get_all_symbols()
            snapshot = self.glm_analyst.get_market_snapshot(
                self.fetcher, all_symbols[:15], "1h"  # Limit to 15 symbols
            )
            
            if not snapshot:
                return
            
            briefing = self.glm_analyst.generate_briefing(snapshot)
            if not briefing:
                return
            
            # Build Discord embed
            bias_emoji = {
                "bullish": "🟢📈", "bearish": "🔴📉",
                "neutral": "⚪↔️", "mixed": "🟡🔄"
            }.get(briefing.overall_bias, "⚪")
            
            key_pairs_text = "\n".join(
                f"• {p.get('symbol', '?')}: {p.get('reason', '')}"
                for p in (briefing.key_pairs or [])[:5]
            )
            
            risks_text = "\n".join(
                f"• {r}" for r in (briefing.risk_events or [])[:5]
            )
            
            watchlist_text = ", ".join(briefing.watchlist or [])[:200]
            
            fields = [
                {"name": "Overall Bias", "value": f"{bias_emoji} {briefing.overall_bias.upper()}", "inline": True},
                {"name": "Key Pairs", "value": key_pairs_text or "N/A", "inline": False},
                {"name": "Risk Events", "value": risks_text or "N/A", "inline": False},
                {"name": "Watchlist", "value": watchlist_text or "N/A", "inline": False},
                {"name": "Summary", "value": briefing.summary or "N/A", "inline": False},
            ]
            
            embed = {
                "title": "🧠 GLM AI Daily Market Briefing",
                "color": 0x9C27B0,  # Purple for AI
                "fields": fields,
                "footer": {"text": f"GLM AI Analyst | {self.config.glm_model}"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            
            self.notifier.send_custom_embed(embed)
            logger.info("GLM Daily Briefing sent to Discord")
            
        except Exception as e:
            logger.warning(f"GLM briefing send error: {e}")

    def _send_eod_summary(self):
        """Generate and send GLM AI end-of-day summary."""
        if not self.glm_analyst or not self.notifier:
            return
        
        try:
            eod = self.glm_analyst.generate_eod_summary()
            if not eod:
                return
            
            lessons_text = "\n".join(f"• {l}" for l in (eod.lessons or [])[:5])
            
            fields = [
                {"name": "Total Signals", "value": str(eod.total_signals), "inline": True},
                {"name": "TAKE", "value": str(eod.signals_taken), "inline": True},
                {"name": "WATCH", "value": str(eod.signals_watched), "inline": True},
                {"name": "SKIP", "value": str(eod.signals_skipped), "inline": True},
                {"name": "Best Signal", "value": eod.best_signal, "inline": True},
                {"name": "Worst Signal", "value": eod.worst_signal, "inline": True},
                {"name": "Lessons", "value": lessons_text or "N/A", "inline": False},
                {"name": "Tomorrow Outlook", "value": eod.outlook or "N/A", "inline": False},
                {"name": "Summary", "value": eod.summary or "N/A", "inline": False},
            ]
            
            embed = {
                "title": "🧠 GLM AI End-of-Day Summary",
                "color": 0x3F51B5,  # Indigo
                "fields": fields,
                "footer": {"text": f"GLM AI Analyst | {self.config.glm_model}"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            
            self.notifier.send_custom_embed(embed)
            logger.info("GLM EOD Summary sent to Discord")
            
        except Exception as e:
            logger.warning(f"GLM EOD send error: {e}")

    def _run_market_scanner(self, all_symbols: List[str]):
        """Uruchom Market Scanner KOMBAJN — caly czas analizuje rynek."""
        if not self.market_scanner or not self.notifier:
            return

        tf = self.config.timeframes[0] if self.config.timeframes else "1h"

        # 1. Market Pulse (co 1h)
        try:
            if self.market_scanner.should_send_pulse():
                pulse = self.market_scanner.generate_pulse(self.fetcher, all_symbols, tf)
                if pulse:
                    embed = self.market_scanner.format_pulse_discord(pulse)
                    self.notifier.send_custom_embed(embed)
                    logger.info("Market Pulse sent to Discord")
        except Exception as e:
            logger.warning(f"Pulse error: {e}")

        # 2. Volatility Scanner (co cykl)
        try:
            vol_alerts = self.market_scanner.scan_volatility(self.fetcher, all_symbols, tf)
            if vol_alerts:
                embed = self.market_scanner.format_volatility_discord(vol_alerts)
                if embed:
                    self.notifier.send_custom_embed(embed)
                    logger.info(f"Volatility alerts sent: {len(vol_alerts)} assets")
        except Exception as e:
            logger.warning(f"Volatility scan error: {e}")

        # 3. S/R Monitor (co 5 cykli)
        if self._scan_count % 5 == 0:
            try:
                sr_alerts = self.market_scanner.scan_sr_levels(self.fetcher, all_symbols, tf)
                if sr_alerts:
                    embed = self.market_scanner.format_sr_discord(sr_alerts)
                    if embed:
                        self.notifier.send_custom_embed(embed)
                        logger.info(f"S/R alerts sent: {len(sr_alerts)} levels")
            except Exception as e:
                logger.warning(f"S/R scan error: {e}")

        # 4. Session Reporter (co cykl — sprawdza czy sesja sie otwiera/zamyka)
        try:
            session_change = self.market_scanner.check_session_change()
            if session_change:
                event_type, session_key, session_info = session_change
                embed = self.market_scanner.format_session_discord(event_type, session_key, session_info)
                self.notifier.send_custom_embed(embed)
                logger.info(f"Session alert: {session_info.name} {event_type}")
        except Exception as e:
            logger.warning(f"Session check error: {e}")

        # 5. Correlation Alert (co 10 cykli)
        if self._scan_count % 10 == 0:
            try:
                corr_divs = self.market_scanner.scan_correlations(self.fetcher, tf)
                if corr_divs:
                    embed = self.market_scanner.format_correlation_discord(corr_divs)
                    if embed:
                        self.notifier.send_custom_embed(embed)
                        logger.info(f"Correlation alerts sent: {len(corr_divs)} pairs")
            except Exception as e:
                logger.debug(f"Correlation scan error: {e}")  # OK — not critical

    def _run_news_monitor(self):
        """Uruchom Breaking News Monitor — sprawdza RSS feeds."""
        if not self._news_monitor or not self.notifier:
            return
        try:
            if self._news_monitor.should_check():
                breaking_news = self._news_monitor.check_feeds()
                if breaking_news:
                    # Send individual breaking news
                    for news in breaking_news[:5]:
                        embed = self._news_monitor.format_news_discord(news)
                        if embed:
                            self.notifier.send_custom_embed(embed)
                    # Batch for remaining
                    if len(breaking_news) > 5:
                        batch = self._news_monitor.format_news_batch_discord(breaking_news[5:])
                        if batch:
                            self.notifier.send_custom_embed(batch)
                    logger.info(f"Breaking News: {len(breaking_news)} articles")
        except Exception as e:
            logger.warning(f"News monitor run error: {e}")

    def _run_fear_greed(self):
        """Uruchom Fear & Greed Index Monitor."""
        if not self._fear_greed_monitor or not self.notifier:
            return
        try:
            if self._fear_greed_monitor.should_check():
                reading = self._fear_greed_monitor.check_and_alert()
                if reading:
                    embed = self._fear_greed_monitor.format_discord(reading)
                    if embed:
                        self.notifier.send_custom_embed(embed)
                    logger.info(f"Fear & Greed: {reading.value} ({reading.classification})")
        except Exception as e:
            logger.warning(f"Fear & Greed run error: {e}")

    def _run_funding_monitor(self):
        """Uruchom Funding Rate Monitor (crypto only)."""
        if not self._funding_monitor or not self.notifier:
            return
        try:
            extreme_rates = self._funding_monitor.check_extreme_rates()
            if extreme_rates:
                embed = self._funding_monitor.format_alert_discord(extreme_rates)
                if embed:
                    self.notifier.send_custom_embed(embed)
                logger.info(f"Funding Rate alerts: {len(extreme_rates)} extreme rates")
        except Exception as e:
            logger.debug(f"Funding rate run error: {e}")  # OK — crypto only

    def _run_whale_monitor(self, all_symbols: List[str]):
        """Uruchom Whale & Liquidation Monitor."""
        if not self._whale_monitor or not self.notifier:
            return
        try:
            # Liquidation detection (co cykl)
            liq_events = self._whale_monitor.detect_liquidations(
                self.fetcher, all_symbols, self.config.timeframes[0] if self.config.timeframes else "5m"
            )
            if liq_events:
                embed = self._whale_monitor.format_liquidation_discord(liq_events)
                if embed:
                    self.notifier.send_custom_embed(embed)
                logger.info(f"Liquidation alerts: {len(liq_events)} events")

            # Whale detection (co 3 cykle)
            if self._scan_count % 3 == 0:
                whale_acts = self._whale_monitor.detect_whale_activity(
                    self.fetcher, all_symbols[:5],
                    self.config.timeframes[0] if self.config.timeframes else "5m"
                )
                if whale_acts:
                    embed = self._whale_monitor.format_whale_discord(whale_acts)
                    if embed:
                        self.notifier.send_custom_embed(embed)
                    logger.info(f"Whale alerts: {len(whale_acts)} activities")
        except Exception as e:
            logger.debug(f"Whale monitor run error: {e}")  # OK — not critical

    def _run_econ_calendar(self):
        """Uruchom Economic Calendar — sprawdza nadchodzace wydarzenia."""
        if not self._econ_calendar or not self.notifier:
            return
        try:
            upcoming = self._econ_calendar.check_upcoming()
            for alert_level, event in upcoming[:3]:
                embed = self._econ_calendar.format_event_discord(alert_level, event)
                if embed:
                    self.notifier.send_custom_embed(embed)
            if upcoming:
                logger.info(f"Econ Calendar: {len(upcoming)} upcoming alerts")
        except Exception as e:
            logger.debug(f"Econ calendar run error: {e}")  # OK — not critical

    def _send_position_close_notification(self, pos):
        """Wyslij powiadomienie o zamknieciu pozycji na Discord."""
        # --- Signal Tracker: zamknij sygnał ---
        if self.signal_tracker:
            try:
                outcome = "WIN" if pos.pnl and pos.pnl > 0 else "LOSS"
                self.signal_tracker.close_signal_by_symbol(
                    symbol=pos.symbol,
                    direction=pos.direction,
                    close_price=pos.close_price or 0,
                    outcome=outcome,
                )
            except Exception:
                pass

        if not self.notifier:
            return

        emoji = "WIN" if pos.pnl and pos.pnl > 0 else "LOSS"
        pnl_str = f"${pos.pnl:+,.2f}" if pos.pnl else "N/A"

        embed = {
            "title": f"{emoji} Position Closed — {pos.symbol}",
            "color": 0x00E676 if pos.pnl and pos.pnl > 0 else 0xFF1744,
            "fields": [
                {"name": "Direction", "value": pos.direction, "inline": True},
                {"name": "Entry", "value": f"${pos.entry_price:,.2f}", "inline": True},
                {"name": "Exit", "value": f"${pos.close_price:,.2f}" if pos.close_price else "N/A", "inline": True},
                {"name": "PnL", "value": f"{emoji} {pnl_str}", "inline": True},
                {"name": "Reason", "value": pos.close_reason or "N/A", "inline": True},
                {"name": "Holding", "value": f"{pos.holding_time_hours:.1f}h" if pos.holding_time_hours else "N/A", "inline": True},
            ],
            "footer": {"text": "Position Tracker | Multi-Asset Signal Bot"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self.notifier.send_custom_embed(embed)

    def _is_on_cooldown(self, key: str) -> bool:
        """Sprawdz czy sygnal jest na cooldown (anti-spam)."""
        entry = self._cooldowns.get(key)
        if entry is None:
            return False
        # Backward-compat: stary format zapisywał sam timestamp jako float
        ts = entry["ts"] if isinstance(entry, dict) else entry
        elapsed = time.time() - ts
        return elapsed < self.config.cooldown_per_signal

    def _cooldown_strength(self, key: str) -> int:
        """Zwroc strength (0-3) ostatniego sygnalu pod tym kluczem; 0 = brak."""
        entry = self._cooldowns.get(key)
        if not isinstance(entry, dict):
            return 0
        return entry.get("strength", 0)

    @staticmethod
    def _signal_strength(sig) -> int:
        """Mapowanie source -> ranga sily. Wieksza = silniejszy sygnal.
        STOCH-ONLY = 1, STOCH+NWO = 2, STOCH STRICT+NWO = 3, CONFLUENCE = 3."""
        src = (sig.extra_data.get("source") or "").upper()
        if src == "CONFLUENCE":
            return 3
        if "STRICT" in src:
            return 3
        if "STOCH+NWO" in src:
            return 2
        return 1  # STOCH-ONLY i nieznane

    def _calculate_wait(self) -> float:
        """Oblicz ile sekund czekac do nastepnego skanu."""
        return float(self.config.scan_interval)

    def _send_status_update(self):
        """Wyslij status bota na Discord."""
        logger.info("  Wysylam status na Discord...")
        for symbol in self.config.symbols[:3]:
            for timeframe in self.config.timeframes[:1]:
                try:
                    df = self.fetcher.fetch_ohlcv(symbol, timeframe)
                    if df.empty:
                        continue
                    values = self.detector.get_current_values(df)
                    if values and self.notifier:
                        self.notifier.send_status(
                            symbol, timeframe, values,
                            detector_config={
                                "k_length": self.config.stoch_k_length,
                                "k_smooth": self.config.stoch_k_smooth,
                                "d_smooth": self.config.stoch_d_smooth,
                            }
                        )
                except Exception:
                    pass

    def _handle_signal(self, signum, frame):
        """Handler sygnalow systemowych (Ctrl+C)."""
        logger.info("Otrzymano sygnal zatrzymania...")
        self._running = False

    def stop(self):
        """Zatrzymaj bota."""
        self._running = False

    @property
    def stats(self) -> dict:
        result = {
            "running": self._running,
            "scans": self._scan_count,
            "signals_sent": self._signals_sent,
            "errors": self._errors,
            "cooldowns_active": len(self._cooldowns),
            "fetcher": self.fetcher.stats if hasattr(self.fetcher, 'stats') else {},
        }
        if self.position_tracker:
            result["positions"] = self.position_tracker.get_stats()
        if self.glm_analyst:
            result["glm_analyst"] = self.glm_analyst.stats
        if self.market_scanner:
            result["market_scanner"] = self.market_scanner.stats
        return result


# ==============================================================================
# ONE-SHOT SCAN
# ==============================================================================

def run_single_scan(config: BotConfig, test_mode: bool = True, strategy: str = "nwo_stoch_cvd"):
    """Uruchom pojedynczy skan i wyswietl wyniki (bez petli, bez Discord)."""
    from strategy.custom_strategy import get_current_nwo_state, strategy_nwo_stoch_cvd, STRATEGY_REGISTRY
    import strategy.custom_strategy as custom_strategy

    # Te same ustawienia strategii co w petli bota (wczesniej --scan je ignorowal)
    custom_strategy.set_closed_bar_mode(config.use_closed_bar)
    custom_strategy.set_stoch_only_enabled(config.allow_stoch_only)
    custom_strategy.TREND_FILTER_MODE = config.trend_filter_mode

    use_nwo = strategy == "nwo_stoch_cvd"

    logger.info("Jednorazowy skan — sprawdzam sygnaly...")
    if use_nwo:
        logger.info("Strategia: NWO + Stoch(7,3,2) + CVD")

    # Data fetcher
    if config.market_source in ("stocks", "both"):
        try:
            from fetchers.yfinance import UnifiedDataFetcher
            fetcher = UnifiedDataFetcher()
        except ImportError:
            fetcher = DataFetcher(
                exchange_id=config.exchange,
                rate_limit_ms=config.rate_limit_ms,
                cache_ttl_seconds=config.cache_ttl,
                candles_per_fetch=config.candles_per_fetch,
            )
    else:
        fetcher = DataFetcher(
            exchange_id=config.exchange,
            rate_limit_ms=config.rate_limit_ms,
            cache_ttl_seconds=config.cache_ttl,
            candles_per_fetch=config.candles_per_fetch,
        )

    detector = SignalDetector(
        stoch_k_length=config.stoch_k_length,
        stoch_k_smooth=config.stoch_k_smooth,
        stoch_d_smooth=config.stoch_d_smooth,
        oversold_threshold=config.oversold_threshold,
        overbought_threshold=config.overbought_threshold,
        require_crossover=config.require_crossover,
    )

    # Combine symbols
    all_symbols = config.get_all_symbols()

    for timeframe in config.timeframes:
        logger.info(f"\n  Timeframe: {timeframe}")
        logger.info(f"  {'Symbol':<14} {'Price':>10} {'NWO':>6} {'K':>6} {'D':>6} {'CVD':>6} {'Signal'}")
        logger.info(f"  {'─'*14} {'─'*10} {'─'*6} {'─'*6} {'─'*6} {'─'*6} {'─'*30}")

        for symbol in all_symbols:
            # Skip invalid TF/symbol combos
            try:
                from fetchers.yfinance import get_asset_class, ASSET_TF_OVERRIDES
                asset_class = get_asset_class(symbol)
                if asset_class in ASSET_TF_OVERRIDES and timeframe not in ASSET_TF_OVERRIDES[asset_class]:
                    continue
            except ImportError:
                pass

            try:
                df = fetcher.fetch_ohlcv(symbol, timeframe)
                if df.empty:
                    continue

                if use_nwo:
                    nwo_state = get_current_nwo_state(df, symbol, timeframe)
                    signals = strategy_nwo_stoch_cvd(df, symbol, timeframe)

                    if nwo_state:
                        signal_str = ""
                        for s in signals:
                            signal_str += f"{s.emoji} {s.signal_type} ({s.extra_data.get('source','')})"

                        risk = "HIGH" if any(s.extra_data.get("against_trend") for s in signals) else "OK"

                        logger.info(
                            f"  {symbol:<14} ${nwo_state['price']:>9,.2f} "
                            f"{nwo_state['osc']:>5.1f} "
                            f"{nwo_state['stoch_k'] or 0:>5.1f} {nwo_state['stoch_d'] or 0:>5.1f} "
                            f"{nwo_state['cvd'] or 0:>+5.2f} "
                            f"{signal_str or '—'}"
                        )

            except Exception as e:
                logger.error(f"  {symbol}: Blad — {e}")

    logger.info("\nSkan zakonczony.")


# ==============================================================================
# CLI ENTRY POINT
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Multi-Asset Signal Bot — Discord Notifier + GLM AI Analyst",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Przyklady:
  python bot.py --webhook https://discord.com/api/webhooks/...
  python bot.py --test
  python bot.py --scan
  python bot.py --config aggressive
  python bot.py --market both --glm-key YOUR_KEY
  python bot.py --symbols BTC/USDT,XAU/USD,EUR/USD,SP500,WIG,DAX,NIKKEI
        """
    )

    parser.add_argument('--webhook', '-w', type=str, default=None,
                        help='Discord Webhook URL')
    parser.add_argument('--test', '-t', action='store_true',
                        help='Tryb testowy (bez wysylki na Discord)')
    parser.add_argument('--scan', action='store_true',
                        help='Pojedynczy skan (bez petli)')
    parser.add_argument('--config', '-c', type=str, default='default',
                        choices=['default', 'aggressive', 'conservative', 'scalping', 'multi_asset'],
                        help='Preset konfiguracji')
    parser.add_argument('--symbols', type=str, default=None,
                        help='Lista par oddzielona przecinkami')
    parser.add_argument('--timeframes', '-tf', type=str, default=None,
                        help='Timeframe\'y oddzielone przecinkami')
    parser.add_argument('--oversold', type=float, default=None)
    parser.add_argument('--overbought', type=float, default=None)
    parser.add_argument('--no-crossover', action='store_true')
    parser.add_argument('--interval', type=int, default=None)
    parser.add_argument('--exchange', type=str, default=None)
    parser.add_argument('--role-id', type=str, default=None)
    parser.add_argument('--strategy', type=str, default='nwo_stoch_cvd',
                        choices=list(STRATEGY_REGISTRY.keys()))

    # --- v3 options ---
    parser.add_argument('--sentiment', action='store_true',
                        help='Wlacz AI news sentiment filter')
    parser.add_argument('--no-sentiment', action='store_true')
    parser.add_argument('--cryptopanic-key', type=str, default='')
    parser.add_argument('--finnhub-key', type=str, default='')
    parser.add_argument('--market', type=str, default=None,
                        choices=['crypto', 'stocks', 'both'],
                        help='Rynek: crypto (Binance), stocks (YFinance), both')
    parser.add_argument('--trend-filter', type=str, default=None,
                        choices=['alert', 'block', 'off'])
    parser.add_argument('--stoch-only', action='store_true',
                        help='Wlacz najslabszy tier STOCH-ONLY (sama strefa K, bez filtra NWO)')
    parser.add_argument('--position-size', type=float, default=None)
    parser.add_argument('--no-positions', action='store_true')
    parser.add_argument('--auto-trade', action='store_true')

    # --- Anti-repaint / execution realism ---
    parser.add_argument('--live-bar', action='store_true',
                        help='Licz sygnaly na biezacym (niezakmnietym) barze — szybciej, ale REPAINT')
    parser.add_argument('--apply-slippage', action='store_true',
                        help='Symuluj slippage przy open/close (do realistycznego PnL)')
    parser.add_argument('--slippage-pct', type=float, default=None,
                        help='Slippage w ulamku (np. 0.001 = 0.1%%)')

    # --- GLM AI Analyst options ---
    parser.add_argument('--glm-key', type=str, default='',
                        help='GLM API key (Zhipu AI ChatGLM)')
    parser.add_argument('--glm-model', type=str, default='glm-4-flash',
                        choices=['glm-4-flash', 'glm-4', 'glm-4-plus'],
                        help='GLM model (glm-4-flash = fast/cheap)')
    parser.add_argument('--no-glm', action='store_true',
                        help='Disable GLM AI Analyst')
    parser.add_argument('--glm-lang', type=str, default='pl',
                        choices=['pl', 'en'],
                        help='GLM response language')

    # --- Market Scanner KOMBAJN options ---
    parser.add_argument('--no-scanner', action='store_true',
                        help='Disable Market Scanner KOMBAJN')
    parser.add_argument('--scanner-pulse', type=int, default=None,
                        help='Market Pulse interval in seconds (default: 3600)')
    parser.add_argument('--no-scanner-pulse', action='store_true',
                        help='Disable Market Pulse')
    parser.add_argument('--no-scanner-vol', action='store_true',
                        help='Disable Volatility Scanner')
    parser.add_argument('--no-scanner-sr', action='store_true',
                        help='Disable S/R Monitor')
    parser.add_argument('--no-scanner-sessions', action='store_true',
                        help='Disable Session Reporter')
    parser.add_argument('--no-scanner-corr', action='store_true',
                        help='Disable Correlation Alert')

    # --- New KOMBAJN features ---
    parser.add_argument('--no-news', action='store_true',
                        help='Disable Breaking News Monitor')
    parser.add_argument('--no-fng', action='store_true',
                        help='Disable Fear & Greed Index')
    parser.add_argument('--no-funding', action='store_true',
                        help='Disable Funding Rate Monitor')
    parser.add_argument('--no-whale', action='store_true',
                        help='Disable Whale/Liquidation Alerts')
    parser.add_argument('--no-econ', action='store_true',
                        help='Disable Economic Calendar')
    parser.add_argument('--api', action='store_true',
                        help='Enable FastAPI REST API')
    parser.add_argument('--api-port', type=int, default=8080,
                        help='API port (default: 8080)')

    parser.add_argument('--log', type=str, default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'])

    args = parser.parse_args()

    # Wybierz preset konfiguracji
    if args.config == 'aggressive':
        config = config_aggressive()
    elif args.config == 'conservative':
        config = config_conservative()
    elif args.config == 'scalping':
        config = config_scalping()
    elif args.config == 'multi_asset':
        config = config_multi_asset()
    else:
        config = BotConfig()

    # Nadpisz konfiguracje z argumentow CLI
    if args.webhook:
        config.discord_webhook_url = args.webhook
    if args.symbols:
        config.symbols = [s.strip() for s in args.symbols.split(',')]
    if args.timeframes:
        config.timeframes = [tf.strip() for tf in args.timeframes.split(',')]
    if args.oversold is not None:
        config.oversold_threshold = args.oversold
    if args.overbought is not None:
        config.overbought_threshold = args.overbought
    if args.no_crossover:
        config.require_crossover = False
    if args.interval:
        config.scan_interval = args.interval
    if args.exchange:
        config.exchange = args.exchange
    if args.role_id:
        config.discord_role_id = args.role_id
    config.log_level = args.log

    # v2 config overrides
    if args.sentiment:
        config.use_sentiment = True
    if args.no_sentiment:
        config.use_sentiment = False
    if args.cryptopanic_key:
        config.cryptopanic_api_key = args.cryptopanic_key
    if args.finnhub_key:
        config.finnhub_api_key = args.finnhub_key
    if args.market:
        config.market_source = args.market
    if args.trend_filter:
        config.trend_filter_mode = args.trend_filter
    if args.stoch_only:
        config.allow_stoch_only = True
    if args.no_positions:
        config.use_position_tracking = False
    if args.position_size:
        config.default_position_size_usd = args.position_size
    if args.auto_trade:
        config.auto_open_positions = True

    # Anti-repaint / slippage flags
    if args.live_bar:
        config.use_closed_bar = False
    if args.apply_slippage:
        config.apply_slippage = True
    if args.slippage_pct is not None:
        config.slippage_pct = args.slippage_pct

    # GLM AI Analyst config overrides
    if args.glm_key:
        config.glm_api_key = args.glm_key
        config.use_glm_analyst = True
    if args.no_glm:
        config.use_glm_analyst = False
    if args.glm_model:
        config.glm_model = args.glm_model
    if args.glm_lang:
        config.glm_language = args.glm_lang

    # --- Market Scanner KOMBAJN config overrides ---
    if args.no_scanner:
        config.use_market_scanner = False
    if args.scanner_pulse is not None:
        config.scanner_pulse_interval = args.scanner_pulse
    if args.no_scanner_pulse:
        config.scanner_pulse = False
    if args.no_scanner_vol:
        config.scanner_volatility = False
    if args.no_scanner_sr:
        config.scanner_sr = False
    if args.no_scanner_sessions:
        config.scanner_sessions = False
    if args.no_scanner_corr:
        config.scanner_correlation = False

    # New KOMBAJN features
    if args.no_news:
        config.use_news_monitor = False
    if args.no_fng:
        config.use_fear_greed = False
    if args.no_funding:
        config.use_funding_rate = False
    if args.no_whale:
        config.use_whale_alerts = False
    if args.no_econ:
        config.use_econ_calendar = False
    if args.api:
        config.use_api = True
        config.api_port = args.api_port

    # Re-konfiguruj logging
    setup_logging(args.log)

    # Walidacja
    if not args.test and not args.scan:
        errors = config.validate()
        if errors:
            for err in errors:
                logger.error(f"Blad konfiguracji: {err}")
            logger.error("Uzyj --test do testowania bez Discord, lub podaj --webhook URL")
            sys.exit(1)

    # Wyswietl konfiguracje
    logger.info(f"\nKonfiguracja:\n{config.summary()}\n")

    # Uruchom
    if args.scan:
        run_single_scan(config, test_mode=args.test, strategy=args.strategy)
    else:
        bot = StochSignalBot(config, test_mode=args.test)

        if args.strategy != 'stoch_7_3_2' and STRATEGY_REGISTRY[args.strategy]['fn']:
            bot.custom_strategy_fn = STRATEGY_REGISTRY[args.strategy]['fn']
            logger.info(f"Strategia: {STRATEGY_REGISTRY[args.strategy]['name']}")

        # Start REST API in background thread if enabled
        if config.use_api:
            try:
                import threading
                from api.server import create_api_app, run_api_server
                import uvicorn

                app = create_api_app(bot)
                if app:
                    api_thread = threading.Thread(
                        target=uvicorn.run,
                        args=(app,),
                        kwargs={"host": config.api_host, "port": config.api_port, "log_level": "warning"},
                        daemon=True,
                    )
                    api_thread.start()
                    logger.info(f"REST API: http://{config.api_host}:{config.api_port}/api/")
                else:
                    logger.warning("REST API: FastAPI not available — install fastapi uvicorn")
            except ImportError:
                logger.warning("REST API: fastapi/uvicorn nie zainstalowane — pip install fastapi uvicorn")

        try:
            bot.run()
        except KeyboardInterrupt:
            bot.stop()


if __name__ == "__main__":
    main()
