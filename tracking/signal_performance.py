"""
Signal Performance Tracker + Self-Learning Filter
═══════════════════════════════════════════════════════════════

Śledzi wyniki sygnałów (win/loss) i pozwala botowi uczyć się na historii.

Komponenty:
  1. SignalPerformanceTracker — SQLite baza wyników sygnałów
  2. SelfLearningFilter — dynamicznie filtruje sygnały na podstawie WR

Schemat SQLite:
  signal_records: pełna historia sygnałów z wynikami
  signal_stats: materializowane statystyki per source/timeframe/direction

Uzycie:
  from tracking.signal_performance import SignalPerformanceTracker, SelfLearningFilter
  tracker = SignalPerformanceTracker(db_path="/app/data/signal_performance.db")
  filter = SelfLearningFilter(tracker)
"""

import sqlite3
import time
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL PERFORMANCE TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SignalRecord:
    """Rekord sygnału z wynikiem."""
    id: Optional[int]
    symbol: str
    timeframe: str
    direction: str          # LONG / SHORT
    source: str             # CONFLUENCE / STOCH+NWO / STOCH STRICT+NWO / STOCH-ONLY
    entry_price: float
    sl: float
    tp: float
    stoch_k: float
    stoch_d: float
    nwo_histogram: float
    cvd: float
    trend: str              # UP / DOWN / ?
    against_trend: bool
    confidence: str         # HIGH / MEDIUM / LOW
    
    # Wynik (wypełniane po zamknięciu)
    outcome: Optional[str] = None       # WIN / LOSS / TIMEOUT / OPEN
    close_price: Optional[float] = None
    pnl_pct: Optional[float] = None
    closed_at: Optional[str] = None
    
    # Metadane
    opened_at: str = ""
    glm_score: Optional[int] = None
    glm_recommendation: Optional[str] = None


class SignalPerformanceTracker:
    """
    Śledzi wyniki wszystkich sygnałów.
    
    Cel: bot może analizować które typy sygnałów przynoszą zysk
    i dynamicznie wyłączać te które tracą.
    """
    
    def __init__(self, db_path: str = "/app/data/signal_performance.db"):
        self.db_path = db_path
        self._conn = None
        self._init_db()
        
        # Cache statystyk (odświeżany co 10 minut)
        self._stats_cache: Dict = {}
        self._stats_cache_time: float = 0
        self._stats_cache_ttl: int = 600  # 10 minut
    
    def _init_db(self):
        # Upewnij się że katalog istnieje
        import os
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")  # 5s timeout na lock
        
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                direction TEXT NOT NULL,
                source TEXT NOT NULL,
                entry_price REAL NOT NULL,
                sl REAL DEFAULT 0,
                tp REAL DEFAULT 0,
                stoch_k REAL DEFAULT 0,
                stoch_d REAL DEFAULT 0,
                nwo_histogram REAL DEFAULT 0,
                cvd REAL DEFAULT 0,
                trend TEXT DEFAULT '?',
                against_trend INTEGER DEFAULT 0,
                confidence TEXT DEFAULT 'MEDIUM',
                outcome TEXT DEFAULT 'OPEN',
                close_price REAL,
                pnl_pct REAL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                glm_score INTEGER,
                glm_recommendation TEXT,
                sl_distance_pct REAL,
                tp_distance_pct REAL,
                rr_ratio REAL
            )
        """)
        
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sr_source ON signal_records(source, outcome)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sr_symbol ON signal_records(symbol, timeframe, direction, outcome)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sr_opened ON signal_records(opened_at)
        """)
        self._conn.commit()
    
    # ─── ZAPISYWANIE SYGNAŁÓW ────────────────────────────────────────────
    
    def record_signal(self, signal_data: Dict) -> Optional[int]:
        """
        Zapisz nowy sygnał do bazy (outcome='OPEN').
        
        Returns:
            ID zapisanego rekordu
        """
        entry = signal_data.get("price", 0)
        sl = signal_data.get("sl", 0)
        tp = signal_data.get("tp", 0)
        
        sl_pct = abs(entry - sl) / entry * 100 if entry > 0 and sl > 0 else 0
        tp_pct = abs(tp - entry) / entry * 100 if entry > 0 and tp > 0 else 0
        rr = tp_pct / sl_pct if sl_pct > 0 else 0
        
        now = datetime.now(timezone.utc).isoformat()
        
        try:
            cursor = self._conn.execute("""
                INSERT INTO signal_records (
                    symbol, timeframe, direction, source,
                    entry_price, sl, tp,
                    stoch_k, stoch_d, nwo_histogram, cvd,
                    trend, against_trend, confidence,
                    outcome, opened_at,
                    glm_score, glm_recommendation,
                    sl_distance_pct, tp_distance_pct, rr_ratio
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?)
            """, (
                signal_data.get("symbol", "?"),
                signal_data.get("timeframe", "?"),
                signal_data.get("direction", "?"),
                signal_data.get("source", "STOCH-ONLY"),
                entry, sl, tp,
                signal_data.get("stoch_k", 0),
                signal_data.get("stoch_d", 0),
                signal_data.get("nwo_histogram", 0),
                signal_data.get("cvd", 0),
                signal_data.get("trend", "?"),
                1 if signal_data.get("against_trend", False) else 0,
                signal_data.get("confidence", "MEDIUM"),
                now,
                signal_data.get("glm_score"),
                signal_data.get("glm_recommendation"),
                round(sl_pct, 3), round(tp_pct, 3), round(rr, 2),
            ))
            self._conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"SignalTracker: record error: {e}")
            return None
    
    def close_signal(self, record_id: int, close_price: float, outcome: str) -> bool:
        """
        Aktualizuj wynik sygnału.
        
        Args:
            record_id: ID z record_signal()
            close_price: Cena zamknięcia
            outcome: 'WIN' / 'LOSS' / 'TIMEOUT'
        """
        try:
            row = self._conn.execute(
                "SELECT entry_price, direction FROM signal_records WHERE id = ?",
                (record_id,)
            ).fetchone()
            
            if not row:
                return False
            
            entry = row["entry_price"]
            direction = row["direction"]
            
            if direction == "LONG":
                pnl_pct = (close_price - entry) / entry * 100
            else:
                pnl_pct = (entry - close_price) / entry * 100
            
            now = datetime.now(timezone.utc).isoformat()
            
            self._conn.execute("""
                UPDATE signal_records
                SET outcome = ?, close_price = ?, pnl_pct = ?, closed_at = ?
                WHERE id = ?
            """, (outcome, close_price, round(pnl_pct, 4), now, record_id))
            self._conn.commit()
            
            # Invalidate stats cache
            self._stats_cache_time = 0
            
            return True
        except Exception as e:
            logger.error(f"SignalTracker: close error: {e}")
            return False
    
    # ─── STATYSTYKI I SELF-LEARNING ──────────────────────────────────────
    
    def get_performance_stats(self, days: int = 30) -> Dict:
        """Oblicz statystyki wyników per source, timeframe, direction."""
        if time.time() - self._stats_cache_time < self._stats_cache_ttl and self._stats_cache:
            return self._stats_cache
        
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        
        rows = self._conn.execute("""
            SELECT source, timeframe, direction, against_trend, confidence,
                   outcome, pnl_pct, glm_score, glm_recommendation
            FROM signal_records
            WHERE outcome IN ('WIN', 'LOSS', 'TIMEOUT')
              AND opened_at >= ?
        """, (since,)).fetchall()
        
        if not rows:
            return {"overall": {}, "by_source": {}, "by_timeframe": {}, "by_direction": {}, "recommendations": []}
        
        def calc_stats(subset):
            if not subset:
                return {"total": 0, "win_rate": 0, "avg_pnl_pct": 0, "wins": 0, "losses": 0}
            wins = [r for r in subset if r["outcome"] == "WIN"]
            losses = [r for r in subset if r["outcome"] != "WIN"]
            pnls = [r["pnl_pct"] for r in subset if r["pnl_pct"] is not None]
            return {
                "total": len(subset),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / len(subset) * 100, 1),
                "avg_pnl_pct": round(sum(pnls) / len(pnls), 3) if pnls else 0,
            }
        
        # By source
        sources = {}
        for source in ["CONFLUENCE", "STOCH+NWO", "STOCH STRICT+NWO", "STOCH-ONLY"]:
            subset = [r for r in rows if r["source"] == source]
            if subset:
                sources[source] = calc_stats(subset)
        
        # By timeframe
        timeframes = {}
        for row in rows:
            tf = row["timeframe"]
            if tf not in timeframes:
                timeframes[tf] = []
            timeframes[tf].append(row)
        timeframes = {tf: calc_stats(rs) for tf, rs in timeframes.items()}
        
        # By direction
        directions = {
            "LONG": calc_stats([r for r in rows if r["direction"] == "LONG"]),
            "SHORT": calc_stats([r for r in rows if r["direction"] == "SHORT"]),
        }
        
        # Against trend analysis
        with_trend = calc_stats([r for r in rows if not r["against_trend"]])
        against_trend = calc_stats([r for r in rows if r["against_trend"]])
        
        # Overall
        overall = calc_stats(rows)
        
        # Recommendations (self-learning hints)
        recommendations = self._generate_recommendations(sources, timeframes, against_trend, overall)
        
        result = {
            "overall": overall,
            "by_source": sources,
            "by_timeframe": timeframes,
            "by_direction": directions,
            "with_trend": with_trend,
            "against_trend": against_trend,
            "recommendations": recommendations,
            "days_analyzed": days,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        
        self._stats_cache = result
        self._stats_cache_time = time.time()
        
        return result
    
    def _generate_recommendations(self, by_source, by_timeframe, against_trend, overall) -> List[str]:
        """Generuj rekomendacje dla self-learning filtra."""
        recs = []
        min_trades = 20
        
        stoch_only = by_source.get("STOCH-ONLY", {})
        if stoch_only.get("total", 0) >= min_trades:
            if stoch_only["win_rate"] < 45:
                recs.append(
                    f"[!] STOCH-ONLY WR={stoch_only['win_rate']}% "
                    f"({stoch_only['total']} trades) — rozważ wyłączenie tego poziomu sygnału"
                )
        
        if against_trend.get("total", 0) >= min_trades:
            if against_trend["win_rate"] < 40:
                recs.append(
                    f"[!] Sygnały COUNTER-TREND WR={against_trend['win_rate']}% "
                    f"({against_trend['total']} trades) — ustaw --trend-filter=block"
                )
        
        tf_5m = by_timeframe.get("5m", {})
        if tf_5m.get("total", 0) >= min_trades:
            if tf_5m["win_rate"] < 45:
                recs.append(
                    f"[!] 5m TF WR={tf_5m['win_rate']}% "
                    f"({tf_5m['total']} trades) — rozważ usunięcie 5m z timeframes"
                )
        
        conf = by_source.get("CONFLUENCE", {})
        if conf.get("total", 0) >= 10:
            if conf["win_rate"] > 55:
                recs.append(
                    f"[OK] CONFLUENCE WR={conf['win_rate']}% ({conf['total']} trades) — "
                    f"ten typ sygnału działa dobrze"
                )
        
        if overall.get("total", 0) >= 50:
            if overall["win_rate"] < 45:
                recs.append(
                    f"[!!] OVERALL WR={overall['win_rate']}% — strategia jest poniżej break-even. "
                    f"Rozważ zmianę parametrów lub wyłączenie bota."
                )
            elif overall["win_rate"] > 55:
                recs.append(
                    f"[OK] OVERALL WR={overall['win_rate']}% — strategia działa powyżej oczekiwań!"
                )
        
        return recs
    
    def find_open_signal(self, symbol: str, direction: str) -> Optional[int]:
        """Znajdź ostatni otwarty sygnał dla symbolu i kierunku.
        
        Używane przez bot.py do zamknięcia sygnału gdy pozycja jest zamykana.
        
        Returns:
            record_id lub None
        """
        try:
            row = self._conn.execute("""
                SELECT id FROM signal_records
                WHERE symbol = ? AND direction = ? AND outcome = 'OPEN'
                ORDER BY opened_at DESC LIMIT 1
            """, (symbol, direction)).fetchone()
            return row["id"] if row else None
        except Exception as e:
            logger.debug(f"SignalTracker: find_open_signal error: {e}")
            return None
    
    def close_signal_by_symbol(self, symbol: str, direction: str, close_price: float, outcome: str) -> bool:
        """Zamknij ostatni otwarty sygnał dla symbolu/kierunku.
        
        Convenience method — nie trzeba znać record_id.
        
        Args:
            symbol: Symbol (np. BTC/USDT)
            direction: LONG / SHORT
            close_price: Cena zamknięcia
            outcome: WIN / LOSS / TIMEOUT
        
        Returns:
            True jeśli zamknięto, False jeśli nie znaleziono
        """
        record_id = self.find_open_signal(symbol, direction)
        if record_id is None:
            return False
        return self.close_signal(record_id, close_price, outcome)
    
    def format_discord_embed(self, days: int = 7) -> Dict:
        """Formatuj statystyki jako Discord embed."""
        stats = self.get_performance_stats(days=days)
        overall = stats.get("overall", {})
        by_source = stats.get("by_source", {})
        recs = stats.get("recommendations", [])
        
        if not overall.get("total"):
            return {
                "title": "Signal Performance — Brak danych",
                "color": 0x9E9E9E,
                "description": f"Za mało sygnałów ({days}d) do analizy.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        
        wr = overall.get("win_rate", 0)
        
        source_lines = []
        for source, s in by_source.items():
            if s["total"] > 0:
                source_lines.append(
                    f"**{source}**: {s['win_rate']}% WR | {s['total']} trades | "
                    f"avg {s['avg_pnl_pct']:+.2f}%"
                )
        
        by_tf = stats.get("by_timeframe", {})
        tf_lines = []
        for tf, s in sorted(by_tf.items()):
            if s["total"] > 0:
                tf_lines.append(f"{tf}: {s['win_rate']}% ({s['total']} trades)")
        
        with_trend = stats.get("with_trend", {})
        against_trend = stats.get("against_trend", {})
        
        fields = [
            {
                "name": f"Overall ({overall['total']} sygnałów, ostatnie {days}d)",
                "value": (
                    f"Win Rate: **{wr}%** ({overall['wins']}W/{overall['losses']}L)\n"
                    f"Avg PnL: **{overall['avg_pnl_pct']:+.3f}%** per trade"
                ),
                "inline": False,
            },
            {
                "name": "Per Zrodlo Sygnalu",
                "value": "\n".join(source_lines) or "Brak danych",
                "inline": False,
            },
            {
                "name": "Per Timeframe",
                "value": "\n".join(tf_lines) or "Brak danych",
                "inline": True,
            },
            {
                "name": "Trend Alignment",
                "value": (
                    f"Z trendem: {with_trend.get('win_rate', 0)}% ({with_trend.get('total', 0)} trades)\n"
                    f"Kontra trend: {against_trend.get('win_rate', 0)}% ({against_trend.get('total', 0)} trades)"
                ),
                "inline": True,
            },
        ]
        
        if recs:
            fields.append({
                "name": "Self-Learning Rekomendacje",
                "value": "\n".join(recs[:5]),
                "inline": False,
            })
        
        return {
            "title": f"Signal Performance Report ({days}d)",
            "color": 0x00E676 if wr > 55 else (0xFFEB3B if wr > 45 else 0xFF1744),
            "fields": fields,
            "footer": {"text": "Signal Performance Tracker | Crypto Signal Bot"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# SELF-LEARNING SIGNAL FILTER
# ═══════════════════════════════════════════════════════════════════════════════

class SelfLearningFilter:
    """
    Dynamicznie wyłącza typy sygnałów które mają zbyt niskie WR.
    
    Działa jako middleware w pipeline sygnałów:
      signal_detector -> SelfLearningFilter -> discord_notifier
    
    Zasady:
      - Wymaga minimum MIN_TRADES_THRESHOLD historycznych tradów
      - Jeśli WR danego źródła < DISABLE_THRESHOLD -> zablokuj ten typ
      - Re-ewaluacja co EVAL_INTERVAL sekund
      - Tryb uczenia: pierwsze 50 tradów = nie blokuje niczego
    """
    
    MIN_TRADES_THRESHOLD = 25
    DISABLE_THRESHOLD = 40.0
    WARN_THRESHOLD = 48.0
    EVAL_INTERVAL = 3600  # Co godzinę
    
    def __init__(self, tracker: SignalPerformanceTracker):
        self.tracker = tracker
        self._disabled_sources: set = set()
        self._disabled_timeframes: set = set()
        self._block_counter_trend: bool = False
        self._last_eval: float = 0
        self._learning_mode: bool = True
    
    def should_send(self, signal_data: Dict) -> Tuple[bool, str]:
        """Sprawdź czy sygnał powinien być wysłany."""
        if time.time() - self._last_eval > self.EVAL_INTERVAL:
            self._evaluate()
        
        source = signal_data.get("source", "STOCH-ONLY")
        timeframe = signal_data.get("timeframe", "?")
        against_trend = signal_data.get("against_trend", False)
        
        if self._learning_mode:
            return True, "Learning mode — all signals allowed"
        
        if source in self._disabled_sources:
            return False, f"Self-learning: {source} disabled (WR < {self.DISABLE_THRESHOLD}%)"
        
        if timeframe in self._disabled_timeframes:
            return False, f"Self-learning: {timeframe} disabled (low WR)"
        
        if against_trend and self._block_counter_trend:
            return False, f"Self-learning: counter-trend signals blocked (WR < {self.DISABLE_THRESHOLD}%)"
        
        return True, "OK"
    
    def _evaluate(self):
        """Ewaluuj statystyki i aktualizuj filtry."""
        self._last_eval = time.time()
        
        stats = self.tracker.get_performance_stats(days=30)
        overall = stats.get("overall", {})
        
        total = overall.get("total", 0)
        if total < self.MIN_TRADES_THRESHOLD:
            self._learning_mode = True
            logger.info(f"SelfLearningFilter: Learning mode ({total}/{self.MIN_TRADES_THRESHOLD} trades)")
            return
        
        self._learning_mode = False
        
        new_disabled_sources = set()
        for source, s in stats.get("by_source", {}).items():
            if s["total"] >= self.MIN_TRADES_THRESHOLD:
                if s["win_rate"] < self.DISABLE_THRESHOLD:
                    new_disabled_sources.add(source)
                    logger.warning(
                        f"SelfLearningFilter: DISABLED source '{source}' "
                        f"WR={s['win_rate']}% ({s['total']} trades)"
                    )
                elif s["win_rate"] < self.WARN_THRESHOLD:
                    logger.info(
                        f"SelfLearningFilter: LOW WR warning '{source}' "
                        f"WR={s['win_rate']}% ({s['total']} trades)"
                    )
        
        self._disabled_sources = new_disabled_sources
        
        new_disabled_tfs = set()
        for tf, s in stats.get("by_timeframe", {}).items():
            if s["total"] >= self.MIN_TRADES_THRESHOLD:
                if s["win_rate"] < self.DISABLE_THRESHOLD:
                    new_disabled_tfs.add(tf)
                    logger.warning(
                        f"SelfLearningFilter: DISABLED timeframe '{tf}' "
                        f"WR={s['win_rate']}% ({s['total']} trades)"
                    )
        
        self._disabled_timeframes = new_disabled_tfs
        
        against = stats.get("against_trend", {})
        if against.get("total", 0) >= self.MIN_TRADES_THRESHOLD:
            self._block_counter_trend = against["win_rate"] < self.DISABLE_THRESHOLD
            if self._block_counter_trend:
                logger.warning(
                    f"SelfLearningFilter: BLOCKING counter-trend signals "
                    f"WR={against['win_rate']}%"
                )
    
    @property
    def status(self) -> Dict:
        return {
            "learning_mode": self._learning_mode,
            "disabled_sources": list(self._disabled_sources),
            "disabled_timeframes": list(self._disabled_timeframes),
            "block_counter_trend": self._block_counter_trend,
            "last_eval_ago": int(time.time() - self._last_eval) if self._last_eval else -1,
        }
