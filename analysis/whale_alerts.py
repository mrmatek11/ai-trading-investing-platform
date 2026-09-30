"""
Whale + Liquidation Alerts Module
═══════════════════════════════════════════════════════════════════════════════════

Czysto algorytmiczny moduł — ZERO płatnych API.
Wykorzystuje dane z Binance (klines + orderbook) przez istniejący DataFetcher.

Komponenty:
  1. Liquidation Detector — wykrywa fale likwidacji na podstawie
     volume spikes + price moves (algorytmiczny, darmowy)
  2. Whale Transaction Monitor — monitoruje duże transakcje poprzez
     analizę orderbooka i nagłych ruchów cenowych z wysokim wolumenem

Logika likwidacji:
  - Wolumen w ostatnich 5 min > 3x średni wolumen
  - Ruch ceny > 0.5% w jednym kierunku
  - => Prawdopodobna fala likwidacji

Logika whale activity:
  - Monitorowanie orderbooka — duże ściany (> $500K)
  - Nagłe ruchy cenowe z wysokim wolumenem jako potencjalna aktywność wielorybów

Użycie:
  from analysis.whale_alerts import WhaleLiquidationMonitor
  monitor = WhaleLiquidationMonitor()
  events = monitor.detect_liquidations(fetcher, ["BTC/USDT", "ETH/USDT"])
  activities = monitor.detect_whale_activity(fetcher, ["BTC/USDT", "ETH/USDT"])
"""

import time
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from fetchers.binance import DataFetcher

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# KOLORY DLA DISCORD EMBEDÓW
# ═══════════════════════════════════════════════════════════════════════════════

COLOR_LIQUIDATION = 0xF44336      # Czerwony — likwidacje zawsze na czerwono
COLOR_WHALE_BULL  = 0x4CAF50      # Zielony — wieloryb kupuje
COLOR_WHALE_BEAR  = 0xF44336      # Czerwony — wieloryb sprzedaje


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class LiquidationEvent:
    """Zdarzenie likwidacji — wykryte algorytmicznie."""
    symbol: str
    direction: str              # "longs_liquidated" lub "shorts_liquidated"
    estimated_volume_usd: float # Szacowany wolumen likwidacji w USD
    price_move_pct: float       # Ruch ceny w %
    volume_ratio: float         # Obecny vol / średni vol
    timestamp: float


@dataclass
class WhaleActivity:
    """Aktywność wieloryba — wykryta algorytmicznie."""
    symbol: str
    activity_type: str          # "large_wall", "volume_spike", "price_surge"
    direction: str              # "bullish" lub "bearish"
    estimated_size_usd: float   # Szacowany rozmiar w USD
    details: str                # Opis szczegółów
    timestamp: float


# ═══════════════════════════════════════════════════════════════════════════════
# WHALE + LIQUIDATION MONITOR — GŁÓWNA KLASA
# ═══════════════════════════════════════════════════════════════════════════════

class WhaleLiquidationMonitor:
    """
    Monitor wielorybów i likwidacji — czysto algorytmiczny.

    Funkcje:
      1. Wykrywanie fal likwidacji (volume spike + price move)
      2. Wykrywanie kaskad likwidacji (wiele szybkich spadków z wysokim vol)
      3. Wykrywanie ścian wielorybów (duże bid/ask w orderbooku)
      4. Wykrywanie volume spike'ów jako potencjalna aktywność wielorybów

    Wszystko darmowe — dane z Binance klines + orderbook.
    """

    # ── Domyślne progi ──
    DEFAULT_LIQUIDATION_VOLUME_RATIO = 3.0    # Wolumen > 3x średni = podejrzenie likwidacji
    DEFAULT_LIQUIDATION_PRICE_MOVE_PCT = 0.5   # Ruch ceny > 0.5% = kierunek likwidacji
    DEFAULT_WHALE_WALL_USD = 500_000           # Ściana > $500K = wieloryb
    DEFAULT_WHALE_VOLUME_RATIO = 2.5          # Wolumen > 2.5x średni = podejrzenie wieloryba
    DEFAULT_WHALE_PRICE_SURGE_PCT = 1.0       # Nagły ruch > 1% = price surge
    CASCADE_WINDOW_SECONDS = 300              # 5 min — okno na kaskadę likwidacji
    CASCADE_MIN_EVENTS = 2                    # Min 2 zdarzenia = kaskada
    COOLDOWN_SECONDS = 300                    # 5 min cooldown per symbol — anty-spam
    VOL_HISTORY_LENGTH = 50                   # Ile świec do średniej wolumenu
    ORDERBOOK_DEPTH = 20                      # Ile poziomów orderbooka analizować

    def __init__(
        self,
        liq_volume_ratio: float = DEFAULT_LIQUIDATION_VOLUME_RATIO,
        liq_price_move_pct: float = DEFAULT_LIQUIDATION_PRICE_MOVE_PCT,
        whale_wall_usd: float = DEFAULT_WHALE_WALL_USD,
        whale_volume_ratio: float = DEFAULT_WHALE_VOLUME_RATIO,
        whale_price_surge_pct: float = DEFAULT_WHALE_PRICE_SURGE_PCT,
        cooldown_seconds: int = COOLDOWN_SECONDS,
    ):
        """
        Inicjalizuj monitor.

        Args:
            liq_volume_ratio: Próg volume ratio do detekcji likwidacji
            liq_price_move_pct: Próg ruchu ceny (%) do detekcji likwidacji
            whale_wall_usd: Minimalny rozmiar ściany (USD) do flagi wieloryba
            whale_volume_ratio: Próg volume ratio do detekcji aktywności wieloryba
            whale_price_surge_pct: Próg nagłego ruchu ceny (%) do detekcji price surge
            cooldown_seconds: Cooldown per symbol w sekundach (anty-spam)
        """
        self.liq_volume_ratio = liq_volume_ratio
        self.liq_price_move_pct = liq_price_move_pct
        self.whale_wall_usd = whale_wall_usd
        self.whale_volume_ratio = whale_volume_ratio
        self.whale_price_surge_pct = whale_price_surge_pct
        self.cooldown_seconds = cooldown_seconds

        # Historia wolumenów per symbol (do obliczania średniej)
        self._vol_history: Dict[str, List[float]] = {}

        # Cooldown per symbol — timestamp ostatniego alertu
        self._liq_cooldown: Dict[str, float] = {}
        self._whale_cooldown: Dict[str, float] = {}

        # Historia zdarzeń likwidacji (do detekcji kaskad)
        self._liq_events_history: Dict[str, List[LiquidationEvent]] = {}

        # Liczniki statystyk
        self._total_liq_detected = 0
        self._total_whale_detected = 0
        self._total_cascades = 0
        self._total_walls = 0
        self._scans_run = 0

        logger.info(
            f"WhaleLiquidationMonitor: INIT | "
            f"Liq vol ratio={liq_volume_ratio}x | "
            f"Liq price move={liq_price_move_pct}% | "
            f"Whale wall=${whale_wall_usd:,.0f} | "
            f"Whale vol ratio={whale_volume_ratio}x | "
            f"Cooldown={cooldown_seconds}s"
        )

    # ═════════════════════════════════════════════════════════════════════
    # CZĘŚĆ 1: DETEKCJA LIKWIDACJI
    # ═════════════════════════════════════════════════════════════════════

    def detect_liquidations(
        self,
        fetcher: DataFetcher,
        symbols: List[str],
        timeframe: str = "5m",
    ) -> List[LiquidationEvent]:
        """
        Wykryj fale likwidacji na podstawie volume spike'ów + ruchów cen.

        Algorytm:
          1. Pobierz świece OHLCV dla każdego symbolu
          2. Oblicz średni wolumen z ostatnich N świec
          3. Porównaj obecny wolumen ze średnią
          4. Jeśli vol > ratio * avg AND price_move > threshold => likwidacja
          5. Sprawdź cooldown żeby uniknąć spamu

        Args:
            fetcher: DataFetcher z modułu fetchers.binance
            symbols: Lista symboli (np. ["BTC/USDT", "ETH/USDT"])
            timeframe: Interwał świec (domyślnie "5m")

        Returns:
            Lista LiquidationEvent
        """
        self._scans_run += 1
        events: List[LiquidationEvent] = []

        for symbol in symbols:
            try:
                # Sprawdź cooldown — jeśli niedawno wysłano alert, pomiń
                if self._is_on_cooldown(symbol, self._liq_cooldown):
                    continue

                # Pobierz dane OHLCV
                df = fetcher.fetch_ohlcv(symbol, timeframe)
                if df.empty or len(df) < 10:
                    continue

                # Aktualizuj historię wolumenów
                self._update_vol_history(symbol, df)

                # Oblicz średni wolumen
                avg_volume = self._get_avg_volume(symbol)
                if avg_volume <= 0:
                    continue

                # Obecna świeca — wolumen i ruch ceny
                current_vol = float(df['volume'].iloc[-1])
                current_close = float(df['close'].iloc[-1])
                prev_close = float(df['close'].iloc[-2]) if len(df) > 1 else current_close

                volume_ratio = current_vol / avg_volume

                # Ruch ceny w procentach
                if prev_close > 0:
                    price_move_pct = ((current_close - prev_close) / prev_close) * 100
                else:
                    price_move_pct = 0.0

                # Kierunek likwidacji — cena spada => longs liquidated, cena rośnie => shorts liquidated
                if price_move_pct > 0:
                    direction = "shorts_liquidated"
                else:
                    direction = "longs_liquidated"

                # Sprawdź warunki likwidacji
                if volume_ratio >= self.liq_volume_ratio and abs(price_move_pct) >= self.liq_price_move_pct:
                    # Szacowany wolumen w USD (cena * wolumen)
                    estimated_usd = current_vol * current_close

                    event = LiquidationEvent(
                        symbol=symbol,
                        direction=direction,
                        estimated_volume_usd=estimated_usd,
                        price_move_pct=price_move_pct,
                        volume_ratio=volume_ratio,
                        timestamp=time.time(),
                    )
                    events.append(event)

                    # Zapisz do historii (do detekcji kaskad)
                    self._record_liq_event(symbol, event)

                    # Ustaw cooldown
                    self._liq_cooldown[symbol] = time.time()
                    self._total_liq_detected += 1

                    logger.info(
                        f"[Liquidation] {symbol} {direction} | "
                        f"Vol ratio: {volume_ratio:.1f}x | "
                        f"Price move: {price_move_pct:+.2f}% | "
                        f"Est. ${estimated_usd:,.0f}"
                    )

            except Exception as e:
                logger.debug(f"Liquidation scan error {symbol}: {e}")
                continue

        # Sprawdź kaskady (wiele likwidacji w krótkim czasie)
        cascade_events = self._detect_cascades(symbols)
        events.extend(cascade_events)

        if events:
            events.sort(key=lambda e: e.volume_ratio, reverse=True)
            logger.info(f"Liquidation scan: {len(events)} events detected across {len(symbols)} symbols")

        return events

    def _update_vol_history(self, symbol: str, df: pd.DataFrame) -> None:
        """Aktualizuj historię wolumenów dla symbolu."""
        if symbol not in self._vol_history:
            self._vol_history[symbol] = []

        # Dodaj wolumeny z ostatnich świec
        vols = df['volume'].astype(float).tolist()
        self._vol_history[symbol] = (self._vol_history[symbol] + vols)[-self.VOL_HISTORY_LENGTH:]

    def _get_avg_volume(self, symbol: str) -> float:
        """Oblicz średni wolumen z historii dla symbolu."""
        history = self._vol_history.get(symbol, [])
        if len(history) < 5:
            return 0.0
        # Użyj mediany jako bardziej odpornej na outliery
        return float(np.median(history))

    def _record_liq_event(self, symbol: str, event: LiquidationEvent) -> None:
        """Zapisz zdarzenie likwidacji do historii (do detekcji kaskad)."""
        if symbol not in self._liq_events_history:
            self._liq_events_history[symbol] = []

        self._liq_events_history[symbol].append(event)

        # Czyść stare zdarzenia (poza oknem kaskady)
        cutoff = time.time() - self.CASCADE_WINDOW_SECONDS
        self._liq_events_history[symbol] = [
            e for e in self._liq_events_history[symbol]
            if e.timestamp > cutoff
        ]

    def _detect_cascades(self, symbols: List[str]) -> List[LiquidationEvent]:
        """
        Wykryj kaskady likwidacji — wiele szybkich zdarzeń w krótkim czasie.

        Kaskada = 2+ zdarzenia likwidacji w tym samym kierunku w ciągu 5 min.
        Kaskady są groźniejsze bo wywołują efekt domina.
        """
        cascade_events: List[LiquidationEvent] = []

        for symbol in symbols:
            history = self._liq_events_history.get(symbol, [])
            if len(history) < self.CASCADE_MIN_EVENTS:
                continue

            # Grupuj zdarzenia po kierunku
            longs_events = [e for e in history if e.direction == "longs_liquidated"]
            shorts_events = [e for e in history if e.direction == "shorts_liquidated"]

            for group, direction in [(longs_events, "longs_liquidated"), (shorts_events, "shorts_liquidated")]:
                if len(group) >= self.CASCADE_MIN_EVENTS:
                    # Sprawdź czy wszystkie są w oknie czasowym
                    latest_ts = max(e.timestamp for e in group)
                    earliest_ts = min(e.timestamp for e in group)

                    if latest_ts - earliest_ts <= self.CASCADE_WINDOW_SECONDS:
                        # To kaskada — utwórz zdarzenie z podsumowaniem
                        total_vol_usd = sum(e.estimated_volume_usd for e in group)
                        avg_price_move = float(np.mean([e.price_move_pct for e in group]))
                        max_volume_ratio = max(e.volume_ratio for e in group)

                        cascade_event = LiquidationEvent(
                            symbol=symbol,
                            direction=f"{direction}_cascade",
                            estimated_volume_usd=total_vol_usd,
                            price_move_pct=avg_price_move,
                            volume_ratio=max_volume_ratio,
                            timestamp=latest_ts,
                        )
                        cascade_events.append(cascade_event)
                        self._total_cascades += 1

                        logger.warning(
                            f"[CASCADE] {symbol} {direction} cascade | "
                            f"Events: {len(group)} | "
                            f"Total est. ${total_vol_usd:,.0f} | "
                            f"Avg price move: {avg_price_move:+.2f}%"
                        )

                        # Wyczyść historię po wykryciu kaskady — nie spamuj
                        self._liq_events_history[symbol] = []

        return cascade_events

    # ═════════════════════════════════════════════════════════════════════
    # CZĘŚĆ 2: DETEKCJA AKTYWNOŚCI WIELORYBÓW
    # ═════════════════════════════════════════════════════════════════════

    def detect_whale_activity(
        self,
        fetcher: DataFetcher,
        symbols: List[str],
        timeframe: str = "5m",
    ) -> List[WhaleActivity]:
        """
        Wykryj aktywność wielorybów — ściany w orderbooku + volume spike'i.

        Metody detekcji (wszystkie algorytmiczne, bez płatnych API):
          1. Large Wall — analiza orderbooka, ściany bid/ask > $500K
          2. Volume Spike — wolumen > threshold bez wyraźnego ruchu ceny
             (potencjalna akumulacja/dystrybucja)
          3. Price Surge — nagły duży ruch ceny z wysokim wolumenem
             (potencjalny market order wieloryba)

        Args:
            fetcher: DataFetcher z modułu fetchers.binance
            symbols: Lista symboli (np. ["BTC/USDT", "ETH/USDT"])
            timeframe: Interwał świec (domyślnie "5m")

        Returns:
            Lista WhaleActivity
        """
        activities: List[WhaleActivity] = []

        for symbol in symbols:
            try:
                # Sprawdź cooldown
                if self._is_on_cooldown(symbol, self._whale_cooldown):
                    continue

                # ── Metoda 1: Ściany w orderbooku ──
                wall_activity = self._detect_orderbook_walls(fetcher, symbol)
                if wall_activity:
                    activities.append(wall_activity)
                    self._whale_cooldown[symbol] = time.time()
                    self._total_walls += 1

                # ── Metoda 2: Volume Spike (akumulacja/dystrybucja) ──
                vol_spike = self._detect_volume_spike(fetcher, symbol, timeframe)
                if vol_spike:
                    # Unikaj duplikatu jeśli ściana już wykryta
                    if not wall_activity or vol_spike.activity_type != wall_activity.activity_type:
                        activities.append(vol_spike)
                        if not wall_activity:
                            self._whale_cooldown[symbol] = time.time()

                # ── Metoda 3: Price Surge (market order wieloryba) ──
                surge = self._detect_price_surge(fetcher, symbol, timeframe)
                if surge:
                    activities.append(surge)
                    if not wall_activity:
                        self._whale_cooldown[symbol] = time.time()

                self._total_whale_detected += len([a for a in activities
                    if a.symbol == symbol and a.timestamp > time.time() - 2])

            except Exception as e:
                logger.debug(f"Whale scan error {symbol}: {e}")
                continue

        if activities:
            activities.sort(key=lambda a: a.estimated_size_usd, reverse=True)
            logger.info(f"Whale scan: {len(activities)} activities detected across {len(symbols)} symbols")

        return activities

    def _detect_orderbook_walls(
        self,
        fetcher: DataFetcher,
        symbol: str,
    ) -> Optional[WhaleActivity]:
        """
        Analizuj orderbook pod kątem dużych ścian bid/ask.

        Pobiera orderbook przez ccxt i szuka poziomów z > $500K.
        Duża ściana bid = opór pod wsparciem = bullish
        Duża ściana ask = opór nad oporem = bearish
        """
        try:
            exchange = fetcher._get_exchange()
            orderbook = exchange.fetch_order_book(symbol, limit=self.ORDERBOOK_DEPTH)

            bids = orderbook.get('bids', [])
            asks = orderbook.get('asks', [])

            if not bids and not asks:
                return None

            # Znajdź największą ścianę bid i ask
            largest_bid_wall = self._find_largest_wall(bids, "bid")
            largest_ask_wall = self._find_largest_wall(asks, "ask")

            # Wybierz większą z dwóch ścian
            best_wall = None
            if largest_bid_wall and largest_ask_wall:
                if largest_bid_wall['size_usd'] >= largest_ask_wall['size_usd']:
                    best_wall = largest_bid_wall
                else:
                    best_wall = largest_ask_wall
            elif largest_bid_wall:
                best_wall = largest_bid_wall
            elif largest_ask_wall:
                best_wall = largest_ask_wall

            if not best_wall:
                return None

            # Sprawdź czy rozmiar przekracza próg
            if best_wall['size_usd'] < self.whale_wall_usd:
                return None

            direction = "bullish" if best_wall['side'] == "bid" else "bearish"
            side_pl = "BID (kupno)" if best_wall['side'] == "bid" else "ASK (sprzedaż)"

            return WhaleActivity(
                symbol=symbol,
                activity_type="large_wall",
                direction=direction,
                estimated_size_usd=best_wall['size_usd'],
                details=(
                    f"Ściana {side_pl} ${best_wall['size_usd']:,.0f} "
                    f"na cenie ${best_wall['price']:,.2f} "
                    f"(ilość: {best_wall['amount']:.4f})"
                ),
                timestamp=time.time(),
            )

        except Exception as e:
            logger.debug(f"Orderbook analysis error {symbol}: {e}")
            return None

    def _find_largest_wall(
        self,
        levels: List[List[float]],
        side: str,
    ) -> Optional[Dict]:
        """
        Znajdź największą ścianę w poziomach orderbooka.

        Args:
            levels: Lista [price, amount] z orderbooka
            side: "bid" lub "ask"

        Returns:
            Dict z 'price', 'amount', 'size_usd', 'side' lub None
        """
        if not levels:
            return None

        largest = None
        largest_size = 0

        for level in levels:
            if len(level) < 2:
                continue

            price = float(level[0])
            amount = float(level[1])
            size_usd = price * amount

            if size_usd > largest_size:
                largest_size = size_usd
                largest = {
                    'price': price,
                    'amount': amount,
                    'size_usd': size_usd,
                    'side': side,
                }

        return largest

    def _detect_volume_spike(
        self,
        fetcher: DataFetcher,
        symbol: str,
        timeframe: str,
    ) -> Optional[WhaleActivity]:
        """
        Wykryj volume spike — wysoki wolumen bez proporcjonalnego ruchu ceny.

        To może wskazywać na akumulację (cicho kupują) lub dystrybucję (cicho sprzedają).
        Wieloryby często używają TWAP/VWAP żeby nie ruszać ceny za bardzo.
        """
        try:
            df = fetcher.fetch_ohlcv(symbol, timeframe)
            if df.empty or len(df) < 10:
                return None

            # Aktualizuj historię wolumenów
            self._update_vol_history(symbol, df)

            avg_volume = self._get_avg_volume(symbol)
            if avg_volume <= 0:
                return None

            current_vol = float(df['volume'].iloc[-1])
            volume_ratio = current_vol / avg_volume

            # Próg volume spike'u jest niższy niż dla likwidacji
            # bo wieloryb może działać cicho
            if volume_ratio < self.whale_volume_ratio:
                return None

            # Ruch ceny
            current_close = float(df['close'].iloc[-1])
            prev_close = float(df['close'].iloc[-2]) if len(df) > 1 else current_close

            if prev_close > 0:
                price_move_pct = ((current_close - prev_close) / prev_close) * 100
            else:
                price_move_pct = 0.0

            # Jeśli ruch ceny jest DUŻY, to bardziej wygląda na likwidację niż cichą akumulację
            # Więc volume spike bez dużego ruchu = potencjalna akumulacja/dystrybucja
            if abs(price_move_pct) >= self.liq_price_move_pct * 2:
                return None  # Zostaw to dla detektora likwidacji

            # Określ kierunek na podstawie zmiany ceny i wolumenu
            if price_move_pct > 0.1:
                direction = "bullish"
                interpretation = "Potencjalna akumulacja (cichy zakup)"
            elif price_move_pct < -0.1:
                direction = "bearish"
                interpretation = "Potencjalna dystrybucja (cicha sprzedaż)"
            else:
                # Cena płaska, ale wolumen wysoki — walka kupujących i sprzedających
                direction = "bullish"  # Domyślnie bullish — ktoś kupuje
                interpretation = "Wysoki wolumen bez ruchu ceny — walka/budowanie pozycji"

            estimated_usd = current_vol * current_close

            return WhaleActivity(
                symbol=symbol,
                activity_type="volume_spike",
                direction=direction,
                estimated_size_usd=estimated_usd,
                details=(
                    f"Volume spike {volume_ratio:.1f}x średniej | "
                    f"Price: {price_move_pct:+.2f}% | "
                    f"{interpretation}"
                ),
                timestamp=time.time(),
            )

        except Exception as e:
            logger.debug(f"Volume spike detection error {symbol}: {e}")
            return None

    def _detect_price_surge(
        self,
        fetcher: DataFetcher,
        symbol: str,
        timeframe: str,
    ) -> Optional[WhaleActivity]:
        """
        Wykryj nagły ruch ceny — potencjalny market order wieloryba.

        Nagły ruch > 1% w jednym kierunku z wolumenem > 2x średni = wieloryb
        wszedł rynkiem (market order).
        """
        try:
            df = fetcher.fetch_ohlcv(symbol, timeframe)
            if df.empty or len(df) < 5:
                return None

            # Analiza ostatnich 3 świec — szukamy nagłego przyspieszenia
            recent_closes = df['close'].iloc[-3:].astype(float).tolist()
            recent_vols = df['volume'].iloc[-3:].astype(float).tolist()

            if len(recent_closes) < 3 or len(recent_vols) < 3:
                return None

            # Aktualizuj średni wolumen
            self._update_vol_history(symbol, df)
            avg_volume = self._get_avg_volume(symbol)
            if avg_volume <= 0:
                return None

            # Ruch ceny w ostatniej świecy
            latest_close = recent_closes[-1]
            prev_close = recent_closes[-2]

            if prev_close > 0:
                price_move_pct = ((latest_close - prev_close) / prev_close) * 100
            else:
                price_move_pct = 0.0

            # Ruch musi być duży (> price_surge_pct)
            if abs(price_move_pct) < self.whale_price_surge_pct:
                return None

            # Wolumen musi być powyżej średniej
            latest_vol = recent_vols[-1]
            volume_ratio = latest_vol / avg_volume

            if volume_ratio < self.DEFAULT_WHALE_VOLUME_RATIO:
                return None  # Ruch bez wolumenu = fałszywy breakout

            # Prędkość ruchu — czy przyspiesza?
            # Porównaj ruch ostatniej świecy z dwiema poprzednimi
            move_1 = abs(recent_closes[-1] - recent_closes[-2])
            move_2 = abs(recent_closes[-2] - recent_closes[-3]) if len(recent_closes) > 2 else 0

            is_accelerating = move_1 > move_2 * 1.5 if move_2 > 0 else True

            direction = "bullish" if price_move_pct > 0 else "bearish"
            dir_pl = "W GÓRĘ" if direction == "bullish" else "W DÓŁ"

            speed_note = " | Przyspieszenie ruchu!" if is_accelerating else ""

            estimated_usd = latest_vol * latest_close

            return WhaleActivity(
                symbol=symbol,
                activity_type="price_surge",
                direction=direction,
                estimated_size_usd=estimated_usd,
                details=(
                    f"Nagły ruch {dir_pl} {price_move_pct:+.2f}% | "
                    f"Vol: {volume_ratio:.1f}x avg{speed_note} | "
                    f"Potencjalny market order wieloryba"
                ),
                timestamp=time.time(),
            )

        except Exception as e:
            logger.debug(f"Price surge detection error {symbol}: {e}")
            return None

    # ═════════════════════════════════════════════════════════════════════
    # COOLDOWN / ANTY-SPAM
    # ═════════════════════════════════════════════════════════════════════

    def _is_on_cooldown(self, symbol: str, cooldown_map: Dict[str, float]) -> bool:
        """Sprawdź czy symbol jest na cooldownie (aby uniknąć spamu)."""
        if symbol not in cooldown_map:
            return False

        elapsed = time.time() - cooldown_map[symbol]
        return elapsed < self.cooldown_seconds

    # ═════════════════════════════════════════════════════════════════════
    # FORMATOWANIE DISCORD
    # ═════════════════════════════════════════════════════════════════════

    def format_liquidation_discord(self, events: List[LiquidationEvent]) -> Optional[Dict]:
        """
        Formatuj zdarzenia likwidacji jako Discord embed.

        Kolor: czerwony (likwidacje zawsze na czerwono — to złe dla jednej strony rynku).

        Args:
            events: Lista LiquidationEvent do sformatowania

        Returns:
            Dict z polami embed'a Discord lub None
        """
        if not events:
            return None

        lines = []
        for event in events[:8]:  # Max 8 zdarzeń na embed
            # Ikona kierunku
            if "cascade" in event.direction:
                icon = ":fire::fire:"  # Kaskada — podwójny ogień
            elif event.direction == "longs_liquidated":
                icon = ":chart_with_downwards_trend:"  # Longs likwidowane — spadek
            else:
                icon = ":chart_with_upwards_trend:"  # Shorts likwidowane — wzrost

            # Kierunek po polsku
            direction_pl = {
                "longs_liquidated": "LONGS likwidowane",
                "shorts_liquidated": "SHORTS likwidowane",
                "longs_liquidated_cascade": "KASKADA LONGS",
                "shorts_liquidated_cascade": "KASKADA SHORTS",
            }.get(event.direction, event.direction)

            lines.append(
                f"{icon} **{event.symbol}** — {direction_pl}\n"
                f"   Ruch ceny: {event.price_move_pct:+.2f}% | "
                f"Vol: {event.volume_ratio:.1f}x avg | "
                f"Est. ${event.estimated_volume_usd:,.0f}"
            )

        text = "\n".join(lines)

        # Podsumowanie
        total_estimated = sum(e.estimated_volume_usd for e in events)
        cascade_count = sum(1 for e in events if "cascade" in e.direction)

        fields = [
            {
                "name": f":rotating_light: {len(events)} zdarzeń likwidacji",
                "value": text,
                "inline": False,
            },
            {
                "name": "Szacowany łączny wolumen",
                "value": f"${total_estimated:,.0f}",
                "inline": True,
            },
            {
                "name": "Kaskady",
                "value": str(cascade_count),
                "inline": True,
            },
        ]

        return {
            "title": "Liquidation Alert",
            "color": COLOR_LIQUIDATION,  # Czerwony
            "fields": fields,
            "footer": {
                "text": (
                    f"Whale+Liq Monitor | "
                    f"Vol ratio: {self.liq_volume_ratio}x | "
                    f"Price move: {self.liq_price_move_pct}%"
                ),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def format_whale_discord(self, activities: List[WhaleActivity]) -> Optional[Dict]:
        """
        Formatuj aktywności wielorybów jako Discord embed.

        Kolor: zielony (bullish) lub czerwony (bearish).

        Args:
            activities: Lista WhaleActivity do sformatowania

        Returns:
            Dict z polami embed'a Discord lub None
        """
        if not activities:
            return None

        lines = []
        dominant_direction = "bullish"  # Domyślnie bullish
        bearish_count = 0
        bullish_count = 0

        for activity in activities[:8]:  # Max 8 na embed
            # Ikony
            type_icons = {
                "large_wall": ":bricks:",
                "volume_spike": ":bar_chart:",
                "price_surge": ":rocket:",
            }
            icon = type_icons.get(activity.activity_type, ":whale:")

            # Kierunek
            dir_icon = ":green_circle:" if activity.direction == "bullish" else ":red_circle:"
            dir_text = "BULL" if activity.direction == "bullish" else "BEAR"

            if activity.direction == "bullish":
                bullish_count += 1
            else:
                bearish_count += 1

            # Typ po polsku
            type_pl = {
                "large_wall": "Ściana",
                "volume_spike": "Vol Spike",
                "price_surge": "Price Surge",
            }.get(activity.activity_type, activity.activity_type)

            lines.append(
                f"{icon} {dir_icon} **{activity.symbol}** — {type_pl} ({dir_text})\n"
                f"   ${activity.estimated_size_usd:,.0f} | {activity.details}"
            )

        # Określ dominujący kierunek
        dominant_direction = "bullish" if bullish_count >= bearish_count else "bearish"

        text = "\n".join(lines)

        embed_color = COLOR_WHALE_BULL if dominant_direction == "bullish" else COLOR_WHALE_BEAR
        color_name = "Zielony (Bullish)" if dominant_direction == "bullish" else "Czerwony (Bearish)"

        # Podsumowanie
        total_size = sum(a.estimated_size_usd for a in activities)
        walls_count = sum(1 for a in activities if a.activity_type == "large_wall")
        spikes_count = sum(1 for a in activities if a.activity_type == "volume_spike")
        surges_count = sum(1 for a in activities if a.activity_type == "price_surge")

        fields = [
            {
                "name": f":whale: {len(activities)} aktywności wielorybów",
                "value": text,
                "inline": False,
            },
            {
                "name": "Szacowany łączny rozmiar",
                "value": f"${total_size:,.0f}",
                "inline": True,
            },
            {
                "name": "Podział",
                "value": f"Ściany: {walls_count} | Spike'i: {spikes_count} | Surge'e: {surges_count}",
                "inline": True,
            },
            {
                "name": "Nastroje",
                "value": f"Bullish: {bullish_count} | Bearish: {bearish_count}",
                "inline": True,
            },
        ]

        return {
            "title": "Whale Activity Alert",
            "color": embed_color,
            "fields": fields,
            "footer": {
                "text": (
                    f"Whale+Liq Monitor | "
                    f"Wall: ${self.whale_wall_usd:,.0f} | "
                    f"Vol: {self.whale_volume_ratio}x | "
                    f"Color: {color_name}"
                ),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ═════════════════════════════════════════════════════════════════════
    # STATYSTYKI
    # ═════════════════════════════════════════════════════════════════════

    @property
    def stats(self) -> dict:
        """Zwróć statystyki monitora."""
        now = time.time()

        # Ile symboli jest na cooldownie
        liq_cooling = sum(
            1 for ts in self._liq_cooldown.values()
            if now - ts < self.cooldown_seconds
        )
        whale_cooling = sum(
            1 for ts in self._whale_cooldown.values()
            if now - ts < self.cooldown_seconds
        )

        return {
            "scans_run": self._scans_run,
            "total_liquidations_detected": self._total_liq_detected,
            "total_cascades_detected": self._total_cascades,
            "total_whale_activities_detected": self._total_whale_detected,
            "total_whale_walls_detected": self._total_walls,
            "symbols_with_vol_history": len(self._vol_history),
            "liq_cooldown_active": liq_cooling,
            "whale_cooldown_active": whale_cooling,
            "config": {
                "liq_volume_ratio": self.liq_volume_ratio,
                "liq_price_move_pct": self.liq_price_move_pct,
                "whale_wall_usd": self.whale_wall_usd,
                "whale_volume_ratio": self.whale_volume_ratio,
                "whale_price_surge_pct": self.whale_price_surge_pct,
                "cooldown_seconds": self.cooldown_seconds,
            },
        }
