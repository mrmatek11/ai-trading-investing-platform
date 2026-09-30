"""
Position Tracker Module — śledzenie otwartych pozycji z persystentnym stanem
═════════════════════════════════════════════════════════════════════════

Funkcje:
  1. Otwieranie pozycji (na bazie sygnału z bota)
  2. Zamykanie pozycji (SL/TP hit, ręczne, timeout)
  3. Persystentny stan przez SQLite (przetrwa restart bota)
  4. Statystyki PnL, win rate, drawdown
  5. Auto-close na bazie aktualnych cen

Schema SQLite:
  positions:
    - id, symbol, direction, entry_price, sl, tp, size,
      opened_at, closed_at, close_price, close_reason,
      pnl, pnl_pct, timeframe, strategy, signal_reason

Użycie:
  from tracking.position_tracker import PositionTracker
  tracker = PositionTracker("positions.db")
  tracker.open_position(signal)
  tracker.check_closes(current_prices)
  stats = tracker.get_stats()
"""

import os
import sqlite3
import time
import json
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict


# ═══════════════════════════════════════════════════════════════════════════════
# POSITION DATA CLASS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Position:
    """Reprezentuje pojedynczą pozycję tradingową."""
    id: Optional[int] = None
    symbol: str = ""
    direction: str = ""          # "LONG" or "SHORT"
    entry_price: float = 0.0
    sl: float = 0.0             # Stop Loss price
    tp: float = 0.0             # Take Profit price
    size: float = 0.0           # Rozmiar pozycji (w jednostkach)
    opened_at: str = ""         # ISO timestamp
    closed_at: Optional[str] = None
    close_price: Optional[float] = None
    close_reason: str = ""      # "SL", "TP", "manual", "timeout", "trailing_stop"
    pnl: Optional[float] = None         # PnL w walucie
    pnl_pct: Optional[float] = None     # PnL w %
    timeframe: str = ""
    strategy: str = ""
    signal_reason: str = ""
    is_open: bool = True
    atr_at_entry: float = 0.0
    risk_level: str = "NORMAL"  # "NORMAL" or "HIGH" (against trend)

    @property
    def holding_time_hours(self) -> Optional[float]:
        """Ile godzin pozycja jest otwarta."""
        if not self.opened_at:
            return None
        try:
            opened = datetime.fromisoformat(self.opened_at)
            end = datetime.fromisoformat(self.closed_at) if self.closed_at else datetime.now(timezone.utc)
            return (end - opened).total_seconds() / 3600
        except (ValueError, TypeError):
            return None


# ═══════════════════════════════════════════════════════════════════════════════
# POSITION TRACKER (SQLite)
# ═══════════════════════════════════════════════════════════════════════════════

class PositionTracker:
    """
    Śledzi otwarte i zamknięte pozycje z persystentnym stanem (SQLite).

    Flow:
      1. Sygnał z bota → open_position()
      2. Co cykl → check_closes() z aktualnymi cenami
      3. SL/TP hit → auto-close + Discord notification
      4. Stats → get_stats() na żądanie
    """

    # Default config
    DEFAULT_TIMEOUT_HOURS = 72       # Max czas otwartej pozycji (3 dni)
    DEFAULT_POSITION_SIZE_USD = 100   # Domyślny rozmiar w USD
    MAX_OPEN_POSITIONS = 10          # Limit jednocześnie otwartych
    DEFAULT_SLIPPAGE_PCT = 0.001     # 0.1% — używane gdy apply_slippage=True

    def __init__(
        self,
        db_path: str = "positions.db",
        timeout_hours: float = 72,
        default_size_usd: float = 100,
        max_open: int = 10,
        apply_slippage: bool = False,                # Domyślnie OFF — opisuje EXECUTION, nie alerty
        slippage_pct: float = DEFAULT_SLIPPAGE_PCT,  # 0.1% jeśli włączone
    ):
        self.db_path = db_path
        self.timeout_hours = timeout_hours
        self.default_size_usd = default_size_usd
        self.max_open = max_open
        self.apply_slippage = apply_slippage
        self.slippage_pct = slippage_pct
        self._conn = None
        # FIX: RLock (reentrant) zamiast Lock — open_position woła get_open_count()
        # ktore tez bierze mutex; z plain Lock to byl deadlock.
        self._mutex = threading.RLock()
        self._init_db()

    def _init_db(self):
        """Inicjalizuj bazę SQLite."""
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        # FIX #8: WAL mode + busy timeout dla concurrent access
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")  # 5s timeout na lock
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                sl REAL DEFAULT 0,
                tp REAL DEFAULT 0,
                size REAL DEFAULT 0,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                close_price REAL,
                close_reason TEXT DEFAULT '',
                pnl REAL,
                pnl_pct REAL,
                timeframe TEXT DEFAULT '',
                strategy TEXT DEFAULT '',
                signal_reason TEXT DEFAULT '',
                is_open INTEGER DEFAULT 1,
                atr_at_entry REAL DEFAULT 0,
                risk_level TEXT DEFAULT 'NORMAL'
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_positions_open
            ON positions(is_open, symbol)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_positions_symbol
            ON positions(symbol, is_open)
        """)
        self._conn.commit()

    def _row_to_position(self, row: sqlite3.Row) -> Position:
        """Konwertuj wiersz DB na Position object."""
        return Position(
            id=row["id"],
            symbol=row["symbol"],
            direction=row["direction"],
            entry_price=row["entry_price"],
            sl=row["sl"],
            tp=row["tp"],
            size=row["size"],
            opened_at=row["opened_at"],
            closed_at=row["closed_at"],
            close_price=row["close_price"],
            close_reason=row["close_reason"],
            pnl=row["pnl"],
            pnl_pct=row["pnl_pct"],
            timeframe=row["timeframe"],
            strategy=row["strategy"],
            signal_reason=row["signal_reason"],
            is_open=bool(row["is_open"]),
            atr_at_entry=row["atr_at_entry"],
            risk_level=row["risk_level"],
        )

    # ─── Open / Close ──────────────────────────────────────────────────────

    def open_position(self, *args, **kwargs):
        """Otwórz pozycję z mutex (FIX #8: concurrent access)."""
        with self._mutex:
            return self._open_position_impl(*args, **kwargs)
    
    def _open_position_impl(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        sl: float = 0,
        tp: float = 0,
        size: float = 0,
        timeframe: str = "",
        strategy: str = "",
        signal_reason: str = "",
        atr: float = 0,
        risk_level: str = "NORMAL",
    ) -> Optional[Position]:
        """
        Otwórz nową pozycję na bazie sygnału.

        Returns:
            Position object lub None jeśli nie można otworzyć
        """
        # Sprawdź limit (GLOBALNY — nie per-symbol)
        global_open = self.get_open_count()  # bez argumentu = globalnie
        if global_open >= self.max_open:
            print(f"[PositionTracker] Max open positions reached ({global_open}/{self.max_open} globally)")
            return None

        # Sprawdź czy nie ma już otwartej pozycji w tym samym kierunku
        existing = self.get_open_position(symbol, direction)
        if existing:
            print(f"[PositionTracker] Already have open {direction} on {symbol} (id={existing.id})")
            return None

        # Oblicz rozmiar pozycji
        if size <= 0:
            size = self.default_size_usd / entry_price if entry_price > 0 else 0

        # Apply slippage (jeśli włączony — domyślnie OFF dla trybu alertowego)
        if self.apply_slippage:
            slip = entry_price * self.slippage_pct
            if direction == "LONG":
                actual_entry = entry_price + slip  # Kupujesz drożej
            else:
                actual_entry = entry_price - slip  # Sprzedajesz taniej
        else:
            actual_entry = entry_price

        now = datetime.now(timezone.utc).isoformat()

        cursor = self._conn.execute("""
            INSERT INTO positions
            (symbol, direction, entry_price, sl, tp, size, opened_at,
             timeframe, strategy, signal_reason, is_open, atr_at_entry, risk_level)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """, (
            symbol, direction, actual_entry, sl, tp, size, now,
            timeframe, strategy, signal_reason, atr, risk_level,
        ))
        self._conn.commit()

        pos = Position(
            id=cursor.lastrowid,
            symbol=symbol,
            direction=direction,
            entry_price=actual_entry,
            sl=sl,
            tp=tp,
            size=size,
            opened_at=now,
            timeframe=timeframe,
            strategy=strategy,
            signal_reason=signal_reason,
            is_open=True,
            atr_at_entry=atr,
            risk_level=risk_level,
        )

        print(f"[PositionTracker] Opened {direction} {symbol} @ ${actual_entry:,.2f} | SL=${sl:,.2f} TP=${tp:,.2f} (id={pos.id})")
        return pos

    def close_position(self, *args, **kwargs):
        """Zamknij pozycję z mutex (FIX #8: concurrent access)."""
        with self._mutex:
            return self._close_position_impl(*args, **kwargs)
    
    def _close_position_impl(
        self,
        position_id: int,
        close_price: float,
        reason: str = "manual",
    ) -> Optional[Position]:
        """
        Zamknij pozycję.

        Args:
            position_id: ID pozycji
            close_price: Cena zamknięcia
            reason: Powód zamknięcia ("SL", "TP", "manual", "timeout")

        Returns:
            Zaktualizowany Position object z PnL
        """
        row = self._conn.execute(
            "SELECT * FROM positions WHERE id = ?", (position_id,)
        ).fetchone()

        if not row:
            print(f"[PositionTracker] Position {position_id} not found")
            return None

        pos = self._row_to_position(row)

        if not pos.is_open:
            print(f"[PositionTracker] Position {position_id} already closed")
            return None

        # Apply slippage on close (jeśli włączony — odzwierciedla realny fill)
        if self.apply_slippage:
            slip = close_price * self.slippage_pct
            if pos.direction == "LONG":
                actual_close = close_price - slip  # Sprzedajesz taniej
            else:
                actual_close = close_price + slip  # Kupujesz drożej
        else:
            actual_close = close_price

        # Oblicz PnL
        if pos.direction == "LONG":
            pnl = (actual_close - pos.entry_price) * pos.size
            pnl_pct = ((actual_close - pos.entry_price) / pos.entry_price) * 100
        else:  # SHORT
            pnl = (pos.entry_price - actual_close) * pos.size
            pnl_pct = ((pos.entry_price - actual_close) / pos.entry_price) * 100

        now = datetime.now(timezone.utc).isoformat()

        self._conn.execute("""
            UPDATE positions
            SET closed_at = ?, close_price = ?, close_reason = ?,
                pnl = ?, pnl_pct = ?, is_open = 0
            WHERE id = ?
        """, (now, actual_close, reason, round(pnl, 4), round(pnl_pct, 4), position_id))
        self._conn.commit()

        result = Position(
            id=pos.id,
            symbol=pos.symbol,
            direction=pos.direction,
            entry_price=pos.entry_price,
            sl=pos.sl,
            tp=pos.tp,
            size=pos.size,
            opened_at=pos.opened_at,
            closed_at=now,
            close_price=actual_close,
            close_reason=reason,
            pnl=round(pnl, 4),
            pnl_pct=round(pnl_pct, 4),
            timeframe=pos.timeframe,
            strategy=pos.strategy,
            signal_reason=pos.signal_reason,
            is_open=False,
            atr_at_entry=pos.atr_at_entry,
            risk_level=pos.risk_level,
        )

        emoji = "🟢" if pnl > 0 else "🔴"
        print(f"[PositionTracker] Closed {pos.direction} {pos.symbol} @ ${actual_close:,.2f} | {emoji} PnL: ${pnl:+,.2f} ({pnl_pct:+.2f}%) | Reason: {reason}")
        return result

    # ─── Auto-close on SL/TP ──────────────────────────────────────────────

    def check_closes(self, *args, **kwargs):
        """Sprawdź SL/TP z mutex (FIX #8: concurrent access)."""
        with self._mutex:
            return self._check_closes_impl(*args, **kwargs)
    
    def _check_closes_impl(
        self,
        current_prices: Dict[str, float],
        current_bars: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> List[Position]:
        """
        Sprawdź otwarte pozycje i zamknij jeśli SL/TP hit lub timeout.

        Args:
            current_prices: dict symbol → aktualna cena (close)
            current_bars: opcjonalny dict symbol → {"high": ..., "low": ..., "close": ...}
                          Jeśli podany, używamy intra-bar high/low do detekcji SL/TP —
                          to wyłapuje knoty świec, których sam close by przegapił.

        Returns:
            Lista zamkniętych pozycji
        """
        closed = []
        open_positions = self.get_open_positions()

        for pos in open_positions:
            price = current_prices.get(pos.symbol)
            if price is None:
                continue

            # Intra-bar dane — jeśli dostępne, używamy ich do detekcji wybicia SL/TP
            bar = (current_bars or {}).get(pos.symbol)
            bar_high = bar["high"] if bar and "high" in bar else price
            bar_low = bar["low"] if bar and "low" in bar else price

            should_close = False
            reason = ""
            exec_price = price  # cena, po której symulujemy egzekucję

            # ─── SL/TP Check (intra-bar) ──────────────────────────
            if pos.direction == "LONG":
                # SL hit: low baru spadł <= SL
                if pos.sl > 0 and bar_low <= pos.sl:
                    should_close = True
                    reason = "SL"
                    exec_price = pos.sl  # assume fill at SL (pesymistycznie)
                # TP hit: high baru wzrosł >= TP
                elif pos.tp > 0 and bar_high >= pos.tp:
                    should_close = True
                    reason = "TP"
                    exec_price = pos.tp

            elif pos.direction == "SHORT":
                # SL hit: high baru wzrósł >= SL
                if pos.sl > 0 and bar_high >= pos.sl:
                    should_close = True
                    reason = "SL"
                    exec_price = pos.sl
                # TP hit: low baru spadł <= TP
                elif pos.tp > 0 and bar_low <= pos.tp:
                    should_close = True
                    reason = "TP"
                    exec_price = pos.tp

            # ─── Timeout Check ─────────────────────────────────────
            if not should_close and self.timeout_hours > 0:
                hours = pos.holding_time_hours
                if hours and hours > self.timeout_hours:
                    should_close = True
                    reason = "timeout"
                    exec_price = price

            if should_close:
                result = self.close_position(pos.id, exec_price, reason)
                if result:
                    closed.append(result)

        return closed

    # ─── Queries ──────────────────────────────────────────────────────────

    def get_open_positions(self, symbol: str = None) -> List[Position]:
        """Pobierz otwarte pozycje z mutex (FIX #8)."""
        with self._mutex:
            if symbol:
                rows = self._conn.execute(
                    "SELECT * FROM positions WHERE is_open = 1 AND symbol = ? ORDER BY opened_at DESC",
                    (symbol,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM positions WHERE is_open = 1 ORDER BY opened_at DESC"
                ).fetchall()
            return [self._row_to_position(r) for r in rows]

    def get_open_position(self, symbol: str, direction: str = None) -> Optional[Position]:
        """Pobierz otwartą pozycję z mutex (FIX #8)."""
        with self._mutex:
            if direction:
                row = self._conn.execute(
                    "SELECT * FROM positions WHERE is_open = 1 AND symbol = ? AND direction = ? LIMIT 1",
                    (symbol, direction)
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM positions WHERE is_open = 1 AND symbol = ? LIMIT 1",
                    (symbol,)
                ).fetchone()
            return self._row_to_position(row) if row else None

    def get_open_count(self, symbol: str = None) -> int:
        """Ile otwartych pozycji."""
        if symbol:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM positions WHERE is_open = 1 AND symbol = ?",
                (symbol,)
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM positions WHERE is_open = 1"
            ).fetchone()
        return row["cnt"] if row else 0

    def get_closed_positions(
        self,
        symbol: str = None,
        limit: int = 50,
        days: int = 30,
    ) -> List[Position]:
        """Pobierz zamknięte pozycje."""
        since = datetime.now(timezone.utc).timestamp() - (days * 86400)

        if symbol:
            rows = self._conn.execute(
                """SELECT * FROM positions
                   WHERE is_open = 0 AND symbol = ? AND closed_at >= datetime(?, 'unixepoch')
                   ORDER BY closed_at DESC LIMIT ?""",
                (symbol, since, limit)
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM positions
                   WHERE is_open = 0 AND closed_at >= datetime(?, 'unixepoch')
                   ORDER BY closed_at DESC LIMIT ?""",
                (since, limit)
            ).fetchall()
        return [self._row_to_position(r) for r in rows]

    def get_position_by_id(self, position_id: int) -> Optional[Position]:
        """Pobierz pozycję po ID."""
        row = self._conn.execute(
            "SELECT * FROM positions WHERE id = ?", (position_id,)
        ).fetchone()
        return self._row_to_position(row) if row else None

    # ─── Statistics ────────────────────────────────────────────────────────

    def get_stats(self, days: int = 30) -> Dict:
        """
        Oblicz statystyki tradingowe.

        Returns:
            Dict z: total_trades, win_rate, pnl, avg_pnl, profit_factor,
                    max_drawdown, avg_holding_time, by_direction, by_strategy
        """
        closed = self.get_closed_positions(days=days)

        if not closed:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0,
                "total_pnl": 0,
                "avg_pnl_pct": 0,
                "profit_factor": 0,
                "avg_holding_hours": 0,
                "best_trade": 0,
                "worst_trade": 0,
                "by_direction": {},
                "by_strategy": {},
                "by_symbol": {},
            }

        wins = [p for p in closed if p.pnl and p.pnl > 0]
        losses = [p for p in closed if p.pnl and p.pnl <= 0]

        total_pnl = sum(p.pnl for p in closed if p.pnl is not None)
        gross_profit = sum(p.pnl for p in wins if p.pnl is not None)
        gross_loss = abs(sum(p.pnl for p in losses if p.pnl is not None))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        avg_pnl_pct = sum(p.pnl_pct for p in closed if p.pnl_pct is not None) / len(closed)

        holding_times = [p.holding_time_hours for p in closed if p.holding_time_hours is not None]
        avg_holding = sum(holding_times) / len(holding_times) if holding_times else 0

        best = max((p.pnl for p in closed if p.pnl is not None), default=0)
        worst = min((p.pnl for p in closed if p.pnl is not None), default=0)

        # By direction
        by_dir = {}
        for direction in ["LONG", "SHORT"]:
            dir_trades = [p for p in closed if p.direction == direction]
            if dir_trades:
                dir_wins = [p for p in dir_trades if p.pnl and p.pnl > 0]
                dir_pnl = sum(p.pnl for p in dir_trades if p.pnl is not None)
                by_dir[direction] = {
                    "count": len(dir_trades),
                    "win_rate": len(dir_wins) / len(dir_trades) * 100,
                    "pnl": round(dir_pnl, 2),
                }

        # By strategy
        by_strat = {}
        for p in closed:
            strat = p.strategy or "unknown"
            if strat not in by_strat:
                by_strat[strat] = {"count": 0, "pnl": 0, "wins": 0}
            by_strat[strat]["count"] += 1
            by_strat[strat]["pnl"] += p.pnl if p.pnl else 0
            if p.pnl and p.pnl > 0:
                by_strat[strat]["wins"] += 1

        # By symbol
        by_sym = {}
        for p in closed:
            sym = p.symbol
            if sym not in by_sym:
                by_sym[sym] = {"count": 0, "pnl": 0, "wins": 0}
            by_sym[sym]["count"] += 1
            by_sym[sym]["pnl"] += p.pnl if p.pnl else 0
            if p.pnl and p.pnl > 0:
                by_sym[sym]["wins"] += 1

        # Max drawdown (simplified: worst cumulative dip)
        cumulative = 0
        peak = 0
        max_dd = 0
        for p in sorted(closed, key=lambda x: x.closed_at or ""):
            cumulative += p.pnl if p.pnl else 0
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        return {
            "total_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl_pct": round(avg_pnl_pct, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown": round(max_dd, 2),
            "avg_holding_hours": round(avg_holding, 1),
            "best_trade": round(best, 2),
            "worst_trade": round(worst, 2),
            "by_direction": by_dir,
            "by_strategy": by_strat,
            "by_symbol": by_sym,
        }

    def get_unrealized_pnl(
        self,
        current_prices: Dict[str, float],
    ) -> Dict:
        """Oblicz unrealized PnL otwartych pozycji."""
        open_positions = self.get_open_positions()
        total_unrealized = 0
        details = []

        for pos in open_positions:
            price = current_prices.get(pos.symbol)
            if price is None:
                continue

            if pos.direction == "LONG":
                pnl = (price - pos.entry_price) * pos.size
                pnl_pct = ((price - pos.entry_price) / pos.entry_price) * 100
            else:
                pnl = (pos.entry_price - price) * pos.size
                pnl_pct = ((pos.entry_price - price) / pos.entry_price) * 100

            total_unrealized += pnl
            details.append({
                "id": pos.id,
                "symbol": pos.symbol,
                "direction": pos.direction,
                "entry": pos.entry_price,
                "current": price,
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "holding_hours": pos.holding_time_hours,
            })

        return {
            "total_unrealized_pnl": round(total_unrealized, 2),
            "open_count": len(open_positions),
            "positions": details,
        }

    # ─── Utility ──────────────────────────────────────────────────────────

    def close_all(self, current_prices: Dict[str, float], reason: str = "manual_close_all") -> List[Position]:
        """Zamknij wszystkie otwarte pozycje."""
        closed = []
        open_positions = self.get_open_positions()
        for pos in open_positions:
            price = current_prices.get(pos.symbol, pos.entry_price)
            result = self._close_position_impl(pos.id, price, reason)  # Already inside mutex from check_closes
            if result:
                closed.append(result)
        return closed

    def cleanup_old_records(self, days: int = 90):
        """Usuń stare zamknięte pozycje (po 90 dniach)."""
        cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
        self._conn.execute(
            "DELETE FROM positions WHERE is_open = 0 AND closed_at < datetime(?, 'unixepoch')",
            (cutoff,)
        )
        self._conn.commit()

    def close(self):
        """Zamknij połączenie z bazą."""
        if self._conn:
            self._conn.close()

    def __del__(self):
        self.close()
