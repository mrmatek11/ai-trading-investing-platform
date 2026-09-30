"""
GLM AI Analyst Module — Zhipu AI ChatGLM Integration
═══════════════════════════════════════════════════════════════════════════

5 funkcji AI:

1. Signal Quality Scorer — AI ocenia KAŻDY sygnał (score 1-10, TAKE/WATCH/SKIP)
2. Daily Market Briefing — AI raport rynkowy co 6h
3. Market Regime Detector — AI klasyfikuje rynek per para (cache 15 min)
4. Multi-TF Confluence — AI ocenia konfluencję wszystkich TF
5. End-of-Day Summary — podsumowanie dnia

Używa: Zhipu AI ChatGLM API (https://open.bigmodel.cn)
Instalacja: pip install requests pyjwt
"""

import os
import time
import json
import logging
import hashlib
import random
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field
from collections import OrderedDict

import requests

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# GLM API CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

class GLMClient:
    """
    Klient API Zhipu AI ChatGLM — z exponential backoff i masked logging.
    
    POPRAWKI vs oryginał:
      - Exponential backoff na 429 (zamiast sleep(5) + return None)
      - API key masked w logach (tylko pierwsze 8 znaków)
      - Max retries configurable
      - Jitter żeby uniknąć thundering herd przy wielu symbolach
    """
    
    API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    
    def __init__(self, api_key: str, model: str = "glm-4-flash-250414"):
        """
        Args:
            api_key: Zhipu AI API key (format: <id>.<secret>)
            model: Model name (glm-4-flash-250414, glm-4, glm-4-plus)
        """
        self.api_key = api_key
        self.model = model
        self._token = None
        self._token_expires = 0
        self._request_count = 0
        self._last_request_time = 0
        self._errors = 0
        
        # Masked key dla logów — nigdy nie loguj pełnego klucza
        self._masked_key = f"{api_key[:8]}..." if len(api_key) > 8 else "***"
    
    def _generate_token(self) -> str:
        """Generate JWT token from API key."""
        try:
            import jwt
            api_key_parts = self.api_key.split('.')
            if len(api_key_parts) != 2:
                return self.api_key
            
            api_id, api_secret = api_key_parts
            now = int(time.time())
            payload = {
                "api_key": api_id,
                "exp": now + 3600,
                "timestamp": now,
            }
            token = jwt.encode(
                payload, api_secret, algorithm="HS256",
                headers={"alg": "HS256", "sign_type": "SIGN"},
            )
            self._token = token
            self._token_expires = now + 3500
            return token
        except ImportError:
            logger.warning("[GLM] pyjwt nie zainstalowany. pip install pyjwt")
            return self.api_key
        except Exception as e:
            logger.debug(f"[GLM] JWT error: {e}")
            return self.api_key
    
    def _get_token(self) -> str:
        """Get or refresh JWT token."""
        if self._token and time.time() < self._token_expires:
            return self._token
        return self._generate_token()
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1000,
        timeout: int = 30,
        max_retries: int = 3,
    ) -> Optional[str]:
        """
        Send chat completion z exponential backoff na rate limiting.
        
        FIX vs oryginał:
          - max_retries zamiast jednego try
          - Exponential backoff: 5s -> 15s -> 45s z ±20% jitter
          - API key nigdy nie pojawia się w logach
        """
        # Min 1s między requestami (rate limit prewencja)
        elapsed = time.time() - self._last_request_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        
        token = self._get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    self.API_URL,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )
                
                self._request_count += 1
                self._last_request_time = time.time()
                
                if response.status_code == 200:
                    data = response.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    return content.strip()
                
                elif response.status_code == 429:
                    # Exponential backoff: 5, 15, 45 sekund + jitter
                    wait = (5 ** (attempt + 1)) * (0.8 + random.random() * 0.4)
                    logger.warning(
                        f"[GLM] Rate limited (429), attempt {attempt+1}/{max_retries}, "
                        f"waiting {wait:.1f}s — key: {self._masked_key}"
                    )
                    if attempt < max_retries - 1:
                        time.sleep(wait)
                        continue
                    return None
                
                elif response.status_code == 401:
                    logger.error(f"[GLM] Auth failed (401) — sprawdź klucz API: {self._masked_key}")
                    return None
                
                elif response.status_code in (500, 502, 503):
                    wait = 10 * (attempt + 1)
                    logger.warning(f"[GLM] Server error {response.status_code}, retry {attempt+1}/{max_retries} in {wait}s")
                    time.sleep(wait)
                    continue
                
                else:
                    logger.warning(f"[GLM] API error {response.status_code}: {response.text[:100]}")
                    self._errors += 1
                    return None
                    
            except requests.exceptions.Timeout:
                logger.warning(f"[GLM] Timeout (attempt {attempt+1}/{max_retries})")
                self._errors += 1
                if attempt < max_retries - 1:
                    time.sleep(5)
                    continue
                return None
            
            except Exception as e:
                logger.warning(f"[GLM] Request error: {type(e).__name__}: {str(e)[:100]}")
                self._errors += 1
                return None
        
        return None
    
    @property
    def stats(self) -> dict:
        return {
            "model": self.model,
            "requests": self._request_count,
            "errors": self._errors,
            "has_key": bool(self.api_key),
            # NIGDY nie dodawaj api_key do stats!
        }


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SignalScore:
    """AI assessment of a signal quality."""
    score: int               # 1-10
    recommendation: str      # TAKE, WATCH, SKIP
    analysis: str            # Short analysis (1-2 sentences)
    key_factors: List[str]   # Key positive factors
    risks: List[str]         # Risk factors
    raw_response: str = ""


@dataclass
class MarketRegime:
    """AI market regime classification."""
    regime: str              # trending_up, trending_down, ranging, volatile, quiet
    strength: float          # 0.0-1.0
    confidence: float        # 0.0-1.0
    bias: str                # bullish, bearish, neutral
    summary: str             # Short description
    timestamp: float = 0.0


@dataclass
class DailyBriefing:
    """AI daily market briefing."""
    overall_bias: str        # bullish, bearish, neutral, mixed
    key_pairs: List[Dict]    # [{symbol, reason}]
    risk_events: List[str]   # Things to watch out for
    watchlist: List[str]     # Pairs to watch
    summary: str             # 2-3 sentence overview
    timestamp: float = 0.0


@dataclass
class MultiTFConfluence:
    """AI multi-timeframe confluence analysis."""
    score: int               # 1-10
    direction: str           # bullish, bearish, neutral, mixed
    strongest_tf: str        # Timeframe with strongest signal
    analysis: str            # Short analysis
    details: Dict[str, str]  # Per-TF summary
    timestamp: float = 0.0


@dataclass
class EndOfDaySummary:
    """AI end-of-day summary."""
    total_signals: int
    signals_taken: int
    signals_watched: int
    signals_skipped: int
    best_signal: str
    worst_signal: str
    lessons: List[str]
    outlook: str             # Tomorrow's outlook
    summary: str


# ═══════════════════════════════════════════════════════════════════════════════
# GLM AI ANALYST — MAIN CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class GLMAnalyst:
    """
    AI Analyst powered by Zhipu ChatGLM.
    
    Features:
      1. Signal Quality Scorer — evaluates every signal
      2. Daily Market Briefing — market report every 6h
      3. Market Regime Detector — classifies market state per pair
      4. Multi-TF Confluence — cross-timeframe analysis
      5. End-of-Day Summary — daily performance report
    """
    
    def __init__(
        self,
        api_key: str = "",
        model: str = "glm-4-flash-250414",
        enabled: bool = True,
        language: str = "pl",   # pl, en
        # Feature toggles (can disable individual features)
        signal_scorer: bool = True,
        daily_briefing: bool = True,
        regime_detector: bool = True,
        multi_tf_confluence: bool = True,
        eod_summary: bool = True,
    ):
        self.enabled = enabled and bool(api_key)
        self.language = language
        
        # Feature toggles
        self.signal_scorer_enabled = signal_scorer
        self.daily_briefing_enabled = daily_briefing
        self.regime_detector_enabled = regime_detector
        self.multi_tf_confluence_enabled = multi_tf_confluence
        self.eod_summary_enabled = eod_summary
        
        if self.enabled:
            self.client = GLMClient(api_key=api_key, model=model)
            logger.info(f"GLM AI Analyst: ENABLED (model={model}, lang={language})")
        else:
            self.client = None
            logger.info("GLM AI Analyst: DISABLED (no API key)")
        
        # Cache for Market Regime (15 min TTL)
        self._regime_cache: OrderedDict = OrderedDict()
        self._regime_cache_ttl = 900  # 15 min
        
        # Briefing state (every 6h)
        self._last_briefing_time = 0
        self._briefing_interval = 21600  # 6h
        
        # EOD state
        self._last_eod_date = ""
        self._daily_signals: List[Dict] = []
        
        # Signal tracking for EOD
        self._signal_history: List[Dict] = []
    
    def _sys_prompt(self) -> str:
        """System prompt for trading analyst."""
        if self.language == "pl":
            return (
                "Jestes profesjonalnym analitykiem tradingowym. "
                "Oceniasz sygnaly tradingowe na podstawie danych technicznych (Stochastic, NWO, CVD, trend, ATR, SL/TP). "
                "Odpowiadasz zwiezle i konkretnie w jezyku polskim. "
                "Uzywasz formatu JSON gdy prosze o strukturyzowane dane."
            )
        return (
            "You are a professional trading analyst. "
            "You evaluate trading signals based on technical data (Stochastic, NWO, CVD, trend, ATR, SL/TP). "
            "You respond concisely and specifically. "
            "You use JSON format when asked for structured data."
        )
    
    # ═════════════════════════════════════════════════════════════════════
    # 1. SIGNAL QUALITY SCORER
    # ═════════════════════════════════════════════════════════════════════
    
    def score_signal(self, signal_data: Dict) -> Optional[SignalScore]:
        """
        AI evaluates a trading signal.
        
        Args:
            signal_data: Dict with signal info:
                symbol, direction, price, stoch_k, stoch_d, 
                nwo_osc, nwo_histogram, cvd, trend, atr, sl, tp,
                source, confidence, against_trend
        
        Returns:
            SignalScore or None on error
        """
        if not self.enabled or not self.signal_scorer_enabled:
            return None
        
        direction = signal_data.get("direction", "LONG")
        against_trend = signal_data.get("against_trend", False)
        source = signal_data.get("source", "STOCH_ONLY")
        confidence = signal_data.get("confidence", "LOW")
        
        if self.language == "pl":
            user_msg = (
                f"Ocen sygnal tradingowy:\n"
                f"Para: {signal_data.get('symbol', '?')}\n"
                f"Kierunek: {direction}\n"
                f"Cena: {signal_data.get('price', 0):.2f}\n"
                f"Stochastic K={signal_data.get('stoch_k', 0):.1f} D={signal_data.get('stoch_d', 0):.1f}\n"
                f"NWO Osc={signal_data.get('nwo_osc', 0):.1f} Histogram={signal_data.get('nwo_histogram', 0):.4f}\n"
                f"CVD={signal_data.get('cvd', 0):.2f}\n"
                f"Trend: {signal_data.get('trend', '?')}\n"
                f"ATR={signal_data.get('atr', 0):.2f}\n"
                f"SL={signal_data.get('sl', 0):.2f} TP={signal_data.get('tp', 0):.2f}\n"
                f"Zrodlo: {source} | Pewnosc: {confidence}\n"
                f"Przeciw trendowi: {'TAK' if against_trend else 'NIE'}\n\n"
                f"Odpowiedz TYLKO w formacie JSON:\n"
                f'{{"score": 1-10, "recommendation": "TAKE"/"WATCH"/"SKIP", '
                f'"analysis": "krotka analiza 1-2 zdania", '
                f'"key_factors": ["factor1", "factor2"], '
                f'"risks": ["risk1", "risk2"]}}'
            )
        else:
            user_msg = (
                f"Evaluate this trading signal:\n"
                f"Pair: {signal_data.get('symbol', '?')}\n"
                f"Direction: {direction}\n"
                f"Price: {signal_data.get('price', 0):.2f}\n"
                f"Stochastic K={signal_data.get('stoch_k', 0):.1f} D={signal_data.get('stoch_d', 0):.1f}\n"
                f"NWO Osc={signal_data.get('nwo_osc', 0):.1f} Histogram={signal_data.get('nwo_histogram', 0):.4f}\n"
                f"CVD={signal_data.get('cvd', 0):.2f}\n"
                f"Trend: {signal_data.get('trend', '?')}\n"
                f"ATR={signal_data.get('atr', 0):.2f}\n"
                f"SL={signal_data.get('sl', 0):.2f} TP={signal_data.get('tp', 0):.2f}\n"
                f"Source: {source} | Confidence: {confidence}\n"
                f"Against trend: {'YES' if against_trend else 'NO'}\n\n"
                f"Respond ONLY in JSON format:\n"
                f'{{"score": 1-10, "recommendation": "TAKE"/"WATCH"/"SKIP", '
                f'"analysis": "short analysis 1-2 sentences", '
                f'"key_factors": ["factor1", "factor2"], '
                f'"risks": ["risk1", "risk2"]}}'
            )
        
        messages = [
            {"role": "system", "content": self._sys_prompt()},
            {"role": "user", "content": user_msg},
        ]
        
        response = self.client.chat(messages, temperature=0.2, max_tokens=500)
        if not response:
            return self._fallback_score(signal_data)
        
        try:
            # Try to parse JSON from response
            json_str = response
            if "{" in response:
                start = response.index("{")
                end = response.rindex("}") + 1
                json_str = response[start:end]
            
            data = json.loads(json_str)
            
            score = int(data.get("score", 5))
            score = max(1, min(10, score))
            
            rec = data.get("recommendation", "WATCH").upper()
            if rec not in ("TAKE", "WATCH", "SKIP"):
                # Infer from score
                if score >= 7:
                    rec = "TAKE"
                elif score >= 4:
                    rec = "WATCH"
                else:
                    rec = "SKIP"
            
            result = SignalScore(
                score=score,
                recommendation=rec,
                analysis=data.get("analysis", ""),
                key_factors=data.get("key_factors", []),
                risks=data.get("risks", []),
                raw_response=response,
            )
            
            # Track for EOD
            self._track_signal(signal_data, result)
            
            return result
            
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug(f"[GLM] Failed to parse signal score: {e}")
            return self._fallback_score(signal_data)
    
    def _fallback_score(self, signal_data: Dict) -> SignalScore:
        """Rule-based fallback when GLM API fails."""
        score = 5
        risks = []
        factors = []
        
        # Source quality
        source = signal_data.get("source", "STOCH_ONLY")
        if source == "CONFLUENCE":
            score += 3
            factors.append("Full confluence (Stoch+NWO+CVD)")
        elif source in ("STOCH+NWO", "STOCH STRICT+NWO"):
            score += 2
            factors.append("Stoch+NWO alignment")
        else:
            risks.append("Stochastic only - no NWO/CVD confirmation")
        
        # Against trend
        if signal_data.get("against_trend", False):
            score -= 3
            risks.append("Signal against trend")
        else:
            factors.append("With trend")
        
        # CVD
        cvd = signal_data.get("cvd", 0)
        direction = signal_data.get("direction", "LONG")
        if direction == "LONG" and cvd > 1.0:
            score += 1
            factors.append(f"CVD bullish ({cvd:.2f})")
        elif direction == "SHORT" and cvd < -1.0:
            score += 1
            factors.append(f"CVD bearish ({cvd:.2f})")
        elif (direction == "LONG" and cvd < -0.5) or (direction == "SHORT" and cvd > 0.5):
            score -= 1
            risks.append(f"CVD opposing ({cvd:.2f})")
        
        score = max(1, min(10, score))
        
        if score >= 7:
            rec = "TAKE"
        elif score >= 4:
            rec = "WATCH"
        else:
            rec = "SKIP"
        
        return SignalScore(
            score=score,
            recommendation=rec,
            analysis=f"Rule-based score: {rec} ({score}/10)",
            key_factors=factors,
            risks=risks,
        )
    
    def _track_signal(self, signal_data: Dict, score: SignalScore):
        """Track signal for End-of-Day summary."""
        self._signal_history.append({
            "symbol": signal_data.get("symbol", "?"),
            "direction": signal_data.get("direction", "?"),
            "source": signal_data.get("source", "?"),
            "score": score.score,
            "recommendation": score.recommendation,
            "timestamp": time.time(),
        })
    
    # ═════════════════════════════════════════════════════════════════════
    # 2. DAILY MARKET BRIEFING
    # ═════════════════════════════════════════════════════════════════════
    
    def should_send_briefing(self) -> bool:
        """Check if it's time for a market briefing."""
        if not self.enabled or not self.daily_briefing_enabled:
            return False
        now = time.time()
        if now - self._last_briefing_time >= self._briefing_interval:
            return True
        return False
    
    def generate_briefing(self, market_data: Dict) -> Optional[DailyBriefing]:
        """
        Generate AI market briefing.
        
        Args:
            market_data: Dict with per-symbol data:
                {symbol: {price, change_24h, stoch_k, nwo_osc, cvd, trend, regime}}
        
        Returns:
            DailyBriefing or None
        """
        if not self.enabled or not self.daily_briefing_enabled:
            return None
        
        # Build market summary
        lines = []
        for symbol, data in market_data.items():
            change = data.get("change_24h", 0)
            trend = data.get("trend", "?")
            stoch_k = data.get("stoch_k", 0)
            nwo = data.get("nwo_osc", 0)
            cvd = data.get("cvd", 0)
            regime = data.get("regime", "?")
            lines.append(
                f"- {symbol}: ${data.get('price', 0):,.2f} ({change:+.2f}%) "
                f"StochK={stoch_k:.1f} NWO={nwo:.1f} CVD={cvd:.2f} "
                f"Trend={trend} Regime={regime}"
            )
        
        market_summary = "\n".join(lines[:20])  # Limit to 20 symbols
        
        if self.language == "pl":
            user_msg = (
                f"Wygeneruj krotki raport rynkowy na podstawie danych:\n\n"
                f"{market_summary}\n\n"
                f"Odpowiedz TYLKO w formacie JSON:\n"
                f'{{"overall_bias": "bullish/bearish/neutral/mixed", '
                f'"key_pairs": [{{"symbol": "XXX", "reason": "dlaczego"}}], '
                f'"risk_events": ["ryzyko1", "ryzyko2"], '
                f'"watchlist": ["SYMBOL1", "SYMBOL2"], '
                f'"summary": "2-3 zdania przegladu"}}'
            )
        else:
            user_msg = (
                f"Generate a brief market report based on data:\n\n"
                f"{market_summary}\n\n"
                f"Respond ONLY in JSON format:\n"
                f'{{"overall_bias": "bullish/bearish/neutral/mixed", '
                f'"key_pairs": [{{"symbol": "XXX", "reason": "why"}}], '
                f'"risk_events": ["risk1", "risk2"], '
                f'"watchlist": ["SYMBOL1", "SYMBOL2"], '
                f'"summary": "2-3 sentence overview"}}'
            )
        
        messages = [
            {"role": "system", "content": self._sys_prompt()},
            {"role": "user", "content": user_msg},
        ]
        
        response = self.client.chat(messages, temperature=0.4, max_tokens=800)
        if not response:
            return None
        
        try:
            json_str = response
            if "{" in response:
                start = response.index("{")
                end = response.rindex("}") + 1
                json_str = response[start:end]
            
            data = json.loads(json_str)
            
            briefing = DailyBriefing(
                overall_bias=data.get("overall_bias", "neutral"),
                key_pairs=data.get("key_pairs", []),
                risk_events=data.get("risk_events", []),
                watchlist=data.get("watchlist", []),
                summary=data.get("summary", ""),
                timestamp=time.time(),
            )
            
            self._last_briefing_time = time.time()
            return briefing
            
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug(f"[GLM] Failed to parse briefing: {e}")
            return None
    
    # ═════════════════════════════════════════════════════════════════════
    # 3. MARKET REGIME DETECTOR
    # ═════════════════════════════════════════════════════════════════════
    
    def detect_regime(self, symbol: str, market_data: Dict) -> Optional[MarketRegime]:
        """
        Classify market regime for a symbol.
        Results are cached for 15 minutes.
        
        Args:
            symbol: Trading pair
            market_data: {price, stoch_k, stoch_d, nwo_osc, nwo_histogram, cvd, 
                          atr, trend, volume_ratio, price_range_pct}
        
        Returns:
            MarketRegime or None
        """
        if not self.enabled or not self.regime_detector_enabled:
            return None
        
        # Check cache
        cache_key = f"regime_{symbol}"
        if cache_key in self._regime_cache:
            cached_data, cached_time = self._regime_cache[cache_key]
            if time.time() - cached_time < self._regime_cache_ttl:
                return cached_data
        
        if self.language == "pl":
            user_msg = (
                f"Klasyfikuj stan rynku dla {symbol}:\n"
                f"Cena: {market_data.get('price', 0):.2f}\n"
                f"Stochastic K={market_data.get('stoch_k', 0):.1f} D={market_data.get('stoch_d', 0):.1f}\n"
                f"NWO Osc={market_data.get('nwo_osc', 0):.1f} Histogram={market_data.get('nwo_histogram', 0):.4f}\n"
                f"CVD={market_data.get('cvd', 0):.2f}\n"
                f"ATR={market_data.get('atr', 0):.2f}\n"
                f"Trend: {market_data.get('trend', '?')}\n"
                f"Zmiana 24h: {market_data.get('change_24h', 0):.2f}%\n"
                f"Zmiennosc: {market_data.get('volatility_pct', 0):.2f}%\n\n"
                f"Odpowiedz TYLKO w formacie JSON:\n"
                f'{{"regime": "trending_up/trending_down/ranging/volatile/quiet", '
                f'"strength": 0.0-1.0, "confidence": 0.0-1.0, '
                f'"bias": "bullish/bearish/neutral", '
                f'"summary": "krotki opis"}}'
            )
        else:
            user_msg = (
                f"Classify market regime for {symbol}:\n"
                f"Price: {market_data.get('price', 0):.2f}\n"
                f"Stochastic K={market_data.get('stoch_k', 0):.1f} D={market_data.get('stoch_d', 0):.1f}\n"
                f"NWO Osc={market_data.get('nwo_osc', 0):.1f} Histogram={market_data.get('nwo_histogram', 0):.4f}\n"
                f"CVD={market_data.get('cvd', 0):.2f}\n"
                f"ATR={market_data.get('atr', 0):.2f}\n"
                f"Trend: {market_data.get('trend', '?')}\n"
                f"Change 24h: {market_data.get('change_24h', 0):.2f}%\n"
                f"Volatility: {market_data.get('volatility_pct', 0):.2f}%\n\n"
                f"Respond ONLY in JSON format:\n"
                f'{{"regime": "trending_up/trending_down/ranging/volatile/quiet", '
                f'"strength": 0.0-1.0, "confidence": 0.0-1.0, '
                f'"bias": "bullish/bearish/neutral", '
                f'"summary": "short description"}}'
            )
        
        messages = [
            {"role": "system", "content": self._sys_prompt()},
            {"role": "user", "content": user_msg},
        ]
        
        response = self.client.chat(messages, temperature=0.2, max_tokens=300)
        if not response:
            regime = self._fallback_regime(market_data)
            self._cache_regime(cache_key, regime)
            return regime
        
        try:
            json_str = response
            if "{" in response:
                start = response.index("{")
                end = response.rindex("}") + 1
                json_str = response[start:end]
            
            data = json.loads(json_str)
            
            regime = MarketRegime(
                regime=data.get("regime", "ranging"),
                strength=float(data.get("strength", 0.5)),
                confidence=float(data.get("confidence", 0.5)),
                bias=data.get("bias", "neutral"),
                summary=data.get("summary", ""),
                timestamp=time.time(),
            )
            
            self._cache_regime(cache_key, regime)
            return regime
            
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug(f"[GLM] Failed to parse regime: {e}")
            regime = self._fallback_regime(market_data)
            self._cache_regime(cache_key, regime)
            return regime
    
    def _fallback_regime(self, market_data: Dict) -> MarketRegime:
        """Rule-based regime fallback."""
        trend = market_data.get("trend", "?")
        stoch_k = market_data.get("stoch_k", 50)
        nwo = market_data.get("nwo_osc", 50)
        vol = market_data.get("volatility_pct", 0)
        
        if vol > 5.0:
            regime = "volatile"
            strength = min(vol / 10.0, 1.0)
        elif trend == "UP" and nwo > 60:
            regime = "trending_up"
            strength = min(nwo / 100.0, 1.0)
        elif trend == "DOWN" and nwo < 40:
            regime = "trending_down"
            strength = 1.0 - max(nwo / 100.0, 0.0)
        elif 40 < stoch_k < 60 and 40 < nwo < 60:
            regime = "quiet"
            strength = 0.3
        else:
            regime = "ranging"
            strength = 0.5
        
        bias = "bullish" if trend == "UP" else ("bearish" if trend == "DOWN" else "neutral")
        
        return MarketRegime(
            regime=regime,
            strength=strength,
            confidence=0.6,
            bias=bias,
            summary=f"Rule-based: {regime}",
            timestamp=time.time(),
        )
    
    def _cache_regime(self, key: str, regime: MarketRegime):
        """Cache regime result."""
        self._regime_cache[key] = (regime, time.time())
        if len(self._regime_cache) > 50:
            self._regime_cache.popitem(last=False)
    
    # ═════════════════════════════════════════════════════════════════════
    # 4. MULTI-TF CONFLUENCE
    # ═════════════════════════════════════════════════════════════════════
    
    def analyze_confluence(self, symbol: str, tf_data: Dict[str, Dict]) -> Optional[MultiTFConfluence]:
        """
        Analyze multi-timeframe confluence.
        
        Args:
            symbol: Trading pair
            tf_data: {timeframe: {stoch_k, stoch_d, nwo_osc, nwo_histogram, cvd, trend, price}}
        
        Returns:
            MultiTFConfluence or None
        """
        if not self.enabled or not self.multi_tf_confluence_enabled:
            return None
        
        # Build TF summary
        tf_lines = []
        for tf, data in tf_data.items():
            tf_lines.append(
                f"- {tf}: StochK={data.get('stoch_k', 0):.1f} D={data.get('stoch_d', 0):.1f} "
                f"NWO={data.get('nwo_osc', 0):.1f} Hist={data.get('nwo_histogram', 0):.4f} "
                f"CVD={data.get('cvd', 0):.2f} Trend={data.get('trend', '?')}"
            )
        
        tf_summary = "\n".join(tf_lines)
        
        if self.language == "pl":
            user_msg = (
                f"Analizuj konfluencje multi-TF dla {symbol}:\n\n"
                f"{tf_summary}\n\n"
                f"Odpowiedz TYLKO w formacie JSON:\n"
                f'{{"score": 1-10, "direction": "bullish/bearish/neutral/mixed", '
                f'"strongest_tf": "timeframe", '
                f'"analysis": "krotka analiza", '
                f'"details": {{"5m": "opis", "15m": "opis", "1h": "opis"}}}}'
            )
        else:
            user_msg = (
                f"Analyze multi-TF confluence for {symbol}:\n\n"
                f"{tf_summary}\n\n"
                f"Respond ONLY in JSON format:\n"
                f'{{"score": 1-10, "direction": "bullish/bearish/neutral/mixed", '
                f'"strongest_tf": "timeframe", '
                f'"analysis": "short analysis", '
                f'"details": {{"5m": "desc", "15m": "desc", "1h": "desc"}}}}'
            )
        
        messages = [
            {"role": "system", "content": self._sys_prompt()},
            {"role": "user", "content": user_msg},
        ]
        
        response = self.client.chat(messages, temperature=0.3, max_tokens=500)
        if not response:
            return self._fallback_confluence(tf_data)
        
        try:
            json_str = response
            if "{" in response:
                start = response.index("{")
                end = response.rindex("}") + 1
                json_str = response[start:end]
            
            data = json.loads(json_str)
            
            score = int(data.get("score", 5))
            score = max(1, min(10, score))
            
            return MultiTFConfluence(
                score=score,
                direction=data.get("direction", "neutral"),
                strongest_tf=data.get("strongest_tf", ""),
                analysis=data.get("analysis", ""),
                details=data.get("details", {}),
                timestamp=time.time(),
            )
            
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug(f"[GLM] Failed to parse confluence: {e}")
            return self._fallback_confluence(tf_data)
    
    def _fallback_confluence(self, tf_data: Dict[str, Dict]) -> MultiTFConfluence:
        """Rule-based confluence fallback."""
        bullish_tfs = 0
        bearish_tfs = 0
        strongest_tf = ""
        strongest_score = 0
        
        for tf, data in tf_data.items():
            nwo = data.get("nwo_osc", 50)
            cvd = data.get("cvd", 0)
            
            tf_score = 0
            if nwo < 30:
                tf_score += 1
                bullish_tfs += 1
            elif nwo > 70:
                tf_score -= 1
                bearish_tfs += 1
            
            if cvd > 0.5:
                bullish_tfs += 0.5
            elif cvd < -0.5:
                bearish_tfs += 0.5
            
            if abs(tf_score) > strongest_score:
                strongest_score = abs(tf_score)
                strongest_tf = tf
        
        if bullish_tfs > bearish_tfs + 1:
            direction = "bullish"
            score = min(int(4 + bullish_tfs), 10)
        elif bearish_tfs > bullish_tfs + 1:
            direction = "bearish"
            score = min(int(4 + bearish_tfs), 10)
        else:
            direction = "mixed"
            score = 4
        
        return MultiTFConfluence(
            score=score,
            direction=direction,
            strongest_tf=strongest_tf or (list(tf_data.keys())[0] if tf_data else ""),
            analysis=f"Rule-based: {bullish_tfs:.0f} bullish TFs, {bearish_tfs:.0f} bearish TFs",
            details={tf: "auto" for tf in tf_data},
            timestamp=time.time(),
        )
    
    # ═════════════════════════════════════════════════════════════════════
    # 5. END-OF-DAY SUMMARY
    # ═════════════════════════════════════════════════════════════════════
    
    def should_send_eod(self) -> bool:
        """Check if it's time for EOD summary (after 22:00 local)."""
        if not self.enabled or not self.eod_summary_enabled:
            return False
        
        now = datetime.now(timezone(timedelta(hours=2)))  # CET
        today = now.strftime("%Y-%m-%d")
        
        if self._last_eod_date == today:
            return False
        
        if now.hour >= 22:
            return True
        
        return False
    
    def generate_eod_summary(self) -> Optional[EndOfDaySummary]:
        """
        Generate end-of-day summary from tracked signals.
        
        Returns:
            EndOfDaySummary or None
        """
        if not self.enabled or not self.eod_summary_enabled:
            return None
        
        if not self._signal_history:
            return None
        
        today = datetime.now(timezone(timedelta(hours=2))).strftime("%Y-%m-%d")
        self._last_eod_date = today
        
        total = len(self._signal_history)
        taken = sum(1 for s in self._signal_history if s["recommendation"] == "TAKE")
        watched = sum(1 for s in self._signal_history if s["recommendation"] == "WATCH")
        skipped = sum(1 for s in self._signal_history if s["recommendation"] == "SKIP")
        
        sorted_signals = sorted(self._signal_history, key=lambda s: s["score"], reverse=True)
        best = f"{sorted_signals[0]['symbol']} {sorted_signals[0]['direction']} ({sorted_signals[0]['score']}/10)" if sorted_signals else "N/A"
        worst = f"{sorted_signals[-1]['symbol']} {sorted_signals[-1]['direction']} ({sorted_signals[-1]['score']}/10)" if sorted_signals else "N/A"
        
        signal_lines = []
        for s in self._signal_history[:30]:
            signal_lines.append(
                f"- {s['symbol']} {s['direction']} | Score: {s['score']}/10 | "
                f"Rec: {s['recommendation']} | Source: {s['source']}"
            )
        
        signal_list = "\n".join(signal_lines)
        
        if self.language == "pl":
            user_msg = (
                f"Podsumowanie dnia — sygnaly z bota:\n\n"
                f"{signal_list}\n\n"
                f"Statystyki: Total={total} TAKE={taken} WATCH={watched} SKIP={skipped}\n"
                f"Najlepszy: {best}\n"
                f"Najgorszy: {worst}\n\n"
                f"Odpowiedz TYLKO w formacie JSON:\n"
                f'{{"lessons": ["lekcja1", "lekcja2", "lekcja3"], '
                f'"outlook": "prognoza na jutro", '
                f'"summary": "podsumowanie dnia"}}'
            )
        else:
            user_msg = (
                f"End-of-day summary — bot signals:\n\n"
                f"{signal_list}\n\n"
                f"Stats: Total={total} TAKE={taken} WATCH={watched} SKIP={skipped}\n"
                f"Best: {best}\n"
                f"Worst: {worst}\n\n"
                f"Respond ONLY in JSON format:\n"
                f'{{"lessons": ["lesson1", "lesson2", "lesson3"], '
                f'"outlook": "tomorrow outlook", '
                f'"summary": "day summary"}}'
            )
        
        messages = [
            {"role": "system", "content": self._sys_prompt()},
            {"role": "user", "content": user_msg},
        ]
        
        response = self.client.chat(messages, temperature=0.4, max_tokens=600)
        
        lessons = []
        outlook = ""
        summary = ""
        
        if response:
            try:
                json_str = response
                if "{" in response:
                    start = response.index("{")
                    end = response.rindex("}") + 1
                    json_str = response[start:end]
                
                data = json.loads(json_str)
                lessons = data.get("lessons", [])
                outlook = data.get("outlook", "")
                summary = data.get("summary", "")
            except (json.JSONDecodeError, ValueError):
                pass
        
        if not lessons:
            lessons = [
                f"Dzis {total} sygnalow — {taken} TAKE, {watched} WATCH, {skipped} SKIP",
                "Sprawdz konfluencje TF przed wejsciem",
            ]
        if not outlook:
            outlook = "Obserwuj kluczowe poziomy wsparcia/oporu"
        if not summary:
            summary = f"Dzien z {total} sygnalami. WR zalezy od source quality (CONFLUENCE > STOCH+NWO > STOCH_ONLY)."
        
        self._signal_history.clear()
        
        return EndOfDaySummary(
            total_signals=total,
            signals_taken=taken,
            signals_watched=watched,
            signals_skipped=skipped,
            best_signal=best,
            worst_signal=worst,
            lessons=lessons,
            outlook=outlook,
            summary=summary,
        )
    
    # ═════════════════════════════════════════════════════════════════════
    # HELPER: Get current market data snapshot
    # ═════════════════════════════════════════════════════════════════════
    
    def get_market_snapshot(self, fetcher, symbols: List[str], timeframe: str = "1h") -> Dict:
        """
        Get market data snapshot for AI analysis.
        
        Args:
            fetcher: Data fetcher (UnifiedDataFetcher or DataFetcher)
            symbols: List of symbols to analyze
            timeframe: Default timeframe
        
        Returns:
            {symbol: {price, change_24h, stoch_k, stoch_d, nwo_osc, nwo_histogram, cvd, trend, regime}}
        """
        from strategy.custom_strategy import get_current_nwo_state
        
        snapshot = {}
        
        for symbol in symbols:
            try:
                df = fetcher.fetch_ohlcv(symbol, timeframe)
                if df.empty or len(df) < 120:
                    continue
                
                nwo_state = get_current_nwo_state(df, symbol, timeframe)
                if not nwo_state:
                    continue
                
                price = nwo_state.get("price", 0)
                prev_close = df['close'].iloc[-2] if len(df) > 1 else price
                change_24h = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
                
                # Determine regime (fallback rule-based)
                trend = nwo_state.get("trend", "?")
                osc = nwo_state.get("osc", 50)
                if osc < 20:
                    regime = "oversold"
                elif osc > 80:
                    regime = "overbought"
                elif trend == "UP":
                    regime = "trending_up"
                elif trend == "DOWN":
                    regime = "trending_down"
                else:
                    regime = "ranging"
                
                snapshot[symbol] = {
                    "price": price,
                    "change_24h": round(change_24h, 2),
                    "stoch_k": nwo_state.get("stoch_k", 0) or 0,
                    "stoch_d": nwo_state.get("stoch_d", 0) or 0,
                    "nwo_osc": nwo_state.get("osc", 0) or 0,
                    "nwo_histogram": nwo_state.get("histogram", 0) or 0,
                    "cvd": nwo_state.get("cvd", 0) or 0,
                    "trend": trend,
                    "regime": regime,
                }
                
            except Exception as e:
                logger.debug(f"[GLM] Snapshot error for {symbol}: {e}")
                continue
        
        return snapshot
    
    @property
    def stats(self) -> dict:
        """Return stats dict for monitoring."""
        return {
            "enabled": self.enabled,
            "language": self.language,
            "features": {
                "signal_scorer": self.signal_scorer_enabled,
                "daily_briefing": self.daily_briefing_enabled,
                "regime_detector": self.regime_detector_enabled,
                "multi_tf_confluence": self.multi_tf_confluence_enabled,
                "eod_summary": self.eod_summary_enabled,
            },
            "signals_scored": len(self._signal_history),
            "regime_cache_size": len(self._regime_cache),
            "last_briefing_age": int(time.time() - self._last_briefing_time) if self._last_briefing_time else -1,
            "briefing_interval": self._briefing_interval,
            "client": self.client.stats if self.client else {},
        }
