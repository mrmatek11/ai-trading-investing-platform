"""Zapisane reguły prop firmy dla rachunku (book) — z nich liczymy zapas do limitów i alerty."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, ExactDecimal, UtcDateTime
from .engine import prop


class PropAccountRow(Base):
    __tablename__ = "prop_accounts"
    __table_args__ = (UniqueConstraint("account", "book", name="uq_prop_account_book"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account: Mapped[str] = mapped_column(String(64), index=True)
    book: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(80))
    initial_balance: Mapped[Decimal] = mapped_column(ExactDecimal)
    daily_loss_pct: Mapped[Decimal] = mapped_column(ExactDecimal)
    max_drawdown_pct: Mapped[Decimal] = mapped_column(ExactDecimal)
    drawdown_type: Mapped[str] = mapped_column(String(10))
    profit_target_pct: Mapped[Optional[Decimal]] = mapped_column(ExactDecimal, nullable=True)
    day_tz: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=lambda: datetime.now(timezone.utc))

    def rules(self) -> prop.PropRules:
        return prop.PropRules(self.initial_balance, self.daily_loss_pct, self.max_drawdown_pct, self.drawdown_type,
                              self.profit_target_pct, self.day_tz, name=self.name)


def status_dict(row: PropAccountRow, st: prop.TodayStatus) -> dict:
    f = lambda d: float(round(d, 2))  # noqa: E731
    return {"id": row.id, "book": row.book, "name": row.name, "day": st.day.isoformat(), "status": st.status,
            "level": st.level, "balance": f(st.balance), "today_pnl": f(st.today_pnl),
            "daily_limit": f(st.daily_limit), "daily_left": f(st.daily_left),
            "overall_limit": f(st.overall_limit), "overall_left": f(st.overall_left),
            "floating": f(st.floating) if st.floating is not None else None,
            "rules": {"initial_balance": f(row.initial_balance), "daily_loss_pct": float(row.daily_loss_pct),
                      "max_drawdown_pct": float(row.max_drawdown_pct), "drawdown_type": row.drawdown_type,
                      "profit_target_pct": float(row.profit_target_pct) if row.profit_target_pct is not None else None,
                      "day_tz": row.day_tz}}
