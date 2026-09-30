"""
Data Fetcher Module
Pobiera dane OHLCV z giełd krypto przez ccxt.
Obsługuje wiele par i timeframe'ów z cache'owaniem.
"""

import time
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Optional, Dict
from collections import OrderedDict


class DataFetcher:
    """
    Pobiera świecze OHLCV z giełd kryptowalutowych.
    Cache'uje dane aby nie odpytywać giełdy za każdym skanem.
    """

    def __init__(
        self,
        exchange_id: str = "binance",
        rate_limit_ms: int = 300,
        cache_ttl_seconds: int = 30,
        candles_per_fetch: int = 100,
    ):
        self.exchange_id = exchange_id
        self.rate_limit_ms = rate_limit_ms
        self.cache_ttl_seconds = cache_ttl_seconds
        self.candles_per_fetch = candles_per_fetch
        self._exchange = None
        self._cache: OrderedDict = OrderedDict()  # key -> (df, timestamp)
        self._last_request_time = 0
        self._request_count = 0

    def _get_exchange(self):
        """Inicjalizuj połączenie z giełdą (lazy)."""
        if self._exchange is not None:
            return self._exchange

        try:
            import ccxt
            exchange_class = getattr(ccxt, self.exchange_id, None)
            if exchange_class is None:
                raise ValueError(f"Giełda '{self.exchange_id}' nie znaleziona w ccxt")

            self._exchange = exchange_class({
                'enableRateLimit': True,
                'options': {'defaultType': 'spot'},
            })
            self._exchange.load_markets()
            print(f"[DataFetcher] Połączono z {self.exchange_id}")
            return self._exchange

        except ImportError:
            raise RuntimeError("ccxt nie jest zainstalowany. Uruchom: pip install ccxt")
        except Exception as e:
            raise RuntimeError(f"Nie można połączyć z {self.exchange_id}: {e}")

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        Pobierz dane OHLCV dla pary i timeframe'u.
        Używa cache'u jeśli dane są świeże.
        
        Returns:
            DataFrame z kolumnami [open, high, low, close, volume] i DatetimeIndex
        """
        cache_key = f"{symbol}_{timeframe}"

        # Sprawdź cache
        if not force_refresh and cache_key in self._cache:
            cached_df, cached_time = self._cache[cache_key]
            age = time.time() - cached_time
            if age < self.cache_ttl_seconds:
                return cached_df

        # Rate limiting
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_ms / 1000.0:
            time.sleep(self.rate_limit_ms / 1000.0 - elapsed)

        exchange = self._get_exchange()

        try:
            # Oblicz since dla potrzebnej liczby świec
            tf_ms = exchange.parse_timeframe(timeframe) * 1000
            since = exchange.milliseconds() - (tf_ms * self.candles_per_fetch)

            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since, self.candles_per_fetch)

            if not ohlcv:
                print(f"[DataFetcher] Brak danych dla {symbol} {timeframe}")
                # Zwróć cache jeśli jest
                if cache_key in self._cache:
                    return self._cache[cache_key][0]
                return pd.DataFrame()

            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
            df.set_index('timestamp', inplace=True)
            df = df[~df.index.duplicated(keep='first')]
            df = df.sort_index()

            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)

            # Zapisz do cache
            self._cache[cache_key] = (df, time.time())
            self._last_request_time = time.time()
            self._request_count += 1

            # Ogranicz rozmiar cache
            if len(self._cache) > 50:
                self._cache.popitem(last=False)

            return df

        except Exception as e:
            print(f"[DataFetcher] Błąd pobierania {symbol} {timeframe}: {e}")
            # Zwróć stary cache jeśli jest
            if cache_key in self._cache:
                print(f"[DataFetcher] Używam cache dla {symbol} {timeframe}")
                return self._cache[cache_key][0]
            raise

    def fetch_multiple(
        self,
        symbols: list,
        timeframe: str,
        force_refresh: bool = False,
    ) -> Dict[str, pd.DataFrame]:
        """Pobierz dane dla wielu par jednocześnie."""
        results = {}
        for symbol in symbols:
            try:
                df = self.fetch_ohlcv(symbol, timeframe, force_refresh)
                if not df.empty:
                    results[symbol] = df
            except Exception as e:
                print(f"[DataFetcher] Błąd {symbol}: {e}")
        return results

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Pobierz aktualną cenę (ticker)."""
        try:
            exchange = self._get_exchange()
            ticker = exchange.fetch_ticker(symbol)
            return ticker.get('last', None)
        except Exception as e:
            print(f"[DataFetcher] Błąd ticker {symbol}: {e}")
            return None

    @property
    def stats(self) -> dict:
        return {
            "exchange": self.exchange_id,
            "requests_made": self._request_count,
            "cache_size": len(self._cache),
            "cached_pairs": list(self._cache.keys()),
        }
