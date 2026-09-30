"""
Market Scanner Module 
═══════════════════════════════════════════════════════════════════════════

5 dodatkowych funkcji, zeby bot ZYL i caly czas cos robil:

1. Market Pulse — krotki raport co 1h (heartbeat, nie czekaj na 6h briefing)
2. Volatility Scanner — wykrywa nietypowe ruchy na wszystkich aktywach
3. Support/Resistance Monitor — sledzi kluczowe poziomy S/R i alarmuje
4. Session Reporter — raportuje otwarcie/zamkniecie sesji (Azja, Europa, US)
5. Correlation Alert — wykrywa rozstepy w skorelowanych aktywach

Wszystko wysyla na Discord jako embed'y.
Nie wymaga GLM API — czysto algorytmiczne, zero dodatkowych kosztow.
"""

import time
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from collections import OrderedDict

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class VolatilityAlert:
    """Alert o nietypowej zmiennosci."""
    symbol: str
    current_vol: float        # Obecna zmiennosc (%)
    avg_vol: float            # Srednia zmiennosc (%)
    ratio: float              # current / avg (>2.0 = alert)
    direction: str            # up, down, neutral
    price_change_pct: float   # Zmiana ceny w ostatnich N swiecach
    timestamp: float = 0.0


@dataclass
class SRLevel:
    """Poziom Support/Resistance."""
    price: float
    level_type: str           # "support" lub "resistance"
    strength: int             # Ile razy poziom byl testowany (1-5)
    distance_pct: float       # Odleglosc od obecnej ceny (%)


@dataclass
class SRAlert:
    """Alert o podejsciu/przebiciu S/R."""
    symbol: str
    level: SRLevel
    action: str               # "approaching", "breakout", "bounced"
    current_price: float
    timestamp: float = 0.0


@dataclass
class SessionInfo:
    """Informacja o sesji rynkowej."""
    name: str                 # "Asian", "European", "US"
    status: str               # "opening", "active", "closing", "closed"
    opens_at: str             # HH:MM CET
    closes_at: str            # HH:MM CET
    key_symbols: List[str]    # Symbole powiazane z sesja


@dataclass
class CorrelationDivergence:
    """Rozstep w korelacji miedzy aktywami."""
    symbol_a: str
    symbol_b: str
    normal_corr: float        # Normalna korelacja (z 30d)
    current_corr: float       # Obecna korelacja (z 7d)
    divergence: float         # |normal - current|
    direction_a: str          # "up" lub "down"
    direction_b: str          # "up" lub "down"
    timestamp: float = 0.0


@dataclass
class MarketPulse:
    """Krotki puls rynku — co 1h."""
    top_movers: List[Dict]    # [{symbol, change_pct, direction}]
    fear_greed_estimate: str  # Na podstawie vol + momentum
    regime_summary: Dict[str, int]  # {regime: count}
    active_alerts: int        # Ilosc aktywnych alertow
    timestamp: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET SCANNER — MAIN CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class MarketScanner:
    """
    Kombajn do rynku — caly czas analizuje, nie tylko czeka na sygnaly.
    
    Features:
      1. Market Pulse — co 1h krotki raport na Discord
      2. Volatility Scanner — wykrywa nietypowe ruchy
      3. S/R Monitor — sledzi kluczowe poziomy
      4. Session Reporter — raportuje otwarcia/zamkniecia sesji
      5. Correlation Alert — wykrywa rozstepy korelacji
    """

    # Sesje rynkowe (CET = UTC+1 / UTC+2 latem)
    SESSIONS = {
        "asian": {
            "name": "Azjatycka",
            "open_cet": "02:00",
            "close_cet": "10:00",
            "key_symbols": ["NIKKEI", "BTC/USDT", "ETH/USDT"],
        },
        "european": {
            "name": "Europejska",
            "open_cet": "09:00",
            "close_cet": "17:30",
            "key_symbols": ["DAX", "WIG", "XAU/USD", "EUR/USD"],
        },
        "us": {
            "name": "Amerykanska",
            "open_cet": "15:30",
            "close_cet": "22:00",
            "key_symbols": ["SP500", "DAX", "XAU/USD", "BTC/USDT"],
        },
    }

    # Pary skorelowane do monitorowania
    CORRELATED_PAIRS = [
        ("BTC/USDT", "ETH/USDT"),
        ("XAU/USD", "XAG/USD"),       # Zloto-Srebro
        ("SP500", "DAX"),              # Indeksy globalne
        ("BTC/USDT", "XAU/USD"),       # Risk-on / Risk-off
    ]

    def __init__(
        self,
        pulse_interval: int = 3600,         # 1h
        volatility_threshold: float = 2.0,  # current/avg > tego = alert
        sr_lookback: int = 50,              # Ile swiec do S/R
        sr_proximity_pct: float = 1.0,      # % odleglosci do alertu
        corr_divergence_threshold: float = 0.3,  # Min rozstep korelacji
        enabled_pulse: bool = True,
        enabled_volatility: bool = True,
        enabled_sr: bool = True,
        enabled_sessions: bool = True,
        enabled_correlation: bool = True,
    ):
        self.pulse_interval = pulse_interval
        self.volatility_threshold = volatility_threshold
        self.sr_lookback = sr_lookback
        self.sr_proximity_pct = sr_proximity_pct
        self.corr_divergence_threshold = corr_divergence_threshold

        # Feature toggles
        self.enabled_pulse = enabled_pulse
        self.enabled_volatility = enabled_volatility
        self.enabled_sr = enabled_sr
        self.enabled_sessions = enabled_sessions
        self.enabled_correlation = enabled_correlation

        # State
        self._last_pulse_time = 0
        self._last_session_check = ""
        self._reported_sessions: Dict[str, str] = {}  # session_name -> "opened"/"closed"
        self._sr_cache: OrderedDict = OrderedDict()    # symbol -> List[SRLevel]
        self._sr_cache_ttl = 1800                      # 30 min cache
        self._vol_history: Dict[str, List[float]] = {} # symbol -> recent vol readings
        self._corr_cache: Dict[str, Tuple[float, float]] = {}  # pair_key -> (normal, current)
        self._alert_count = 0

        logger.info(
            f"MarketScanner: INIT | Pulse={pulse_interval}s | "
            f"Vol threshold={volatility_threshold}x | "
            f"S/R proximity={sr_proximity_pct}% | "
            f"Corr threshold={corr_divergence_threshold}"
        )

    # ═════════════════════════════════════════════════════════════════════
    # 1. MARKET PULSE (co 1h)
    # ═════════════════════════════════════════════════════════════════════

    def should_send_pulse(self) -> bool:
        """Czy juz czas na Market Pulse?"""
        if not self.enabled_pulse:
            return False
        return time.time() - self._last_pulse_time >= self.pulse_interval

    def generate_pulse(self, fetcher, symbols: List[str], timeframe: str = "1h") -> Optional[MarketPulse]:
        """
        Generuj krotki puls rynku — top movers, regime, fear/greed.
        
        Args:
            fetcher: Data fetcher (unified/binance/yfinance)
            symbols: Lista symboli do sprawdzenia
            timeframe: Timeframe do analizy
        
        Returns:
            MarketPulse or None
        """
        if not self.enabled_pulse:
            return None

        movers = []
        regime_counts: Dict[str, int] = {}
        total_scanned = 0

        for symbol in symbols:
            try:
                df = fetcher.fetch_ohlcv(symbol, timeframe)
                if df.empty or len(df) < 20:
                    continue

                total_scanned += 1
                close = df['close'].iloc[-1]
                prev_close = df['close'].iloc[-2] if len(df) > 1 else close
                change_pct = ((close - prev_close) / prev_close) * 100 if prev_close else 0

                # Zmiennosc
                returns = df['close'].pct_change().dropna()
                vol = returns.std() * np.sqrt(365 * 24) * 100 if len(returns) > 5 else 0

                # Trend (prosty EMA)
                ema20 = df['close'].ewm(span=20).mean().iloc[-1]
                trend = "up" if close > ema20 else ("down" if close < ema20 else "neutral")

                # Regime (prosta klasyfikacja)
                if vol > 60:
                    regime = "volatile"
                elif trend == "up" and vol < 30:
                    regime = "trending_up"
                elif trend == "down" and vol < 30:
                    regime = "trending_down"
                elif vol < 15:
                    regime = "quiet"
                else:
                    regime = "ranging"

                regime_counts[regime] = regime_counts.get(regime, 0) + 1

                movers.append({
                    "symbol": symbol,
                    "change_pct": change_pct,
                    "direction": "up" if change_pct > 0 else "down",
                    "vol": vol,
                    "trend": trend,
                })

            except Exception as e:
                logger.debug(f"Pulse scan error {symbol}: {e}")  # OK — individual symbol error
                continue

        if not movers:
            return None

        # Sort by absolute change — top movers
        movers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
        top_movers = movers[:8]

        # Fear/Greed estimate na podstawie momentum + vol
        avg_change = np.mean([m["change_pct"] for m in movers])
        vol_values = [m["vol"] for m in movers if m["vol"] > 0]
        avg_vol = np.mean(vol_values) if vol_values else 0

        if avg_change > 2.0 and avg_vol < 40:
            fear_greed = "EXTREME GREED"
        elif avg_change > 0.5:
            fear_greed = "GREED"
        elif avg_change < -2.0 and avg_vol > 40:
            fear_greed = "EXTREME FEAR"
        elif avg_change < -0.5:
            fear_greed = "FEAR"
        else:
            fear_greed = "NEUTRAL"

        self._last_pulse_time = time.time()

        return MarketPulse(
            top_movers=top_movers,
            fear_greed_estimate=fear_greed,
            regime_summary=regime_counts,
            active_alerts=self._alert_count,
            timestamp=time.time(),
        )

    def format_pulse_discord(self, pulse: MarketPulse) -> Dict:
        """Formatuj Market Pulse jako Discord embed."""
        # Top movers
        movers_lines = []
        for m in pulse.top_movers[:8]:
            arrow = ":arrow_up:" if m["direction"] == "up" else ":arrow_down:"
            movers_lines.append(
                f"{arrow} **{m['symbol']}** {m['change_pct']:+.2f}% (vol: {m['vol']:.0f}%)"
            )
        movers_text = "\n".join(movers_lines)

        # Fear/Greed
        fg_emoji = {
            "EXTREME GREED": ":fire::fire:",
            "GREED": ":fire:",
            "NEUTRAL": ":white_circle:",
            "FEAR": ":snowflake:",
            "EXTREME FEAR": ":snowflake::snowflake:",
        }.get(pulse.fear_greed_estimate, ":white_circle:")

        # Regime summary
        regime_text = " | ".join(
            f"{k}: {v}" for k, v in sorted(pulse.regime_summary.items())
        )

        fields = [
            {"name": "Top Movers", "value": movers_text or "N/A", "inline": False},
            {"name": "Fear/Greed", "value": f"{fg_emoji} {pulse.fear_greed_estimate}", "inline": True},
            {"name": "Regimes", "value": regime_text or "N/A", "inline": True},
            {"name": "Active Alerts", "value": str(pulse.active_alerts), "inline": True},
        ]

        return {
            "title": "Market Pulse",
            "color": 0x00BCD4,  # Cyan
            "fields": fields,
            "footer": {"text": "Market Scanner | 1h Pulse"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ═════════════════════════════════════════════════════════════════════
    # 2. VOLATILITY SCANNER
    # ═════════════════════════════════════════════════════════════════════

    def scan_volatility(self, fetcher, symbols: List[str], timeframe: str = "1h") -> List[VolatilityAlert]:
        """
        Skanuj wszystkie aktywa pod katem nietypowej zmiennosci.
        
        Porownuje obecną zmiennosc (ATR-based) ze srednia z 30 swiec.
        Jesli current > threshold * avg -> ALARM.
        
        Returns:
            Lista VolatilityAlert
        """
        if not self.enabled_volatility:
            return []

        alerts = []

        for symbol in symbols:
            try:
                df = fetcher.fetch_ohlcv(symbol, timeframe)
                if df.empty or len(df) < 30:
                    continue

                # Oblicz zmiany procentowe
                df['pct_change'] = df['close'].pct_change() * 100
                df = df.dropna()

                if len(df) < 30:
                    continue

                # Srednia zmiennosc z ostatnich 30 swiec
                recent_vol = df['pct_change'].iloc[-10:].abs().mean()
                avg_vol = df['pct_change'].iloc[-30:].abs().mean()

                if avg_vol < 0.001:
                    continue  # Pomin bardzo niską vol

                ratio = recent_vol / avg_vol

                # Zmiana ceny
                price_change = df['pct_change'].iloc[-1]
                direction = "up" if price_change > 0 else "down"

                # Zapisz do historii
                if symbol not in self._vol_history:
                    self._vol_history[symbol] = []
                self._vol_history[symbol].append(ratio)
                if len(self._vol_history[symbol]) > 100:
                    self._vol_history[symbol] = self._vol_history[symbol][-100:]

                if ratio >= self.volatility_threshold:
                    alert = VolatilityAlert(
                        symbol=symbol,
                        current_vol=recent_vol,
                        avg_vol=avg_vol,
                        ratio=ratio,
                        direction=direction,
                        price_change_pct=price_change,
                        timestamp=time.time(),
                    )
                    alerts.append(alert)
                    self._alert_count += 1

            except Exception as e:
                logger.debug(f"Vol scan error {symbol}: {e}")  # OK — individual symbol error
                continue

        if alerts:
            alerts.sort(key=lambda a: a.ratio, reverse=True)
            logger.info(f"Volatility alerts: {len(alerts)} assets with vol ratio > {self.volatility_threshold}x")

        return alerts

    def format_volatility_discord(self, alerts: List[VolatilityAlert]) -> Optional[Dict]:
        """Formatuj volatility alerts jako Discord embed."""
        if not alerts:
            return None

        lines = []
        for a in alerts[:8]:
            arrow = ":chart_with_upwards_trend:" if a.direction == "up" else ":chart_with_downwards_trend:"
            lines.append(
                f"{arrow} **{a.symbol}** — {a.price_change_pct:+.2f}% | "
                f"Vol: {a.ratio:.1f}x avg ({a.current_vol:.2f}% vs {a.avg_vol:.2f}%)"
            )

        text = "\n".join(lines)

        return {
            "title": "Volatility Alert",
            "color": 0xFF9800,  # Orange
            "fields": [
                {"name": f"{len(alerts)} Assets with Unusual Volatility", "value": text, "inline": False},
            ],
            "footer": {"text": f"Market Scanner | Threshold: {self.volatility_threshold}x"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ═════════════════════════════════════════════════════════════════════
    # 3. SUPPORT/RESISTANCE MONITOR
    # ═════════════════════════════════════════════════════════════════════

    def detect_sr_levels(self, df: pd.DataFrame, lookback: int = 50) -> List[SRLevel]:
        """
        Wykryj poziomy S/R na podstawie local highs/lows.
        
        Metoda:
          - Znajdz lokalne ekstrema (highs/lows) z ostatnich N swiec
          - Grupuj ekstrema ktore sa blisko siebie (within 0.5%)
          - Ranking po ilosci testow
        
        Args:
            df: OHLCV DataFrame
            lookback: Ile swiec analizowac
        
        Returns:
            Lista SRLevel
        """
        if len(df) < lookback:
            lookback = len(df)
        
        data = df.tail(lookback)
        current_price = data['close'].iloc[-1]
        
        # Znajdz lokalne ekstrema
        highs = []
        lows = []
        
        for i in range(2, len(data) - 2):
            # Local high
            if (data['high'].iloc[i] > data['high'].iloc[i-1] and
                data['high'].iloc[i] > data['high'].iloc[i-2] and
                data['high'].iloc[i] > data['high'].iloc[i+1] and
                data['high'].iloc[i] > data['high'].iloc[i+2]):
                highs.append(data['high'].iloc[i])
            
            # Local low
            if (data['low'].iloc[i] < data['low'].iloc[i-1] and
                data['low'].iloc[i] < data['low'].iloc[i-2] and
                data['low'].iloc[i] < data['low'].iloc[i+1] and
                data['low'].iloc[i] < data['low'].iloc[i+2]):
                lows.append(data['low'].iloc[i])
        
        # Grupuj podobne poziomy (within 0.5%)
        def cluster_levels(prices, is_resistance: bool) -> List[SRLevel]:
            if not prices:
                return []
            
            prices = sorted(prices)
            clusters = [[prices[0]]]
            
            for p in prices[1:]:
                if abs(p - clusters[-1][-1]) / clusters[-1][-1] < 0.005:
                    clusters[-1].append(p)
                else:
                    clusters.append([p])
            
            levels = []
            for cluster in clusters:
                avg_price = np.mean(cluster)
                strength = min(len(cluster), 5)
                distance_pct = ((avg_price - current_price) / current_price) * 100
                
                level_type = "resistance" if avg_price > current_price else "support"
                if is_resistance and level_type != "resistance":
                    continue
                if not is_resistance and level_type != "support":
                    continue
                
                levels.append(SRLevel(
                    price=avg_price,
                    level_type=level_type,
                    strength=strength,
                    distance_pct=abs(distance_pct),
                ))
            
            return levels
        
        all_levels = cluster_levels(highs, True) + cluster_levels(lows, False)
        all_levels.sort(key=lambda l: l.distance_pct)
        
        return all_levels[:10]  # Top 10 levels

    def scan_sr_levels(self, fetcher, symbols: List[str], timeframe: str = "1h") -> List[SRAlert]:
        """
        Skanuj S/R poziomy dla wszystkich symboli i sprawdz czy cena jest blisko.
        
        Returns:
            Lista SRAlert
        """
        if not self.enabled_sr:
            return []

        alerts = []

        for symbol in symbols:
            try:
                # Sprawdz cache (z TTL)
                cache_key = f"sr_{symbol}"
                levels = None
                if cache_key in self._sr_cache:
                    cached_data = self._sr_cache[cache_key]
                    if isinstance(cached_data, tuple) and len(cached_data) == 2:
                        cached_levels, cached_time = cached_data
                        if time.time() - cached_time < self._sr_cache_ttl:
                            levels = cached_levels
                        else:
                            # Expired — remove from cache
                            del self._sr_cache[cache_key]

                df = fetcher.fetch_ohlcv(symbol, timeframe)
                if df.empty or len(df) < 20:
                    continue

                current_price = df['close'].iloc[-1]

                if levels is None:
                    levels = self.detect_sr_levels(df, self.sr_lookback)
                    self._sr_cache[cache_key] = (levels, time.time())
                    # Trim cache — remove oldest by TTL, not FIFO
                    if len(self._sr_cache) > 50:
                        now = time.time()
                        expired = [k for k, v in self._sr_cache.items()
                                   if isinstance(v, tuple) and len(v) == 2 and now - v[1] > self._sr_cache_ttl]
                        for k in expired:
                            del self._sr_cache[k]
                        # If still too many, remove oldest
                        if len(self._sr_cache) > 50:
                            oldest = min(self._sr_cache.items(),
                                         key=lambda x: x[1][1] if isinstance(x[1], tuple) else 0)
                            del self._sr_cache[oldest[0]]

                # Sprawdz czy cena jest blisko S/R
                for level in levels:
                    distance_pct = abs(current_price - level.price) / current_price * 100

                    if distance_pct < self.sr_proximity_pct:
                        # Podejscie do S/R
                        action = "approaching"
                        alerts.append(SRAlert(
                            symbol=symbol,
                            level=level,
                            action=action,
                            current_price=current_price,
                            timestamp=time.time(),
                        ))
                        self._alert_count += 1

                    elif distance_pct < 0.1 and level.level_type == "resistance" and current_price > level.price:
                        # Przebicie resistance
                        action = "breakout"
                        alerts.append(SRAlert(
                            symbol=symbol,
                            level=level,
                            action=action,
                            current_price=current_price,
                            timestamp=time.time(),
                        ))
                        self._alert_count += 1

                    elif distance_pct < 0.1 and level.level_type == "support" and current_price < level.price:
                        # Przebicie support
                        action = "breakout"
                        alerts.append(SRAlert(
                            symbol=symbol,
                            level=level,
                            action=action,
                            current_price=current_price,
                            timestamp=time.time(),
                        ))
                        self._alert_count += 1

            except Exception as e:
                logger.debug(f"S/R scan error {symbol}: {e}")  # OK — individual symbol error
                continue

        if alerts:
            logger.info(f"S/R alerts: {len(alerts)} level approaches/breakouts detected")

        return alerts

    def format_sr_discord(self, alerts: List[SRAlert]) -> Optional[Dict]:
        """Formatuj S/R alerts jako Discord embed."""
        if not alerts:
            return None

        lines = []
        for a in alerts[:8]:
            if a.action == "breakout":
                icon = ":triangular_flag_on_post:"
            elif a.action == "approaching":
                icon = ":warning:"
            else:
                icon = ":information_source:"

            level_type = a.level.level_type.upper()
            lines.append(
                f"{icon} **{a.symbol}** — {a.action.upper()} {level_type} "
                f"${a.level.price:,.2f} (strength: {a.level.strength}/5, "
                f"dist: {a.level.distance_pct:.2f}%)"
            )

        text = "\n".join(lines)

        return {
            "title": "Support/Resistance Monitor",
            "color": 0xE91E63,  # Pink
            "fields": [
                {"name": f"{len(alerts)} S/R Alerts", "value": text, "inline": False},
            ],
            "footer": {"text": f"Market Scanner | Proximity: {self.sr_proximity_pct}%"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ═════════════════════════════════════════════════════════════════════
    # 4. SESSION REPORTER
    # ═════════════════════════════════════════════════════════════════════

    def check_session_change(self) -> Optional[Tuple[str, str, SessionInfo]]:
        """
        Sprawdz czy jakas sesja rynkowa wlasnie sie otwiera/zamyka.
        
        Zwraca (event_type, session_key, session_info) lub None.
        event_type: "opening" lub "closing"
        """
        if not self.enabled_sessions:
            return None

        # Obecny czas CET (UTC+2 dla CEST, UTC+1 dla CET)
        now_utc = datetime.now(timezone.utc)
        # Proste CET = UTC+1 (zima) / UTC+2 (lato)
        cet_offset = timedelta(hours=2 if now_utc.month in range(4, 11) else 1)
        now_cet = now_utc + cet_offset
        current_hm = now_cet.strftime("%H:%M")
        today_key = now_cet.strftime("%Y-%m-%d")

        for session_key, session_data in self.SESSIONS.items():
            open_time = session_data["open_cet"]
            close_time = session_data["close_cet"]

            # Sprawdz otwarcie (w oknie +/-10 min)
            session_report_key_open = f"{today_key}_{session_key}_opened"
            session_report_key_close = f"{today_key}_{session_key}_closed"

            if self._is_within_minutes(current_hm, open_time, 10) and session_report_key_open not in self._reported_sessions:
                info = SessionInfo(
                    name=session_data["name"],
                    status="opening",
                    opens_at=open_time,
                    closes_at=close_time,
                    key_symbols=session_data["key_symbols"],
                )
                self._reported_sessions[session_report_key_open] = "reported"
                return ("opening", session_key, info)

            # Sprawdz zamkniecie
            if self._is_within_minutes(current_hm, close_time, 10) and session_report_key_close not in self._reported_sessions:
                info = SessionInfo(
                    name=session_data["name"],
                    status="closing",
                    opens_at=open_time,
                    closes_at=close_time,
                    key_symbols=session_data["key_symbols"],
                )
                self._reported_sessions[session_report_key_close] = "reported"
                return ("closing", session_key, info)

        # Cleanup old keys (keep only today's)
        keys_to_remove = [k for k in self._reported_sessions if not k.startswith(today_key)]
        for k in keys_to_remove:
            del self._reported_sessions[k]

        return None

    @staticmethod
    def _is_within_minutes(current_hm: str, target_hm: str, window_min: int) -> bool:
        """Check if current time is within ±window_min minutes of target time."""
        try:
            cur_h, cur_m = map(int, current_hm.split(':'))
            tgt_h, tgt_m = map(int, target_hm.split(':'))
            cur_total = cur_h * 60 + cur_m
            tgt_total = tgt_h * 60 + tgt_m
            return abs(cur_total - tgt_total) <= window_min
        except (ValueError, IndexError):
            return current_hm == target_hm

    def format_session_discord(self, event_type: str, session_key: str, session_info: SessionInfo) -> Dict:
        """Formatuj session alert jako Discord embed."""
        if event_type == "opening":
            title = f"Sesja {session_info.name} — OTWARCIE"
            color = 0x4CAF50  # Green
            icon = ":green_circle:"
        else:
            title = f"Sesja {session_info.name} — ZAMKNIECIE"
            color = 0xF44336  # Red
            icon = ":red_circle:"

        symbols_text = ", ".join(session_info.key_symbols)
        hours_text = f"{session_info.opens_at} - {session_info.closes_at} CET"

        return {
            "title": f"{icon} {title}",
            "color": color,
            "fields": [
                {"name": "Godziny", "value": hours_text, "inline": True},
                {"name": "Kluczowe aktywa", "value": symbols_text, "inline": True},
            ],
            "footer": {"text": "Market Scanner | Session Monitor"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ═════════════════════════════════════════════════════════════════════
    # 5. CORRELATION ALERT
    # ═════════════════════════════════════════════════════════════════════

    def scan_correlations(self, fetcher, timeframe: str = "1h") -> List[CorrelationDivergence]:
        """
        Sprawdz czy skorelowane aktywa sie rozstepuja.
        
        Oblicza korelacje z 30 swiec (normal) i 7 swiec (current).
        Jesli |normal - current| > threshold -> alarm.
        
        Returns:
            Lista CorrelationDivergence
        """
        if not self.enabled_correlation:
            return []

        divergences = []

        for sym_a, sym_b in self.CORRELATED_PAIRS:
            try:
                df_a = fetcher.fetch_ohlcv(sym_a, timeframe)
                df_b = fetcher.fetch_ohlcv(sym_b, timeframe)

                if df_a.empty or df_b.empty:
                    continue
                if len(df_a) < 30 or len(df_b) < 30:
                    continue

                # Oblicz zwroty
                returns_a = df_a['close'].pct_change().dropna()
                returns_b = df_b['close'].pct_change().dropna()

                # Wyrównaj dlugosc
                min_len = min(len(returns_a), len(returns_b))
                returns_a = returns_a.iloc[-min_len:]
                returns_b = returns_b.iloc[-min_len:]

                if min_len < 30:
                    continue

                # Normal correlation (30 swiec)
                normal_corr = returns_a.iloc[-30:].corr(returns_b.iloc[-30:])

                # Current correlation (7 swiec)
                current_corr = returns_a.iloc[-7:].corr(returns_b.iloc[-7:])

                # Sprawdz rozstep
                divergence = abs(normal_corr - current_corr)

                # Kierunki
                change_a = returns_a.iloc[-1]
                change_b = returns_b.iloc[-1]
                dir_a = "up" if change_a > 0 else "down"
                dir_b = "up" if change_b > 0 else "down"

                # Cache
                pair_key = f"{sym_a}_{sym_b}"
                self._corr_cache[pair_key] = (normal_corr, current_corr)

                if divergence >= self.corr_divergence_threshold:
                    divergences.append(CorrelationDivergence(
                        symbol_a=sym_a,
                        symbol_b=sym_b,
                        normal_corr=normal_corr,
                        current_corr=current_corr,
                        divergence=divergence,
                        direction_a=dir_a,
                        direction_b=dir_b,
                        timestamp=time.time(),
                    ))
                    self._alert_count += 1

            except Exception as e:
                logger.debug(f"Correlation scan error {sym_a}/{sym_b}: {e}")  # OK — individual pair error
                continue

        if divergences:
            divergences.sort(key=lambda d: d.divergence, reverse=True)
            logger.info(f"Correlation divergences: {len(divergences)} pairs diverging")

        return divergences

    def format_correlation_discord(self, divergences: List[CorrelationDivergence]) -> Optional[Dict]:
        """Formatuj correlation alerts jako Discord embed."""
        if not divergences:
            return None

        lines = []
        for d in divergences[:6]:
            arrow_a = ":arrow_up:" if d.direction_a == "up" else ":arrow_down:"
            arrow_b = ":arrow_up:" if d.direction_b == "up" else ":arrow_down:"
            lines.append(
                f":twisted_rightwards_arrows: **{d.symbol_a}** {arrow_a} vs **{d.symbol_b}** {arrow_b} | "
                f"Corr: {d.normal_corr:.2f} -> {d.current_corr:.2f} "
                f"(div: {d.divergence:.2f})"
            )

        text = "\n".join(lines)

        return {
            "title": "Correlation Divergence Alert",
            "color": 0x9C27B0,  # Purple
            "fields": [
                {"name": f"{len(divergences)} Pairs Diverging", "value": text, "inline": False},
            ],
            "footer": {"text": f"Market Scanner | Threshold: {self.corr_divergence_threshold}"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ═════════════════════════════════════════════════════════════════════
    # HELPER: Full scan (run all checks)
    # ═════════════════════════════════════════════════════════════════════

    def full_scan(self, fetcher, symbols: List[str], timeframe: str = "1h") -> Dict:
        """
        Uruchom wszystkie skanery i zwroc wyniki.
        
        Returns:
            {
                "volatility_alerts": List[VolatilityAlert],
                "sr_alerts": List[SRAlert],
                "correlation_divergences": List[CorrelationDivergence],
                "session_change": Optional[Tuple],
                "pulse": Optional[MarketPulse],
            }
        """
        results = {}

        # 1. Volatility scan
        results["volatility_alerts"] = self.scan_volatility(fetcher, symbols, timeframe)

        # 2. S/R scan (co 5 cykli, zeby nie spamowac)
        results["sr_alerts"] = self.scan_sr_levels(fetcher, symbols, timeframe)

        # 3. Correlation scan
        results["correlation_divergences"] = self.scan_correlations(fetcher, timeframe)

        # 4. Session check
        results["session_change"] = self.check_session_change()

        # 5. Pulse (jezeli czas)
        if self.should_send_pulse():
            results["pulse"] = self.generate_pulse(fetcher, symbols, timeframe)
        else:
            results["pulse"] = None

        return results

    @property
    def stats(self) -> dict:
        return {
            "pulse_interval": self.pulse_interval,
            "last_pulse": self._last_pulse_time,
            "sr_cache_size": len(self._sr_cache),
            "vol_history_symbols": len(self._vol_history),
            "corr_pairs_tracked": len(self._corr_cache),
            "total_alerts": self._alert_count,
            "sessions_reported": len(self._reported_sessions),
        }
