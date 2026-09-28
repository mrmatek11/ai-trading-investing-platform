"""
Data Fetcher — Yahoo Finance (indeksy, forex, towary, akcje)
═════════════════════════════════════════════════════════════════════════

Alternatywny provider danych do data_fetcher.py (który używa ccxt/Binance).

Obsługuje (v4):
  ─── Indeksy ───
  - S&P 500 (SPY / ^GSPC)
  - NASDAQ 100 (QQQ)
  - DAX (^GDAXI)
  - Nikkei 225 (^N225)
  - WIG / Polski rynek (EPOL — iShares MSCI Poland ETF)
  - Dow Jones (DIA)
  - FTSE (^FTSE)

  ─── Forex ───
  - EUR/USD (EURUSD=X)
  - GBP/USD, USD/JPY, USD/PLN, itd.

  ─── Towary (Commodities) ───
  - Złoto (GC=F / GLD)
  - Srebro (SI=F / SLV)
  - Ropa (CL=F)
  - Gaz ziemny (NG=F)

  ─── Akcje ───
  - Dowolne tickery (AAPL, TSLA, itd.)

Dane:
  - 1h, 4h, 1d: darmowe, miesiące/lat danych
  - 15m: darmowe, max 60 dni
  - 5m, 1m: darmowe, max 7-30 dni

Instalacja:
  pip install yfinance

Użycie:
  from data_fetcher_yfinance import YFinanceDataFetcher
  
  fetcher = YFinanceDataFetcher()
  df = fetcher.fetch_ohlcv("SPY", "1h")
"""

import time
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List
from collections import OrderedDict


# Mapowanie symboli krypto → Yahoo Finance tickery
CRYPTO_TO_YF = {
    "BTC/USDT": "BTC-USD",
    "ETH/USDT": "ETH-USD",
    "SOL/USDT": "SOL-USD",
    "BNB/USDT": "BNB-USD",
    "XRP/USDT": "XRP-USD",
    "ADA/USDT": "ADA-USD",
    "DOGE/USDT": "DOGE-USD",
    "AVAX/USDT": "AVAX-USD",
    "DOT/USDT": "DOT-USD",
    "LINK/USDT": "LINK-USD",
}

# Mapowanie indeksów → Yahoo Finance tickery
INDEX_TICKERS = {
    # ─── US ───
    "SP500": "SPY",          # S&P 500 ETF
    "^GSPC": "^GSPC",        # S&P 500 index bezpośrednio
    "US100": "QQQ",          # NASDAQ 100 ETF
    "NASDAQ": "QQQ",         # alias
    "DOW": "DIA",            # Dow Jones ETF
    # ─── Europa ───
    "DAX": "^GDAXI",         # DAX 40 (Niemcy)
    "FTSE": "^FTSE",         # FTSE 100 (UK)
    "CAC40": "^FCHI",        # CAC 40 (Francja)
    # ─── Azja ───
    "NIKKEI": "^N225",        # Nikkei 225 (Japonia)
    "NIKKEI225": "^N225",     # alias
    "HSI": "^HSI",           # Hang Seng (Hong Kong)
    # ─── Polska ───
    "WIG": "EPOL",           # iShares MSCI Poland ETF (proxy dla WIG)
    "WIG20": "EPOL",         # to samo — YFinance nie ma bezpośredniego WIG20
    "POLAND": "EPOL",        # alias
}

# Mapowanie forex → Yahoo Finance tickery
FOREX_TICKERS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "USDJPY=X",
    "USD/CHF": "USDCHF=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CAD": "USDCAD=X",
    "NZD/USD": "NZDUSD=X",
    "EUR/GBP": "EURGBP=X",
    "EUR/PLN": "EURPLN=X",
    "USD/PLN": "USDPLN=X",
    "EUR/JPY": "EURJPY=X",
    "GBP/JPY": "GBPJPY=X",
}

# Mapowanie towarów → Yahoo Finance tickery
COMMODITY_TICKERS = {
    "GOLD": "GC=F",           # Złoto (COMEX futures)
    "XAU/USD": "GC=F",        # alias
    "SILVER": "SI=F",         # Srebro (COMEX futures)
    "XAG/USD": "SI=F",        # alias
    "OIL": "CL=F",            # Ropa WTI (NYMEX futures)
    "CRUDE": "CL=F",          # alias
    "NATGAS": "NG=F",         # Gaz ziemny (NYMEX futures)
    "COPPER": "HG=F",         # Miedź (COMEX futures)
    # ETF alternatives (dłuższa historia, lepsza płynność)
    "GOLD_ETF": "GLD",        # SPDR Gold Shares ETF
    "SILVER_ETF": "SLV",      # iShares Silver Trust ETF
}

# Typ rynku per symbol (do wyświetlania i formatowania)
MARKET_TYPE_MAP = {
    # Indeksy
    "SP500": "INDEX", "^GSPC": "INDEX", "US100": "INDEX", "NASDAQ": "INDEX",
    "DOW": "INDEX", "DAX": "INDEX", "FTSE": "INDEX", "CAC40": "INDEX",
    "NIKKEI": "INDEX", "NIKKEI225": "INDEX", "HSI": "INDEX",
    "WIG": "INDEX", "WIG20": "INDEX", "POLAND": "INDEX",
    # Forex
    "EUR/USD": "FOREX", "GBP/USD": "FOREX", "USD/JPY": "FOREX",
    "USD/CHF": "FOREX", "AUD/USD": "FOREX", "USD/CAD": "FOREX",
    "NZD/USD": "FOREX", "EUR/GBP": "FOREX", "EUR/PLN": "FOREX",
    "USD/PLN": "FOREX", "EUR/JPY": "FOREX", "GBP/JPY": "FOREX",
    # Towary
    "GOLD": "COMMODITY", "XAU/USD": "COMMODITY", "SILVER": "COMMODITY",
    "XAG/USD": "COMMODITY", "OIL": "COMMODITY", "CRUDE": "COMMODITY",
    "NATGAS": "COMMODITY", "COPPER": "COMMODITY",
    "GOLD_ETF": "COMMODITY", "SILVER_ETF": "COMMODITY",
}

# Domyślne timeframe'y per typ rynku (tradycyjne rynki = wyższe TF)
MARKET_DEFAULT_TIMEFRAMES = {
    "CRYPTO": ["5m", "15m", "1h", "4h"],
    "INDEX": ["1h", "4h", "1d"],
    "FOREX": ["1h", "4h", "1d"],
    "COMMODITY": ["1h", "4h", "1d"],
}

# Timeframe overrides per asset class (used by bot.py to filter valid TF/symbol combos)
ASSET_TF_OVERRIDES = {
    "INDEX": ["15m", "1h", "4h", "1d"],     # Indeksy: min 15m (YFinance limit)
    "FOREX": ["15m", "1h", "4h", "1d"],      # Forex: min 15m
    "COMMODITY": ["15m", "1h", "4h", "1d"],   # Towary: min 15m
    # CRYPTO: all TFs allowed (not in dict = no restriction)
    # 5m is only valid for crypto via Binance/ccxt
}


def get_asset_class(symbol: str) -> str:
    """Zwróć klasę assetu (CRYPTO, INDEX, FOREX, COMMODITY, STOCK).
    
    Alias dla YFinanceDataFetcher.get_market_type().
    Używane przez bot.py do filtrowania timeframe'ów.
    Zwraca UPPERCASE — spójne z kluczami ASSET_TF_OVERRIDES.
    """
    market_type = YFinanceDataFetcher.get_market_type(symbol)
    return market_type.upper()

# Formatowanie ceny per typ rynku
PRICE_FORMAT = {
    "CRYPTO": ",.2f",
    "INDEX": ",.2f",
    "FOREX": ",.5f",
    "COMMODITY": ",.2f",
}

# Emoji per typ rynku
MARKET_EMOJI = {
    "CRYPTO": "\u20bf",    # ₿
    "INDEX": "\U0001f4c8",  # 📈
    "FOREX": "\U0001f4b1",  # 💱
    "COMMODITY": "\U0001f3c6",  # 🏆
}

# Pełne nazwy instrumentów
INSTRUMENT_NAMES = {
    "SP500": "S&P 500",
    "NASDAQ": "NASDAQ 100",
    "DAX": "DAX 40",
    "NIKKEI": "Nikkei 225",
    "WIG": "WIG (via EPOL)",
    "FTSE": "FTSE 100",
    "CAC40": "CAC 40",
    "DOW": "Dow Jones",
    "HSI": "Hang Seng",
    "EUR/USD": "EUR/USD",
    "GBP/USD": "GBP/USD",
    "USD/JPY": "USD/JPY",
    "USD/PLN": "USD/PLN",
    "EUR/PLN": "EUR/PLN",
    "GOLD": "Gold (XAU/USD)",
    "SILVER": "Silver (XAG/USD)",
    "OIL": "Crude Oil WTI",
    "NATGAS": "Natural Gas",
    "COPPER": "Copper",
}

# Mapowanie timeframe → Yahoo Finance interval
TF_TO_YF = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "1d": "1d",
    "1w": "1wk",
    "1M": "1mo",
}

# Max historii per timeframe (YFinance limits)
TF_MAX_PERIOD = {
    "1m": "7d",
    "5m": "60d",
    "15m": "60d",
    "30m": "60d",
    "1h": "730d",
    "2h": "730d",
    "4h": "730d",
    "1d": "max",
    "1w": "max",
    "1M": "max",
}


class YFinanceDataFetcher:
    """
    Pobiera dane OHLCV z Yahoo Finance.
    
    Zalety vs ccxt:
    - Darmowe, bez API key
    - Indeksy giełdowe (SP500, NASDAQ, DAX, Nikkei)
    - Forex (EUR/USD, GBP/USD)
    - Towary (złoto, srebro, ropa)
    - Akcje (AAPL, TSLA)
    - Krypto też działa (BTC-USD)
    
    Wady:
    - Mniej danych intraday (max 60 dni na 15m)
    - Brak danych tick-level
    - Ograniczenia rate limit (2000 req/h)
    """
    
    def __init__(
        self,
        cache_ttl_seconds: int = 60,
    ):
        self.cache_ttl = cache_ttl_seconds
        self._cache: OrderedDict = OrderedDict()
        self._request_count = 0
        self._yf = None
    
    def _get_yfinance(self):
        """Lazy init yfinance."""
        if self._yf is not None:
            return self._yf
        
        try:
            import yfinance
            self._yf = yfinance
            return self._yf
        except ImportError:
            raise RuntimeError(
                "yfinance nie jest zainstalowany. Uruchom: pip install yfinance"
            )
    
    def _resolve_ticker(self, symbol: str) -> str:
        """Przekonwertuj symbol na Yahoo Finance ticker."""
        sym_upper = symbol.upper()
        
        # Sprawdź indeksy
        if sym_upper in INDEX_TICKERS:
            return INDEX_TICKERS[sym_upper]
        
        # Sprawdź forex
        if sym_upper in FOREX_TICKERS:
            return FOREX_TICKERS[sym_upper]
        
        # Sprawdź towary
        if sym_upper in COMMODITY_TICKERS:
            return COMMODITY_TICKERS[sym_upper]
        
        # Sprawdź krypto
        if symbol in CRYPTO_TO_YF:
            return CRYPTO_TO_YF[symbol]
        
        # Sprawdź czy to już ticker YF (zawiera - lub . lub =)
        if "-" in symbol or "." in symbol or "=" in symbol:
            return symbol
        
        # Default: traktuj jako stock ticker
        return symbol
    
    @staticmethod
    def get_market_type(symbol: str) -> str:
        """Zwróć typ rynku dla symbolu (CRYPTO, INDEX, FOREX, COMMODITY, STOCK)."""
        sym_upper = symbol.upper()
        
        # Sprawdź mapę
        if sym_upper in MARKET_TYPE_MAP:
            return MARKET_TYPE_MAP[sym_upper]
        
        # Krypto patterns
        for pattern in ["/USDT", "/BTC", "/ETH", "/BNB"]:
            if pattern in sym_upper and "=" not in symbol:
                return "CRYPTO"
        # /USD pattern but not forex and not commodity
        if "/USD" in sym_upper and sym_upper not in FOREX_TICKERS and sym_upper not in ("XAU/USD", "XAG/USD"):
            return "CRYPTO"
        
        # Futures surowcowe (GC=F, SI=F, CL=F…) — przed forex, bo też zawierają "="
        if sym_upper in COMMODITY_TICKERS.values():
            return "COMMODITY"

        # Forex patterns
        if "=" in symbol or sym_upper in FOREX_TICKERS:
            return "FOREX"
        
        # Index patterns
        if sym_upper.startswith("^") or sym_upper in INDEX_TICKERS:
            return "INDEX"
        
        # Commodity patterns
        if sym_upper in COMMODITY_TICKERS:
            return "COMMODITY"
        
        return "STOCK"
    
    @staticmethod
    def get_instrument_name(symbol: str) -> str:
        """Zwróć pełną nazwę instrumentu."""
        sym_upper = symbol.upper()
        return INSTRUMENT_NAMES.get(sym_upper, symbol)
    
    @staticmethod
    def format_price(price: float, symbol: str) -> str:
        """Formatuj cenę z odpowiednią precyzją dla typu rynku."""
        market_type = YFinanceDataFetcher.get_market_type(symbol)
        fmt = PRICE_FORMAT.get(market_type, ",.2f")
        return f"${price:{fmt}}"
    
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        period: str = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        Pobierz dane OHLCV z Yahoo Finance.
        
        Args:
            symbol: Ticker (np. "SPY", "BTC/USDT", "EUR/USD", "GOLD") lub alias ("SP500", "DAX")
            timeframe: Interwał ("1m", "5m", "15m", "1h", "4h", "1d")
            period: Okres danych (np. "1mo", "6mo", "1y", "max"). Jeśli None, auto-detect.
            force_refresh: Wymuś odświeżenie cache.
        
        Returns:
            DataFrame z kolumnami [open, high, low, close, volume] i DatetimeIndex
        """
        yf_ticker = self._resolve_ticker(symbol)
        yf_interval = TF_TO_YF.get(timeframe, "1h")
        
        cache_key = f"{yf_ticker}_{yf_interval}"
        
        # Check cache
        if not force_refresh and cache_key in self._cache:
            cached_df, cached_time = self._cache[cache_key]
            age = time.time() - cached_time
            if age < self.cache_ttl:
                return cached_df
        
        yf = self._get_yfinance()
        
        # Determine period
        if period is None:
            period = TF_MAX_PERIOD.get(timeframe, "1y")
        
        try:
            ticker = yf.Ticker(yf_ticker)
            df = ticker.history(period=period, interval=yf_interval)
            
            if df.empty:
                print(f"[YFinance] Brak danych dla {yf_ticker} {yf_interval}")
                if cache_key in self._cache:
                    return self._cache[cache_key][0]
                return pd.DataFrame()
            
            # Standaryzuj kolumny (YF używa Title Case)
            df.columns = [c.lower().replace(' ', '_') for c in df.columns]
            
            # Wybierz tylko OHLCV
            keep_cols = []
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col in df.columns:
                    keep_cols.append(col)
            df = df[keep_cols]
            
            # Konwertuj timezone na UTC
            if df.index.tz is not None:
                df.index = df.index.tz_convert('UTC')
            else:
                df.index = df.index.tz_localize('UTC')
            
            # Usuń duplikaty
            df = df[~df.index.duplicated(keep='first')]
            df = df.sort_index()
            
            # Cast to float
            for col in df.columns:
                df[col] = df[col].astype(float)
            
            # Cache
            self._cache[cache_key] = (df, time.time())
            self._request_count += 1
            
            # Limit cache size
            if len(self._cache) > 50:
                self._cache.popitem(last=False)
            
            return df
            
        except Exception as e:
            print(f"[YFinance] Błąd pobierania {yf_ticker} {yf_interval}: {e}")
            if cache_key in self._cache:
                return self._cache[cache_key][0]
            raise
    
    def fetch_extended(
        self,
        symbol: str,
        timeframe: str,
        total_bars: int = 1500,
    ) -> pd.DataFrame:
        """
        Pobierz więcej barów niż default.
        
        Dla 1d/1w: używa period="max"
        Dla 1h/4h: używa period="2y" (dostarcza ~1500+ barów na 1h)
        Dla <1h: limited do 60 dni przez YFinance
        """
        # Map total_bars to period
        if timeframe in ("1d", "1w", "1M"):
            period = "max"
        elif timeframe in ("1h", "2h", "4h"):
            # 1h * 1500 bars = ~62 days, YFinance allows 730d
            period = "2y"
        else:
            # Intraday: YFinance limits to 60d
            period = "60d"
        
        return self.fetch_ohlcv(symbol, timeframe, period=period, force_refresh=True)
    
    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Pobierz aktualną cenę (last close z najnowszej świecy)."""
        # Spróbuj z cache (szybko)
        yf_ticker = self._resolve_ticker(symbol)
        for interval in ["1m", "5m", "15m", "1h"]:
            cache_key = f"{yf_ticker}_{TF_TO_YF.get(interval, interval)}"
            if cache_key in self._cache:
                df, _ = self._cache[cache_key]
                if not df.empty:
                    return float(df['close'].iloc[-1])

        # Fallback: pobierz 1m dane
        try:
            df = self.fetch_ohlcv(symbol, "1m", period="1d")
            if not df.empty:
                return float(df['close'].iloc[-1])
        except Exception:
            pass

        # Ostateczny fallback: 1h dane
        try:
            df = self.fetch_ohlcv(symbol, "1h", period="5d")
            if not df.empty:
                return float(df['close'].iloc[-1])
        except Exception:
            pass

        return None

    @property
    def stats(self) -> dict:
        return {
            "source": "yfinance",
            "requests_made": self._request_count,
            "cache_size": len(self._cache),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# UNIFIED DATA FETCHER — auto-wybiera Binance lub YFinance
# ═══════════════════════════════════════════════════════════════════════════════

class UnifiedDataFetcher:
    """
    Automatycznie wybiera źródło danych:
    - Krypto (BTC/USDT, ETH/USDT) → Binance (ccxt)
    - Indeksy/Forex/Towary/Akcje → Yahoo Finance
    
    Jeden interfejs, dwa backendy.
    
    v4: pełne wsparcie dla forex (EUR/USD), towarów (Gold, Silver),
        indeksów (S&P500, DAX, Nikkei, WIG).
    """
    
    # Krypto symbols (use Binance)
    CRYPTO_PATTERNS = ["/USDT", "/BTC", "/ETH", "/BNB", "/BUSD"]
    
    def __init__(
        self,
        binance_cache_ttl: int = 30,
        yf_cache_ttl: int = 60,
    ):
        self._binance = None
        self._yfinance = YFinanceDataFetcher(cache_ttl_seconds=yf_cache_ttl)
        self._yf_cache_ttl = yf_cache_ttl
        self._binance_cache_ttl = binance_cache_ttl
    
    def _get_binance(self):
        """Lazy init Binance fetcher."""
        if self._binance is None:
            from fetchers.binance import DataFetcher
            self._binance = DataFetcher(
                exchange_id='binance',
                candles_per_fetch=500,
                cache_ttl_seconds=self._binance_cache_ttl,
            )
        return self._binance
    
    def _is_crypto(self, symbol: str) -> bool:
        """Czy to krypto (dla Binance)? Jeśli nie, użyj YFinance."""
        sym_upper = symbol.upper()
        
        # Known non-crypto categories — check FIRST
        if sym_upper in INDEX_TICKERS:
            return False
        if sym_upper in FOREX_TICKERS:
            return False
        if sym_upper in COMMODITY_TICKERS:
            return False
        
        # Forex pattern: contains = (EURUSD=X)
        if "=" in symbol:
            return False
        
        # YF-style ticker (contains ^ for indices like ^GDAXI)
        if "^" in symbol:
            return False
        
        # YF-style ticker (contains - like BTC-USD, but that's in CRYPTO_TO_YF)
        if "-" in symbol and symbol not in CRYPTO_TO_YF:
            return False
        
        # Known crypto patterns
        for pattern in self.CRYPTO_PATTERNS:
            if pattern in sym_upper:
                return True
        
        # /USD pattern — could be crypto or commodity
        if "/USD" in sym_upper:
            # XAU/USD, XAG/USD are commodities
            if sym_upper in ("XAU/USD", "XAG/USD"):
                return False
            # Other /USD is crypto (BTC/USD etc, though Binance prefers /USDT)
            return True
        
        # Default: not crypto → YFinance
        return False
    
    def get_market_type(self, symbol: str) -> str:
        """Zwróć typ rynku dla symbolu."""
        return YFinanceDataFetcher.get_market_type(symbol)
    
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Pobierz dane — auto-wybierz źródło."""
        if self._is_crypto(symbol):
            fetcher = self._get_binance()
        else:
            fetcher = self._yfinance
        
        return fetcher.fetch_ohlcv(symbol, timeframe, force_refresh=force_refresh)

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Pobierz aktualną cenę — deleguj do odpowiedniego backendu."""
        if self._is_crypto(symbol):
            fetcher = self._get_binance()
            if hasattr(fetcher, 'get_latest_price'):
                return fetcher.get_latest_price(symbol)
            return None
        else:
            return self._yfinance.get_latest_price(symbol)

    @property
    def stats(self) -> dict:
        result = {"mode": "unified"}
        if self._binance:
            result["binance"] = self._binance.stats
        result["yfinance"] = self._yfinance.stats
        return result
