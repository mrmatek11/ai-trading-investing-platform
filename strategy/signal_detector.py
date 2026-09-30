"""
Signal Detector Module
Wykrywa sygnały tradingowe na podstawie wskaźników technicznych.
Główny focus: Stochastic (7,3,2) - oversold/overbought z crossover/crossunder.
"""

import math
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════════════════════
# PRICE PRECISION HELPERS — kluczowe dla niskocenowych monet (DOGE, SHIB, PEPE)
# ═══════════════════════════════════════════════════════════════════════════════

def price_precision(price: float) -> int:
    """
    Zwraca liczbę miejsc po przecinku adekwatną dla danego poziomu ceny.

    Przykłady:
        100000  → 2  (BTC: $67,432.50)
        100     → 3  (BNB: $612.345)
        1.0     → 4  (XRP: $0.6234)
        0.1     → 5  (DOGE: $0.08234)
        0.001   → 7  (SHIB: $0.0000234)

    Używaj zamiast hardcoded round(x, 2) — to zaokrąglało SL/TP DOGE/SHIB do bezużytecznych wartości.
    """
    if price is None or not isinstance(price, (int, float)) or price <= 0 or math.isnan(price):
        return 2
    if price >= 1000:
        return 2
    if price >= 1:
        return 4
    if price >= 0.01:
        return 5
    if price >= 0.0001:
        return 7
    # mikro-ceny (np. SHIB, PEPE) — 8 miejsc
    return 8


def round_price(price: float, ref_price: Optional[float] = None) -> float:
    """
    Zaokrągla cenę używając precyzji dobranej do `ref_price` (lub samej ceny).

    Args:
        price: cena do zaokrąglenia (np. SL, TP)
        ref_price: cena referencyjna do oszacowania precyzji (np. current price)
                   jeśli None, używa price.
    """
    if price is None or (isinstance(price, float) and math.isnan(price)):
        return 0.0
    p = ref_price if ref_price is not None and ref_price > 0 else price
    return round(price, price_precision(p))


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Signal:
    """Reprezentuje pojedynczy sygnał tradingowy."""
    symbol: str
    timeframe: str
    signal_type: str          # "LONG" or "SHORT"
    strategy_name: str        # np. "Stoch Oversold"
    reason: str               # Opis powodu sygnału
    price: float
    k_value: float            # Stochastic %K
    d_value: float            # Stochastic %D
    timestamp: datetime
    extra_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def emoji(self) -> str:
        if self.signal_type == "LONG":
            return "🟢"
        return "🔴"

    @property
    def color_hex(self) -> int:
        if self.signal_type == "LONG":
            return 0x00E676   # zielony
        return 0xFF1744       # czerwony

    @property
    def arrow(self) -> str:
        if self.signal_type == "LONG":
            return "▲"
        return "▼"


# ═══════════════════════════════════════════════════════════════════════════════
# INDICATOR CALCULATIONS (zoptymalizowane pod live)
# ═══════════════════════════════════════════════════════════════════════════════

def calc_stoch(high: pd.Series, low: pd.Series, close: pd.Series,
               k_length: int = 7, k_smooth: int = 3, d_smooth: int = 2):
    """
    Stochastic Oscillator — PineScript ta.stoch() odpowiednik.
    
    PineScript:
        k = ta.sma(ta.stoch(close, high, low, 7), 3)
        d = ta.sma(k, 2)
    """
    lowest_low = low.rolling(window=k_length, min_periods=k_length).min()
    highest_high = high.rolling(window=k_length, min_periods=k_length).max()
    
    # Raw %K
    k_raw = 100.0 * (close - lowest_low) / (highest_high - lowest_low)
    k_raw = k_raw.replace([np.inf, -np.inf], np.nan)
    
    # Smoothed %K (SMA)
    k_line = k_raw.rolling(window=k_smooth, min_periods=k_smooth).mean()
    
    # %D (SMA of %K)
    d_line = k_line.rolling(window=d_smooth, min_periods=d_smooth).mean()
    
    return k_line, d_line


def calc_ema(source: pd.Series, length: int) -> pd.Series:
    return source.ewm(span=length, adjust=False, min_periods=length).mean()


def calc_rsi(source: pd.Series, length: int = 14) -> pd.Series:
    delta = source.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    alpha = 1.0 / length
    avg_gain = gain.ewm(alpha=alpha, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    alpha = 1.0 / length
    return true_range.ewm(alpha=alpha, adjust=False, min_periods=length).mean()


def calc_volume_sma(volume: pd.Series, length: int = 20) -> pd.Series:
    return volume.rolling(window=length, min_periods=length).mean()


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL DETECTOR
# ═══════════════════════════════════════════════════════════════════════════════

class SignalDetector:
    """
    Główna klasa detektora sygnałów.
    Konfigurowalne thresholdy Stochastic i warunki wejścia.
    """

    def __init__(
        self,
        stoch_k_length: int = 7,
        stoch_k_smooth: int = 3,
        stoch_d_smooth: int = 2,
        oversold_threshold: float = 20.0,
        overbought_threshold: float = 80.0,
        # Dodatkowe filtry
        require_crossover: bool = True,       # Wymaga crossoveru K nad D
        rsi_filter: bool = False,             # Filtr RSI (dodatkowy)
        rsi_oversold: float = 35.0,
        rsi_overbought: float = 65.0,
        volume_filter: bool = False,          # Filtr wolumenu
        volume_mult: float = 1.5,
        min_bars: int = 20,                   # Min bars do obliczeń
        use_closed_bar: bool = True,          # ANTI-REPAINT: bierz zamknięty bar (i=-2)
    ):
        self.stoch_k_length = stoch_k_length
        self.stoch_k_smooth = stoch_k_smooth
        self.stoch_d_smooth = stoch_d_smooth
        self.oversold_threshold = oversold_threshold
        self.overbought_threshold = overbought_threshold
        self.require_crossover = require_crossover
        self.rsi_filter = rsi_filter
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.volume_filter = volume_filter
        self.volume_mult = volume_mult
        self.min_bars = min_bars
        self.use_closed_bar = use_closed_bar

    def detect(self, df: pd.DataFrame, symbol: str, timeframe: str) -> List[Signal]:
        """
        Analizuje OHLCV DataFrame i zwraca listę sygnałów.
        Sprawdza ZAMKNIĘTY bar (i = -2) jeśli use_closed_bar=True (default), żeby uniknąć repaintu.
        """
        signals = []

        if len(df) < self.min_bars:
            return signals

        close = df['close']
        high = df['high']
        low = df['low']
        volume = df.get('volume', pd.Series(dtype=float))

        # ─── Oblicz wskaźniki ──────────────────────────────────────────────
        k_line, d_line = calc_stoch(
            high, low, close,
            self.stoch_k_length, self.stoch_k_smooth, self.stoch_d_smooth
        )
        rsi = calc_rsi(close, 14) if self.rsi_filter else None
        atr = calc_atr(high, low, close, 14)
        vol_sma = calc_volume_sma(volume, 20) if self.volume_filter and len(volume) > 0 else None

        # FIX: anti-repaint — używamy zamkniętego baru zamiast bieżącego
        if self.use_closed_bar and len(df) >= 2:
            i = len(df) - 2
        else:
            i = len(df) - 1

        # Sprawdź NaN
        if pd.isna(k_line.iloc[i]) or pd.isna(d_line.iloc[i]):
            return signals
        if i < 2:
            return signals

        k_now = k_line.iloc[i]
        d_now = d_line.iloc[i]
        k_prev = k_line.iloc[i - 1]
        d_prev = d_line.iloc[i - 1]
        current_price = close.iloc[i]
        current_atr = atr.iloc[i] if not pd.isna(atr.iloc[i]) else 0

        # ─── Crossover / Crossunder ────────────────────────────────────────
        k_cross_d_up = (k_prev <= d_prev) and (k_now > d_now)      # K przecina D od dołu
        k_cross_d_down = (k_prev >= d_prev) and (k_now < d_now)    # K przecina D od góry

        # ─── FILTR WOLUMENU ────────────────────────────────────────────────
        vol_ok = True
        if self.volume_filter and vol_sma is not None and not pd.isna(vol_sma.iloc[i]):
            vol_ok = volume.iloc[i] > vol_sma.iloc[i] * self.volume_mult

        # ─── FILTR RSI ─────────────────────────────────────────────────────
        rsi_val = rsi.iloc[i] if rsi is not None and not pd.isna(rsi.iloc[i]) else None

        # ═══════════════════════════════════════════════════════════════════
        # SYGNAŁ LONG: Stoch w strefie oversold + K crossover D
        # ═══════════════════════════════════════════════════════════════════
        long_condition = False
        long_reason = ""

        # Wariant 1: Crossover K nad D w strefie oversold (najsilniejszy sygnał)
        if k_now < self.oversold_threshold and d_now < self.oversold_threshold:
            if self.require_crossover:
                if k_cross_d_up:
                    long_condition = True
                    long_reason = f"Stoch OVERSOLD crossover: K({k_now:.1f}) crossed D({d_now:.1f}) < {self.oversold_threshold:.0f}"
            else:
                long_condition = True
                long_reason = f"Stoch OVERSOLD zone: K={k_now:.1f}, D={d_now:.1f} < {self.oversold_threshold:.0f}"

        # Wariant 2: K wychodzi ze strefy oversold (crosses above threshold)
        elif k_prev < self.oversold_threshold and k_now >= self.oversold_threshold:
            if self.require_crossover and k_cross_d_up:
                long_condition = True
                long_reason = f"Stoch EXIT oversold + crossover: K({k_now:.1f}) crossed above {self.oversold_threshold:.0f}"
            elif not self.require_crossover:
                long_condition = True
                long_reason = f"Stoch EXIT oversold: K({k_now:.1f}) crossed above {self.oversold_threshold:.0f}"

        # Dodatkowe filtry do LONG
        if long_condition and self.rsi_filter and rsi_val is not None:
            if rsi_val > self.rsi_oversold:
                long_condition = False  # RSI nie potwierdza oversold
        if long_condition and not vol_ok:
            long_condition = False

        if long_condition:
            signals.append(Signal(
                symbol=symbol,
                timeframe=timeframe,
                signal_type="LONG",
                strategy_name=f"Stoch({self.stoch_k_length},{self.stoch_k_smooth},{self.stoch_d_smooth})",
                reason=long_reason,
                price=current_price,
                k_value=round(k_now, 2),
                d_value=round(d_now, 2),
                timestamp=df.index[i] if isinstance(df.index[i], datetime) else pd.Timestamp(df.index[i]).to_pydatetime(),
                extra_data={
                    "atr": round_price(current_atr, current_price),
                    "rsi": round(rsi_val, 2) if rsi_val else None,
                    "k_prev": round(k_prev, 2),
                    "d_prev": round(d_prev, 2),
                }
            ))

        # ═══════════════════════════════════════════════════════════════════
        # SYGNAŁ SHORT: Stoch w strefie overbought + K crossunder D
        # ═══════════════════════════════════════════════════════════════════
        short_condition = False
        short_reason = ""

        # Wariant 1: Crossunder K pod D w strefie overbought
        if k_now > self.overbought_threshold and d_now > self.overbought_threshold:
            if self.require_crossover:
                if k_cross_d_down:
                    short_condition = True
                    short_reason = f"Stoch OVERBOUGHT crossunder: K({k_now:.1f}) crossed D({d_now:.1f}) > {self.overbought_threshold:.0f}"
            else:
                short_condition = True
                short_reason = f"Stoch OVERBOUGHT zone: K={k_now:.1f}, D={d_now:.1f} > {self.overbought_threshold:.0f}"

        # Wariant 2: K wychodzi ze strefy overbought (crosses below threshold)
        elif k_prev > self.overbought_threshold and k_now <= self.overbought_threshold:
            if self.require_crossover and k_cross_d_down:
                short_condition = True
                short_reason = f"Stoch EXIT overbought + crossunder: K({k_now:.1f}) crossed below {self.overbought_threshold:.0f}"
            elif not self.require_crossover:
                short_condition = True
                short_reason = f"Stoch EXIT overbought: K({k_now:.1f}) crossed below {self.overbought_threshold:.0f}"

        # Dodatkowe filtry do SHORT
        if short_condition and self.rsi_filter and rsi_val is not None:
            if rsi_val < self.rsi_overbought:
                short_condition = False
        if short_condition and not vol_ok:
            short_condition = False

        if short_condition:
            signals.append(Signal(
                symbol=symbol,
                timeframe=timeframe,
                signal_type="SHORT",
                strategy_name=f"Stoch({self.stoch_k_length},{self.stoch_k_smooth},{self.stoch_d_smooth})",
                reason=short_reason,
                price=current_price,
                k_value=round(k_now, 2),
                d_value=round(d_now, 2),
                timestamp=df.index[i] if isinstance(df.index[i], datetime) else pd.Timestamp(df.index[i]).to_pydatetime(),
                extra_data={
                    "atr": round_price(current_atr, current_price),
                    "rsi": round(rsi_val, 2) if rsi_val else None,
                    "k_prev": round(k_prev, 2),
                    "d_prev": round(d_prev, 2),
                }
            ))

        return signals

    def get_current_values(self, df: pd.DataFrame) -> Optional[Dict]:
        """Zwraca aktualne wartości wskaźników (do statusu/debugu)."""
        if len(df) < self.min_bars:
            return None

        close = df['close']
        high = df['high']
        low = df['low']

        k_line, d_line = calc_stoch(high, low, close, self.stoch_k_length, self.stoch_k_smooth, self.stoch_d_smooth)
        rsi = calc_rsi(close, 14)
        atr = calc_atr(high, low, close, 14)

        # Same closed-bar logic for status output
        if self.use_closed_bar and len(df) >= 2:
            i = len(df) - 2
        else:
            i = len(df) - 1
        if pd.isna(k_line.iloc[i]):
            return None

        cur_price = close.iloc[i]
        return {
            "price": cur_price,
            "stoch_k": round(k_line.iloc[i], 2),
            "stoch_d": round(d_line.iloc[i], 2),
            "rsi": round(rsi.iloc[i], 2) if not pd.isna(rsi.iloc[i]) else None,
            "atr": round_price(atr.iloc[i], cur_price) if not pd.isna(atr.iloc[i]) else None,
            "zone": "oversold" if k_line.iloc[i] < self.oversold_threshold else
                    "overbought" if k_line.iloc[i] > self.overbought_threshold else "neutral",
        }
