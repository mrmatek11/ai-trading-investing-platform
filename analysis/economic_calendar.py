"""
Economic Calendar Module — Monitor wydarzen makroekonomicznych
═════════════════════════════════════════════════════════════════════════

Komponenty:
  1. EconomicEvent — dataclass pojedynczego wydarzenia makro
  2. EconomicCalendar — glowny kalendarz z hardcoded events + optional RSS

Logika:
  - Hardcoded major 2025 events (FOMC, NFP, CPI, GDP, ECB, BOJ, NBP MPC)
  - Alert 24h przed high-impact eventem
  - Alert 1h przed high-impact eventem
  - Alert gdy event sie dzieje TERAZ
  - Tygodniowy kalendarz (wysylany w poniedzialki)
  - Dynamiczne obliczanie "time until event"
  - CET timezone dla wyswietlania

Uzycie:
  from analysis.economic_calendar import EconomicCalendar, EconomicEvent
  cal = EconomicCalendar(alert_hours_before=24)
  alerts = cal.check_upcoming()
  for alert_level, event in alerts:
      embed = cal.format_event_discord(alert_level, event)
      # wyslij na Discord

Kolory Discord:
  High impact = czerwony (0xFF0000)
  Medium impact = pomaranczowy (0xFF9800)
  Low impact = niebieski (0x2196F3)
"""

import time
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# STALE CZASOWE
# ═══════════════════════════════════════════════════════════════════════════════

# CET = UTC+1 (zima), CEST = UTC+2 (lato)
# Prosta detekcja: kwiecien-wrzesien = CEST (UTC+2), reszta = CET (UTC+1)
def _get_cet_offset() -> timedelta:
    """Zwraca offset CET/CEST wzgledem UTC."""
    now_utc = datetime.now(timezone.utc)
    # CEST obowiazuje od ostatniej niedzieli marca do ostatniej niedzieli pazdziernika
    # Uproszczenie: kwiecien-wrzesien = CEST
    if now_utc.month in range(4, 11):
        return timedelta(hours=2)
    return timedelta(hours=1)


def now_cet() -> datetime:
    """Zwraca obecny czas w strefie CET/CEST."""
    return datetime.now(timezone.utc) + _get_cet_offset()


# ═══════════════════════════════════════════════════════════════════════════════
# ECONOMIC EVENT DATACLASS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class EconomicEvent:
    """Pojedyncze wydarzenie makroekonomiczne."""
    name: str               # np. "FOMC Meeting Minutes", "Non-Farm Payrolls"
    country: str            # "US", "EU", "JP", "PL", "UK", "CN"
    impact: str             # "high", "medium", "low"
    date: str               # ISO date "2025-03-20"
    time: str               # "14:00" ET (US events) lub "10:00" CET
    forecast: str           # Oczekiwana wartosc (jesli dostepna)
    previous: str           # Poprzednia wartosc (jesli dostepna)
    is_recurring: bool      # Czesc regularnego kalendarza

    @property
    def event_key(self) -> str:
        """Unikalny klucz eventu (do deduplikacji alertow)."""
        return hashlib.md5(f"{self.name}:{self.country}:{self.date}:{self.time}".encode()).hexdigest()[:12]

    @property
    def impact_emoji(self) -> str:
        """Emoji zalezne od wplywu."""
        return {
            "high": "🔴",
            "medium": "🟠",
            "low": "🔵",
        }.get(self.impact, "⚪")

    @property
    def impact_color(self) -> int:
        """Kolor Discord embed zalezny od wplywu."""
        return {
            "high": 0xFF0000,      # Czerwony
            "medium": 0xFF9800,    # Pomaranczowy
            "low": 0x2196F3,       # Niebieski
        }.get(self.impact, 0x9E9E9E)  # Szary fallback

    @property
    def country_flag(self) -> str:
        """Flaga kraju jako emoji."""
        return {
            "US": "🇺🇸",
            "EU": "🇪🇺",
            "JP": "🇯🇵",
            "PL": "🇵🇱",
            "UK": "🇬🇧",
            "CN": "🇨🇳",
            "CH": "🇨🇭",
            "CA": "🇨🇦",
            "AU": "🇦🇺",
            "NZ": "🇳🇿",
        }.get(self.country, "🌍")

    @property
    def country_name(self) -> str:
        """Pelna nazwa kraju."""
        return {
            "US": "USA",
            "EU": "Eurozona",
            "JP": "Japonia",
            "PL": "Polska",
            "UK": "Wielka Brytania",
            "CN": "Chiny",
            "CH": "Szwajcaria",
            "CA": "Kanada",
            "AU": "Australia",
            "NZ": "Nowa Zelandia",
        }.get(self.country, self.country)

    def get_datetime_utc(self) -> Optional[datetime]:
        """
        Przeksztalc date+time na UTC datetime.
        
        Konwencje:
          - US events: time jest w ET (Eastern Time)
          - EU events: time jest w CET/CEST
          - JP events: time jest w JST (UTC+9)
          - PL events: time jest w CET/CEST
          - Jesli time jest puste, przyjmij 13:30 UTC (default market time)
        """
        try:
            date_part = self.date.strip()
            time_part = self.time.strip() if self.time else ""

            if not time_part or time_part == "TBD":
                # Domyslny czas — 13:30 UTC (typowy czas publikacji US danych)
                return datetime(
                    int(date_part[:4]), int(date_part[5:7]), int(date_part[8:10]),
                    13, 30, 0, tzinfo=timezone.utc
                )

            hour, minute = map(int, time_part.split(":"))

            if self.country == "US":
                # ET = UTC-5 (EST) lub UTC-4 (EDT)
                # Uproszczenie: kwiecien-pazdziernik = EDT (UTC-4), reszta = EST (UTC-5)
                month = int(date_part[5:7])
                et_offset = -4 if month in range(4, 11) else -5
                return datetime(
                    int(date_part[:4]), int(date_part[5:7]), int(date_part[8:10]),
                    hour, minute, 0, tzinfo=timezone.utc
                ) + timedelta(hours=-et_offset)

            elif self.country in ("EU", "PL", "CH"):
                # CET = UTC+1, CEST = UTC+2
                month = int(date_part[5:7])
                cet_offset = 2 if month in range(4, 11) else 1
                return datetime(
                    int(date_part[:4]), int(date_part[5:7]), int(date_part[8:10]),
                    hour, minute, 0, tzinfo=timezone.utc
                ) - timedelta(hours=cet_offset)

            elif self.country == "JP":
                # JST = UTC+9 (nie ma DST)
                return datetime(
                    int(date_part[:4]), int(date_part[5:7]), int(date_part[8:10]),
                    hour, minute, 0, tzinfo=timezone.utc
                ) - timedelta(hours=9)

            elif self.country == "UK":
                # GMT = UTC+0, BST = UTC+1
                month = int(date_part[5:7])
                uk_offset = 1 if month in range(4, 11) else 0
                return datetime(
                    int(date_part[:4]), int(date_part[5:7]), int(date_part[8:10]),
                    hour, minute, 0, tzinfo=timezone.utc
                ) - timedelta(hours=uk_offset)

            elif self.country in ("AU", "NZ"):
                # AEST = UTC+10 (uproszczenie)
                return datetime(
                    int(date_part[:4]), int(date_part[5:7]), int(date_part[8:10]),
                    hour, minute, 0, tzinfo=timezone.utc
                ) - timedelta(hours=10)

            else:
                # Default: traktuj jako UTC
                return datetime(
                    int(date_part[:4]), int(date_part[5:7]), int(date_part[8:10]),
                    hour, minute, 0, tzinfo=timezone.utc
                )

        except (ValueError, IndexError) as e:
            logger.debug(f"EconomicEvent: nie udalo sie sparsowac daty '{self.date}' time '{self.time}': {e}")
            return None

    def time_until(self) -> Optional[timedelta]:
        """Oblicz czas pozostaly do eventu."""
        event_dt = self.get_datetime_utc()
        if event_dt is None:
            return None
        now = datetime.now(timezone.utc)
        return event_dt - now

    def format_time_until(self) -> str:
        """Sformatuj czas do eventu jako czytelny tekst."""
        delta = self.time_until()
        if delta is None:
            return "N/A"

        total_seconds = int(delta.total_seconds())
        if total_seconds < 0:
            # Event juz sie zaczal lub zakonczyl
            if total_seconds > -3600:
                return "🔥 TRWA TERAZ!"
            elif total_seconds > -86400:
                hours_ago = abs(total_seconds) // 3600
                return f"Wydarzenie {hours_ago}h temu"
            else:
                days_ago = abs(total_seconds) // 86400
                return f"Wydarzenie {days_ago}d temu"

        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        days = hours // 24

        if days > 0:
            return f"{days}d {hours % 24}h"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"


# ═══════════════════════════════════════════════════════════════════════════════
# ECONOMIC CALENDAR — GLOWNA KLASA
# ═══════════════════════════════════════════════════════════════════════════════

class EconomicCalendar:
    """
    Kalendarz wydarzen makroekonomicznych.
    
    Zrodla danych:
      1. Hardcoded MAJOR_EVENTS — znane daty 2025 (FOMC, NFP, CPI, etc.)
      2. Opcjonalnie: RSS feeds z forexlive/investing.com
      3. Custom events dodane przez add_event()
    
    System alertow:
      - 24h przed: "warning" — przygotuj sie
      - 1h przed:  "urgent"  — uwaga, zaraz publikacja
      - Teraz:     "now"     — event sie dzieje!
    """

    # ─── ZNANE WYDARZENIA 2025 ──────────────────────────────────────────────
    #
    # Zrodla:
    #   FOMC: https://www.federalreserve.gov/newsevents/calendar.htm
    #   NFP:  Pierwszy piatek miesiaca (z wyjatkiem sytuacji specjalnych)
    #   CPI:  Zwykle ~12-13 dnia miesiaca
    #   GDP:  Kwartalnie (advance, second, third estimates)
    #   ECB:  https://www.ecb.europa.eu/press/calendars/html/index.en.html
    #   BOJ:  Bank of Japan monetary policy meetings
    #   NBP:  Rada Polityki Pienieznej (MPC) — zazwyczaj srody
    #
    # Format: (name, country, impact, date, time, forecast, previous, is_recurring)
    # Time konwencja: US=ET, EU/PL=CET, JP=JST

    MAJOR_EVENTS_2025: List[Tuple] = [
        # ═══════════════════════════════════════════════════════════
        # FOMC MEETINGS 2025 (8 spotkan)
        # Publikacja decyzji: 14:00 ET drugiego dnia spotkania
        # ═══════════════════════════════════════════════════════════
        ("FOMC Rate Decision", "US", "high", "2025-01-29", "14:00", "", "", True),
        ("FOMC Rate Decision", "US", "high", "2025-03-19", "14:00", "", "", True),
        ("FOMC Rate Decision", "US", "high", "2025-05-07", "14:00", "", "", True),
        ("FOMC Rate Decision", "US", "high", "2025-06-18", "14:00", "", "", True),
        ("FOMC Rate Decision", "US", "high", "2025-07-30", "14:00", "", "", True),
        ("FOMC Rate Decision", "US", "high", "2025-09-17", "14:00", "", "", True),
        ("FOMC Rate Decision", "US", "high", "2025-10-29", "14:00", "", "", True),
        ("FOMC Rate Decision", "US", "high", "2025-12-10", "14:00", "", "", True),

        # FOMC Meeting Minutes (publikowane 3 tyg. po spotkaniu)
        ("FOMC Meeting Minutes", "US", "high", "2025-02-19", "14:00", "", "", True),
        ("FOMC Meeting Minutes", "US", "high", "2025-04-09", "14:00", "", "", True),
        ("FOMC Meeting Minutes", "US", "high", "2025-05-28", "14:00", "", "", True),
        ("FOMC Meeting Minutes", "US", "high", "2025-07-09", "14:00", "", "", True),
        ("FOMC Meeting Minutes", "US", "high", "2025-08-20", "14:00", "", "", True),
        ("FOMC Meeting Minutes", "US", "high", "2025-10-08", "14:00", "", "", True),
        ("FOMC Meeting Minutes", "US", "high", "2025-11-19", "14:00", "", "", True),
        ("FOMC Meeting Minutes", "US", "high", "2025-12-31", "14:00", "", "", True),

        # ═══════════════════════════════════════════════════════════
        # NON-FARM PAYROLLS (NFP) 2025 — pierwszy piatek miesiaca
        # Publikacja: 08:30 ET
        # ═══════════════════════════════════════════════════════════
        ("Non-Farm Payrolls", "US", "high", "2025-01-10", "08:30", "", "", True),
        ("Non-Farm Payrolls", "US", "high", "2025-02-07", "08:30", "", "", True),
        ("Non-Farm Payrolls", "US", "high", "2025-03-07", "08:30", "", "", True),
        ("Non-Farm Payrolls", "US", "high", "2025-04-04", "08:30", "", "", True),
        ("Non-Farm Payrolls", "US", "high", "2025-05-02", "08:30", "", "", True),
        ("Non-Farm Payrolls", "US", "high", "2025-06-06", "08:30", "", "", True),
        ("Non-Farm Payrolls", "US", "high", "2025-07-03", "08:30", "", "", True),
        ("Non-Farm Payrolls", "US", "high", "2025-08-01", "08:30", "", "", True),
        ("Non-Farm Payrolls", "US", "high", "2025-09-05", "08:30", "", "", True),
        ("Non-Farm Payrolls", "US", "high", "2025-10-03", "08:30", "", "", True),
        ("Non-Farm Payrolls", "US", "high", "2025-11-07", "08:30", "", "", True),
        ("Non-Farm Payrolls", "US", "high", "2025-12-05", "08:30", "", "", True),

        # ═══════════════════════════════════════════════════════════
        # CPI (Consumer Price Index) 2025 — zwykle ~13. dnia miesiaca
        # Publikacja: 08:30 ET
        # ═══════════════════════════════════════════════════════════
        ("CPI (YoY)", "US", "high", "2025-01-15", "08:30", "", "", True),
        ("CPI (YoY)", "US", "high", "2025-02-12", "08:30", "", "", True),
        ("CPI (YoY)", "US", "high", "2025-03-12", "08:30", "", "", True),
        ("CPI (YoY)", "US", "high", "2025-04-10", "08:30", "", "", True),
        ("CPI (YoY)", "US", "high", "2025-05-13", "08:30", "", "", True),
        ("CPI (YoY)", "US", "high", "2025-06-11", "08:30", "", "", True),
        ("CPI (YoY)", "US", "high", "2025-07-11", "08:30", "", "", True),
        ("CPI (YoY)", "US", "high", "2025-08-13", "08:30", "", "", True),
        ("CPI (YoY)", "US", "high", "2025-09-10", "08:30", "", "", True),
        ("CPI (YoY)", "US", "high", "2025-10-14", "08:30", "", "", True),
        ("CPI (YoY)", "US", "high", "2025-11-13", "08:30", "", "", True),
        ("CPI (YoY)", "US", "high", "2025-12-10", "08:30", "", "", True),

        # ═══════════════════════════════════════════════════════════
        # GDP 2025 (kwartalne publikacje)
        # Advance estimate ~1 miesiac po koncu kwartalu
        # ═══════════════════════════════════════════════════════════
        ("GDP (QoQ) Advance", "US", "high", "2025-01-30", "08:30", "", "", True),
        ("GDP (QoQ) Second", "US", "medium", "2025-02-27", "08:30", "", "", True),
        ("GDP (QoQ) Third", "US", "low", "2025-03-27", "08:30", "", "", True),
        ("GDP (QoQ) Advance", "US", "high", "2025-04-30", "08:30", "", "", True),
        ("GDP (QoQ) Second", "US", "medium", "2025-05-29", "08:30", "", "", True),
        ("GDP (QoQ) Third", "US", "low", "2025-06-26", "08:30", "", "", True),
        ("GDP (QoQ) Advance", "US", "high", "2025-07-30", "08:30", "", "", True),
        ("GDP (QoQ) Second", "US", "medium", "2025-08-28", "08:30", "", "", True),
        ("GDP (QoQ) Advance", "US", "high", "2025-10-30", "08:30", "", "", True),
        ("GDP (QoQ) Advance", "US", "high", "2025-12-18", "08:30", "", "", True),

        # ═══════════════════════════════════════════════════════════
        # UNEMPLOYMENT RATE 2025 (razem z NFP)
        # Publikacja: 08:30 ET (razem z NFP)
        # ═══════════════════════════════════════════════════════════
        ("Unemployment Rate", "US", "high", "2025-01-10", "08:30", "", "", True),
        ("Unemployment Rate", "US", "high", "2025-02-07", "08:30", "", "", True),
        ("Unemployment Rate", "US", "high", "2025-03-07", "08:30", "", "", True),
        ("Unemployment Rate", "US", "high", "2025-04-04", "08:30", "", "", True),
        ("Unemployment Rate", "US", "high", "2025-05-02", "08:30", "", "", True),
        ("Unemployment Rate", "US", "high", "2025-06-06", "08:30", "", "", True),
        ("Unemployment Rate", "US", "high", "2025-07-03", "08:30", "", "", True),
        ("Unemployment Rate", "US", "high", "2025-08-01", "08:30", "", "", True),
        ("Unemployment Rate", "US", "high", "2025-09-05", "08:30", "", "", True),
        ("Unemployment Rate", "US", "high", "2025-10-03", "08:30", "", "", True),
        ("Unemployment Rate", "US", "high", "2025-11-07", "08:30", "", "", True),
        ("Unemployment Rate", "US", "high", "2025-12-05", "08:30", "", "", True),

        # ═══════════════════════════════════════════════════════════
        # POWELL SPEECHES (wybrane najwazniejsze)
        # ═══════════════════════════════════════════════════════════
        ("Powell Testimony to Congress", "US", "high", "2025-02-11", "10:00", "", "", True),
        ("Powell Testimony to Congress", "US", "high", "2025-02-12", "10:00", "", "", True),
        ("Powell Press Conference", "US", "high", "2025-03-19", "14:30", "", "", True),
        ("Powell Press Conference", "US", "high", "2025-05-07", "14:30", "", "", True),
        ("Powell Press Conference", "US", "high", "2025-06-18", "14:30", "", "", True),
        ("Powell Press Conference", "US", "high", "2025-07-30", "14:30", "", "", True),
        ("Powell Press Conference", "US", "high", "2025-09-17", "14:30", "", "", True),
        ("Powell Press Conference", "US", "high", "2025-10-29", "14:30", "", "", True),
        ("Powell Press Conference", "US", "high", "2025-12-10", "14:30", "", "", True),

        # ═══════════════════════════════════════════════════════════
        # ECB MONETARY POLICY DECISIONS 2025
        # Publikacja: 13:45 CET, Konferencja prasowa: 14:30 CET
        # ═══════════════════════════════════════════════════════════
        ("ECB Rate Decision", "EU", "high", "2025-01-30", "13:45", "", "", True),
        ("ECB Rate Decision", "EU", "high", "2025-03-06", "13:45", "", "", True),
        ("ECB Rate Decision", "EU", "high", "2025-04-17", "13:45", "", "", True),
        ("ECB Rate Decision", "EU", "high", "2025-06-05", "13:45", "", "", True),
        ("ECB Rate Decision", "EU", "high", "2025-07-24", "13:45", "", "", True),
        ("ECB Rate Decision", "EU", "high", "2025-09-11", "13:45", "", "", True),
        ("ECB Rate Decision", "EU", "high", "2025-10-23", "13:45", "", "", True),
        ("ECB Rate Decision", "EU", "high", "2025-12-11", "13:45", "", "", True),

        # ECB Press Conference (45 min po decyzji)
        ("ECB Press Conference", "EU", "high", "2025-01-30", "14:30", "", "", True),
        ("ECB Press Conference", "EU", "high", "2025-03-06", "14:30", "", "", True),
        ("ECB Press Conference", "EU", "high", "2025-04-17", "14:30", "", "", True),
        ("ECB Press Conference", "EU", "high", "2025-06-05", "14:30", "", "", True),
        ("ECB Press Conference", "EU", "high", "2025-07-24", "14:30", "", "", True),
        ("ECB Press Conference", "EU", "high", "2025-09-11", "14:30", "", "", True),
        ("ECB Press Conference", "EU", "high", "2025-10-23", "14:30", "", "", True),
        ("ECB Press Conference", "EU", "high", "2025-12-11", "14:30", "", "", True),

        # ═══════════════════════════════════════════════════════════
        # BANK OF JAPAN (BOJ) MONETARY POLICY 2025
        # Publikacja: ok. 12:00 JST (03:00 UTC)
        # ═══════════════════════════════════════════════════════════
        ("BOJ Rate Decision", "JP", "high", "2025-01-24", "12:00", "", "", True),
        ("BOJ Rate Decision", "JP", "high", "2025-03-14", "12:00", "", "", True),
        ("BOJ Rate Decision", "JP", "high", "2025-04-25", "12:00", "", "", True),
        ("BOJ Rate Decision", "JP", "high", "2025-06-13", "12:00", "", "", True),
        ("BOJ Rate Decision", "JP", "high", "2025-07-25", "12:00", "", "", True),
        ("BOJ Rate Decision", "JP", "high", "2025-09-19", "12:00", "", "", True),
        ("BOJ Rate Decision", "JP", "high", "2025-10-31", "12:00", "", "", True),
        ("BOJ Rate Decision", "JP", "high", "2025-12-18", "12:00", "", "", True),

        # ═══════════════════════════════════════════════════════════
        # NBP RADA POLITYKI PIERZNEJ (MPC) 2025
        # Posiedzenia: zazwyczaj srody, decyzja ok. 16:00 CET
        # Uwaga: dokladne daty moga byc korygowane przez NBP
        # ═══════════════════════════════════════════════════════════
        ("NBP MPC Rate Decision", "PL", "high", "2025-01-08", "16:00", "", "", True),
        ("NBP MPC Rate Decision", "PL", "high", "2025-02-05", "16:00", "", "", True),
        ("NBP MPC Rate Decision", "PL", "high", "2025-03-05", "16:00", "", "", True),
        ("NBP MPC Rate Decision", "PL", "high", "2025-04-09", "16:00", "", "", True),
        ("NBP MPC Rate Decision", "PL", "high", "2025-05-07", "16:00", "", "", True),
        ("NBP MPC Rate Decision", "PL", "high", "2025-06-04", "16:00", "", "", True),
        ("NBP MPC Rate Decision", "PL", "high", "2025-07-09", "16:00", "", "", True),
        ("NBP MPC Rate Decision", "PL", "high", "2025-08-06", "16:00", "", "", True),
        ("NBP MPC Rate Decision", "PL", "high", "2025-09-03", "16:00", "", "", True),
        ("NBP MPC Rate Decision", "PL", "high", "2025-10-08", "16:00", "", "", True),
        ("NBP MPC Rate Decision", "PL", "high", "2025-11-05", "16:00", "", "", True),
        ("NBP MPC Rate Decision", "PL", "high", "2025-12-03", "16:00", "", "", True),

        # ═══════════════════════════════════════════════════════════
        # INNE WAZNE US EVENTS 2025
        # ═══════════════════════════════════════════════════════════
        # PPI (Producer Price Index)
        ("PPI (YoY)", "US", "medium", "2025-01-14", "08:30", "", "", True),
        ("PPI (YoY)", "US", "medium", "2025-02-13", "08:30", "", "", True),
        ("PPI (YoY)", "US", "medium", "2025-03-13", "08:30", "", "", True),
        ("PPI (YoY)", "US", "medium", "2025-04-11", "08:30", "", "", True),
        ("PPI (YoY)", "US", "medium", "2025-05-14", "08:30", "", "", True),
        ("PPI (YoY)", "US", "medium", "2025-06-12", "08:30", "", "", True),
        ("PPI (YoY)", "US", "medium", "2025-07-10", "08:30", "", "", True),
        ("PPI (YoY)", "US", "medium", "2025-08-14", "08:30", "", "", True),
        ("PPI (YoY)", "US", "medium", "2025-09-11", "08:30", "", "", True),
        ("PPI (YoY)", "US", "medium", "2025-10-15", "08:30", "", "", True),
        ("PPI (YoY)", "US", "medium", "2025-11-14", "08:30", "", "", True),
        ("PPI (YoY)", "US", "medium", "2025-12-12", "08:30", "", "", True),

        # Retail Sales
        ("Retail Sales (MoM)", "US", "medium", "2025-01-15", "08:30", "", "", True),
        ("Retail Sales (MoM)", "US", "medium", "2025-02-14", "08:30", "", "", True),
        ("Retail Sales (MoM)", "US", "medium", "2025-03-17", "08:30", "", "", True),
        ("Retail Sales (MoM)", "US", "medium", "2025-04-15", "08:30", "", "", True),
        ("Retail Sales (MoM)", "US", "medium", "2025-05-15", "08:30", "", "", True),
        ("Retail Sales (MoM)", "US", "medium", "2025-06-17", "08:30", "", "", True),
        ("Retail Sales (MoM)", "US", "medium", "2025-07-16", "08:30", "", "", True),
        ("Retail Sales (MoM)", "US", "medium", "2025-08-15", "08:30", "", "", True),
        ("Retail Sales (MoM)", "US", "medium", "2025-09-16", "08:30", "", "", True),
        ("Retail Sales (MoM)", "US", "medium", "2025-10-16", "08:30", "", "", True),
        ("Retail Sales (MoM)", "US", "medium", "2025-11-17", "08:30", "", "", True),
        ("Retail Sales (MoM)", "US", "medium", "2025-12-15", "08:30", "", "", True),

        # ISM Manufacturing PMI
        ("ISM Manufacturing PMI", "US", "medium", "2025-02-03", "10:00", "", "", True),
        ("ISM Manufacturing PMI", "US", "medium", "2025-03-03", "10:00", "", "", True),
        ("ISM Manufacturing PMI", "US", "medium", "2025-04-01", "10:00", "", "", True),
        ("ISM Manufacturing PMI", "US", "medium", "2025-05-01", "10:00", "", "", True),
        ("ISM Manufacturing PMI", "US", "medium", "2025-06-02", "10:00", "", "", True),
        ("ISM Manufacturing PMI", "US", "medium", "2025-07-01", "10:00", "", "", True),
        ("ISM Manufacturing PMI", "US", "medium", "2025-08-01", "10:00", "", "", True),
        ("ISM Manufacturing PMI", "US", "medium", "2025-09-02", "10:00", "", "", True),
        ("ISM Manufacturing PMI", "US", "medium", "2025-10-01", "10:00", "", "", True),
        ("ISM Manufacturing PMI", "US", "medium", "2025-11-03", "10:00", "", "", True),
        ("ISM Manufacturing PMI", "US", "medium", "2025-12-02", "10:00", "", "", True),

        # ISM Services PMI
        ("ISM Services PMI", "US", "medium", "2025-02-05", "10:00", "", "", True),
        ("ISM Services PMI", "US", "medium", "2025-03-05", "10:00", "", "", True),
        ("ISM Services PMI", "US", "medium", "2025-04-03", "10:00", "", "", True),
        ("ISM Services PMI", "US", "medium", "2025-05-05", "10:00", "", "", True),
        ("ISM Services PMI", "US", "medium", "2025-06-04", "10:00", "", "", True),
        ("ISM Services PMI", "US", "medium", "2025-07-03", "10:00", "", "", True),
        ("ISM Services PMI", "US", "medium", "2025-08-05", "10:00", "", "", True),
        ("ISM Services PMI", "US", "medium", "2025-09-04", "10:00", "", "", True),
        ("ISM Services PMI", "US", "medium", "2025-10-03", "10:00", "", "", True),
        ("ISM Services PMI", "US", "medium", "2025-11-05", "10:00", "", "", True),
        ("ISM Services PMI", "US", "medium", "2025-12-04", "10:00", "", "", True),

        # ═══════════════════════════════════════════════════════════
        # UK EVENTS
        # ═══════════════════════════════════════════════════════════
        ("BOE Rate Decision", "UK", "high", "2025-02-06", "12:00", "", "", True),
        ("BOE Rate Decision", "UK", "high", "2025-03-20", "12:00", "", "", True),
        ("BOE Rate Decision", "UK", "high", "2025-05-08", "12:00", "", "", True),
        ("BOE Rate Decision", "UK", "high", "2025-06-19", "12:00", "", "", True),
        ("BOE Rate Decision", "UK", "high", "2025-08-07", "12:00", "", "", True),
        ("BOE Rate Decision", "UK", "high", "2025-09-18", "12:00", "", "", True),
        ("BOE Rate Decision", "UK", "high", "2025-11-06", "12:00", "", "", True),
        ("BOE Rate Decision", "UK", "high", "2025-12-18", "12:00", "", "", True),

        # ═══════════════════════════════════════════════════════════
        # CHINA EVENTS
        # ═══════════════════════════════════════════════════════════
        ("China GDP (YoY)", "CN", "high", "2025-01-17", "02:00", "", "", True),
        ("China GDP (YoY)", "CN", "high", "2025-04-16", "02:00", "", "", True),
        ("China GDP (YoY)", "CN", "high", "2025-07-16", "02:00", "", "", True),
        ("China GDP (YoY)", "CN", "high", "2025-10-19", "02:00", "", "", True),

        # ═══════════════════════════════════════════════════════════
        # EU CPI (Eurozone HICP)
        # ═══════════════════════════════════════════════════════════
        ("Eurozone CPI (YoY)", "EU", "high", "2025-01-07", "11:00", "", "", True),
        ("Eurozone CPI (YoY)", "EU", "high", "2025-02-03", "11:00", "", "", True),
        ("Eurozone CPI (YoY)", "EU", "high", "2025-03-04", "11:00", "", "", True),
        ("Eurozone CPI (YoY)", "EU", "high", "2025-04-01", "11:00", "", "", True),
        ("Eurozone CPI (YoY)", "EU", "high", "2025-05-02", "11:00", "", "", True),
        ("Eurozone CPI (YoY)", "EU", "high", "2025-06-03", "11:00", "", "", True),
        ("Eurozone CPI (YoY)", "EU", "high", "2025-07-01", "11:00", "", "", True),
        ("Eurozone CPI (YoY)", "EU", "high", "2025-08-01", "11:00", "", "", True),
        ("Eurozone CPI (YoY)", "EU", "high", "2025-09-02", "11:00", "", "", True),
        ("Eurozone CPI (YoY)", "EU", "high", "2025-10-01", "11:00", "", "", True),
        ("Eurozone CPI (YoY)", "EU", "high", "2025-11-04", "11:00", "", "", True),
        ("Eurozone CPI (YoY)", "EU", "high", "2025-12-03", "11:00", "", "", True),
    ]

    # ─── RSS FEEDS (opcjonalne, do przyszlego uzycia) ────────────
    RSS_FEEDS = {
        "forexlive": "https://www.forexlive.com/feed",
        "investing_econcal": "https://www.investing.com/economic-calendar/",
        "fxstreet": "https://www.fxstreet.com/rss",
    }

    def __init__(self, alert_hours_before: int = 24):
        """
        Inicjalizuj kalendarz makroekonomiczny.
        
        Args:
            alert_hours_before: Ile godzin przed eventem zaczac alertowac (default: 24h)
        """
        self.alert_hours_before = alert_hours_before

        # Stan alertow — slowi deduplikacje (nie spamuj tym samym alertem)
        # Format: {event_key: "reported_24h"} lub {event_key: "reported_1h"} lub {event_key: "reported_now"}
        self._reported_events: Dict[str, str] = {}

        # Czas ostatniego sprawdzenia RSS
        self._last_rss_check: float = 0

        # Custom events dodane recznie
        self._custom_events: List[EconomicEvent] = []

        # Inicjalizuj hardcoded events
        self._hardcoded_events: List[EconomicEvent] = []
        for ev_tuple in self.MAJOR_EVENTS_2025:
            self._hardcoded_events.append(EconomicEvent(
                name=ev_tuple[0],
                country=ev_tuple[1],
                impact=ev_tuple[2],
                date=ev_tuple[3],
                time=ev_tuple[4],
                forecast=ev_tuple[5],
                previous=ev_tuple[6],
                is_recurring=ev_tuple[7],
            ))

        # Czyszczenie starych alertow co jakis czas (max 500 kluczy)
        self._max_reported = 500

        logger.info(
            f"EconomicCalendar: INIT | Alert {alert_hours_before}h przed | "
            f"Hardcoded events: {len(self._hardcoded_events)}"
        )

    # ═════════════════════════════════════════════════════════════════════
    # ZARZADZANIE EVENTAMI
    # ═════════════════════════════════════════════════════════════════════

    def add_event(self, event: EconomicEvent) -> None:
        """Dodaj custom event do kalendarza."""
        self._custom_events.append(event)
        logger.info(f"EconomicCalendar: Dodano custom event: {event.name} ({event.date})")

    def get_all_events(self, include_past: bool = False, days_ahead: int = 30) -> List[EconomicEvent]:
        """
        Pobierz wszystkie eventy (hardcoded + custom).
        
        Args:
            include_past: Czy wlaczyc przeszle eventy
            days_ahead: Ile dni w przod pokazywac (default: 30)
        
        Returns:
            Lista EconomicEvent posortowana po dacie
        """
        all_events = self._hardcoded_events + self._custom_events

        if not include_past:
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=2)  # Pokazuj eventy do 2h wstecz
            future_cutoff = now + timedelta(days=days_ahead)

            filtered = []
            for ev in all_events:
                ev_dt = ev.get_datetime_utc()
                if ev_dt is None:
                    # Jesli nie udalo sie sparsowac daty, sprawdz chocby date
                    try:
                        date_only = datetime.strptime(ev.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                        if date_only >= cutoff and date_only <= future_cutoff:
                            filtered.append(ev)
                    except ValueError:
                        continue
                elif cutoff <= ev_dt <= future_cutoff:
                    filtered.append(ev)

            all_events = filtered

        # Sortuj po dacie
        all_events.sort(key=lambda e: e.date + " " + (e.time if e.time else "00:00"))
        return all_events

    def get_events_by_impact(self, impact: str = "high", days_ahead: int = 7) -> List[EconomicEvent]:
        """Pobierz eventy o danym wplywie."""
        return [ev for ev in self.get_all_events(days_ahead=days_ahead) if ev.impact == impact]

    def get_events_by_country(self, country: str, days_ahead: int = 7) -> List[EconomicEvent]:
        """Pobierz eventy dla danego kraju."""
        return [ev for ev in self.get_all_events(days_ahead=days_ahead) if ev.country == country]

    # ═════════════════════════════════════════════════════════════════════
    # SYSTEM ALERTOW
    # ═════════════════════════════════════════════════════════════════════

    def check_upcoming(self) -> List[Tuple[str, EconomicEvent]]:
        """
        Sprawdz nadchodzace eventy i zwroc alerty.
        
        Logika alertow (tylko high-impact):
          - "warning" — 24h przed eventem
          - "urgent"  — 1h przed eventem
          - "now"     — event sie dzieje teraz (w oknie +-30 min)
        
        Dedyklikacja: kazdy alert dla danego eventa wysylany jest tylko RAZ.
        
        Returns:
            Lista (alert_level, event) krotek
        """
        alerts = []
        now = datetime.now(timezone.utc)

        # Sprawdz tylko high i medium impact events
        events = self.get_all_events(include_past=False, days_ahead=2)

        for event in events:
            # Tylko high i medium impact
            if event.impact not in ("high", "medium"):
                continue

            ev_dt = event.get_datetime_utc()
            if ev_dt is None:
                continue

            time_until = ev_dt - now
            total_seconds = time_until.total_seconds()
            hours_until = total_seconds / 3600

            event_key = event.event_key

            # Alert: 24h przed (dla high impact)
            if event.impact == "high" and 0 < hours_until <= self.alert_hours_before:
                report_key = f"{event_key}_24h"
                if report_key not in self._reported_events:
                    self._reported_events[report_key] = "reported_24h"
                    alerts.append(("warning", event))

            # Alert: 1h przed (dla high i medium impact)
            if 0 < hours_until <= 1:
                report_key = f"{event_key}_1h"
                if report_key not in self._reported_events:
                    self._reported_events[report_key] = "reported_1h"
                    alerts.append(("urgent", event))

            # Alert: teraz (w oknie +-30 min)
            if -1800 <= total_seconds <= 1800:  # -30min do +30min
                report_key = f"{event_key}_now"
                if report_key not in self._reported_events:
                    self._reported_events[report_key] = "reported_now"
                    alerts.append(("now", event))

        # Czyszczenie starych kluczy (prevent memory leak)
        self._cleanup_reported_keys()

        if alerts:
            logger.info(f"EconomicCalendar: {len(alerts)} alertow makroekonomicznych")

        return alerts

    def _cleanup_reported_keys(self) -> None:
        """Usun stare klucze z _reported_events zeby nie wyczerpac pamieci."""
        if len(self._reported_events) > self._max_reported:
            # Zachowaj tylko najnowsze klucze
            # Poniewaz dict w Python 3.7+ zachowuje kolejnosc wstawiania,
            # usun najstarsze
            keys = list(self._reported_events.keys())
            for key in keys[: len(keys) - self._max_reported // 2]:
                del self._reported_events[key]

    # ═════════════════════════════════════════════════════════════════════
    # FORMATOWANIE DISCORD
    # ═════════════════════════════════════════════════════════════════════

    def format_event_discord(self, alert_level: str, event: EconomicEvent) -> Dict:
        """
        Formatuj alert o wydarzeniu makro jako Discord embed.
        
        Args:
            alert_level: "warning" (24h), "urgent" (1h), "now" (teraz)
            event: EconomicEvent
        
        Returns:
            Dict z Discord embed
        """
        # Naglowek zalezny od poziomu alertu
        if alert_level == "now":
            alert_emoji = "🔥"
            alert_text = "WYDARZENIE TERAZ!"
            alert_color = 0xFF0000  # Czerwony — krytyczne
        elif alert_level == "urgent":
            alert_emoji = "⚡"
            alert_text = "1H PRzed PUBLIKACJA"
            alert_color = 0xFF6600  # Ciemny pomaranczowy
        elif alert_level == "warning":
            alert_emoji = "⚠️"
            alert_text = "24H PRzed WYDARZENIEM"
            alert_color = 0xFFC107  # Zolty warning
        else:
            alert_emoji = "📅"
            alert_text = "NADCHODZACE WYDARZENIE"
            alert_color = event.impact_color

        # Konwertuj czas do CET
        ev_dt = event.get_datetime_utc()
        cet_offset = _get_cet_offset()
        if ev_dt:
            cet_time = ev_dt + cet_offset
            cet_time_str = cet_time.strftime("%H:%M") + " CET"
            cet_date_str = cet_time.strftime("%d.%m.%Y")
        else:
            cet_time_str = event.time + " (local)"
            cet_date_str = event.date

        # Czas do eventu
        time_until_str = event.format_time_until()

        # Buduj pola embeda
        fields = [
            {
                "name": f"{alert_emoji} {alert_text}",
                "value": f"{event.impact_emoji} **{event.name}**",
                "inline": False,
            },
            {
                "name": "Kraj",
                "value": f"{event.country_flag} {event.country_name}",
                "inline": True,
            },
            {
                "name": "Wplyw",
                "value": f"{event.impact_emoji} {event.impact.upper()}",
                "inline": True,
            },
            {
                "name": "Data",
                "value": f"📅 {cet_date_str}",
                "inline": True,
            },
            {
                "name": "Czas (CET)",
                "value": f"🕐 {cet_time_str}",
                "inline": True,
            },
            {
                "name": "Czas do eventu",
                "value": f"⏳ {time_until_str}",
                "inline": True,
            },
        ]

        # Forecast i previous (jesli dostepne)
        if event.forecast:
            fields.append({
                "name": "Prognoza",
                "value": f"📊 {event.forecast}",
                "inline": True,
            })

        if event.previous:
            fields.append({
                "name": "Poprzednie",
                "value": f"📈 {event.previous}",
                "inline": True,
            })

        # Ostrzezenie dla high-impact
        if event.impact == "high":
            fields.append({
                "name": "⚠️ OSTRZEZENIE",
                "value": "Wysoki wplyw na rynki! Oczekuj zwiekszonej zmiennosci i szerszych spreadow. "
                         "Rozwarz zmniejszenie pozycji lub ustawienie szerszych SL.",
                "inline": False,
            })

        return {
            "title": f"📅 Kalendarz Makro: {event.name}",
            "color": alert_color,
            "fields": fields,
            "footer": {"text": "Economic Calendar | Crypto Signal Bot"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def format_weekly_calendar_discord(self) -> Optional[Dict]:
        """
        Formatuj tygodniowy kalendarz makro jako Discord embed.
        
        Wysylany w poniedzialki — podsumowanie nadchodzacych wydarzen na caly tydzien.
        
        Returns:
            Dict z Discord embed, lub None jesli brak wydarzen w tym tygodniu
        """
        # Pobierz wydarzenia na najblizsze 7 dni
        events = self.get_all_events(include_past=False, days_ahead=7)

        if not events:
            return None

        # Grupuj po dniach
        days_map: Dict[str, List[EconomicEvent]] = {}
        for ev in events:
            if ev.date not in days_map:
                days_map[ev.date] = []
            days_map[ev.date].append(ev)

        # Dni tygodnia po polsku
        day_names_pl = {
            0: "Poniedzialek",
            1: "Wtorek",
            2: "Sroda",
            3: "Czwartek",
            4: "Piatek",
            5: "Sobota",
            6: "Niedziela",
        }

        # Buduj tekst kalendarza
        calendar_lines = []
        high_impact_count = 0
        medium_impact_count = 0

        for date_str in sorted(days_map.keys()):
            day_events = days_map[date_str]

            # Oblicz dzien tygodnia
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                day_name = day_names_pl.get(date_obj.weekday(), date_str)
                display_date = f"{day_name} ({date_str[5:]})"  # "Poniedzialek (03-20)"
            except ValueError:
                display_date = date_str

            # Naglowek dnia
            calendar_lines.append(f"\n**📅 {display_date}**")

            # Sortuj: high -> medium -> low, potem po czasie
            day_events.sort(key=lambda e: ({"high": 0, "medium": 1, "low": 2}.get(e.impact, 3), e.time))

            for ev in day_events:
                # Konwertuj czas do CET
                ev_dt = ev.get_datetime_utc()
                if ev_dt:
                    cet_time = (ev_dt + _get_cet_offset()).strftime("%H:%M")
                else:
                    cet_time = ev.time if ev.time else "TBD"

                line = f"  {ev.impact_emoji} {cet_time} — **{ev.name}** {ev.country_flag}"
                if ev.forecast:
                    line += f" (exp: {ev.forecast})"
                calendar_lines.append(line)

                if ev.impact == "high":
                    high_impact_count += 1
                elif ev.impact == "medium":
                    medium_impact_count += 1

        calendar_text = "\n".join(calendar_lines)

        # Ogranicz dlugosc (Discord limit 1024 znakow na field value)
        if len(calendar_text) > 1000:
            calendar_text = calendar_text[:997] + "..."

        # Tydzien: data od-do
        today = now_cet()
        week_end = today + timedelta(days=7)
        week_title = f"Kalendarz Makro: {today.strftime('%d.%m')} - {week_end.strftime('%d.%m.%Y')}"

        return {
            "title": f"📅 {week_title}",
            "color": 0x673AB7,  # Fioletowy (kalendarz)
            "fields": [
                {
                    "name": "Wydarzenia tego tygodnia",
                    "value": calendar_text,
                    "inline": False,
                },
                {
                    "name": "Podsumowanie",
                    "value": f"🔴 High Impact: **{high_impact_count}** | "
                             f"🟠 Medium: **{medium_impact_count}** | "
                             f"Total: **{len(events)}**",
                    "inline": False,
                },
            ],
            "footer": {"text": "Economic Calendar | Crypto Signal Bot | Czasy w CET"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def format_daily_preview_discord(self) -> Optional[Dict]:
        """
        Formatuj dzienny podglad makro jako Discord embed.
        
        Wysylany rano — wydarzenia na dzisiejszy dzien.
        
        Returns:
            Dict z Discord embed, lub None jesli brak wydarzen dzisiaj
        """
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_events = [ev for ev in self.get_all_events(include_past=False, days_ahead=1)
                        if ev.date == today_str]

        if not today_events:
            return None

        # Sortuj: high -> medium -> low, potem po czasie
        today_events.sort(key=lambda e: ({"high": 0, "medium": 1, "low": 2}.get(e.impact, 3), e.time))

        lines = []
        high_count = 0

        for ev in today_events:
            ev_dt = ev.get_datetime_utc()
            if ev_dt:
                cet_time = (ev_dt + _get_cet_offset()).strftime("%H:%M")
                time_until = ev.format_time_until()
            else:
                cet_time = ev.time if ev.time else "TBD"
                time_until = "N/A"

            line = f"{ev.impact_emoji} **{cet_time} CET** — {ev.name} {ev.country_flag}"
            if ev.impact == "high":
                line += " ⚠️"
                high_count += 1
            line += f"\n   ⏳ {time_until}"

            if ev.forecast:
                line += f" | Exp: {ev.forecast}"

            lines.append(line)

        text = "\n".join(lines)

        if len(text) > 1000:
            text = text[:997] + "..."

        # Kolor zalezy od najwyzszego impactu dzisiaj
        if high_count > 0:
            color = 0xFF0000  # Czerwony — sa high-impact events
        elif any(ev.impact == "medium" for ev in today_events):
            color = 0xFF9800  # Pomaranczowy
        else:
            color = 0x2196F3  # Niebieski

        today_cet = now_cet()
        title = f"📋 Dzisiejszy Kalendarz Makro ({today_cet.strftime('%d.%m.%Y')})"

        if high_count > 0:
            title += f" — {high_count} HIGH IMPACT!"

        return {
            "title": title,
            "color": color,
            "fields": [
                {
                    "name": f"Wydarzenia na dzis ({len(today_events)})",
                    "value": text,
                    "inline": False,
                },
            ],
            "footer": {"text": "Economic Calendar | Czasy w CET"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ═════════════════════════════════════════════════════════════════════
    # INTEGRACJA Z BOTEM — czy wyslac tygodniowy/dzienny kalendarz?
    # ═════════════════════════════════════════════════════════════════════

    def should_send_weekly_calendar(self) -> bool:
        """Czy wyslac tygodniowy kalendarz? (poniedzialek rano, raz na tydzien)."""
        today = now_cet()
        # Poniedzialek = weekday 0, miedzy 7:00 a 10:00 CET
        if today.weekday() == 0 and 7 <= today.hour < 10:
            week_key = f"weekly_{today.strftime('%Y-W%W')}"
            if week_key not in self._reported_events:
                self._reported_events[week_key] = "sent"
                return True
        return False

    def should_send_daily_preview(self) -> bool:
        """Czy wyslac dzienny podglad? (codziennie rano, raz na dzien)."""
        today = now_cet()
        # Miedzy 7:00 a 9:00 CET
        if 7 <= today.hour < 9:
            day_key = f"daily_{today.strftime('%Y-%m-%d')}"
            if day_key not in self._reported_events:
                self._reported_events[day_key] = "sent"
                return True
        return False

    # ═════════════════════════════════════════════════════════════════════
    # OPCJONALNY RSS (do przyszlego rozszerzenia)
    # ═════════════════════════════════════════════════════════════════════

    def fetch_rss_events(self) -> List[EconomicEvent]:
        """
        Pobierz wydarzenia z RSS feeds (opcjonalne).
        
        UWAGA: RSS scraping jest niestabilny i zalezy od dostepnosci zrodel.
        Hardcoded events sa glownym zrodlem — RSS jest uzupeknieniem.
        
        Returns:
            Lista EconomicEvent z RSS (moze byc pusta)
        """
        rss_events: List[EconomicEvent] = []

        # Sprawdz czy minelo 30 min od ostatniego sprawdzenia
        if time.time() - self._last_rss_check < 1800:
            return rss_events

        self._last_rss_check = time.time()

        try:
            import requests
            import xml.etree.ElementTree as ET

            # Proba pobrania z forexlive RSS
            feed_url = self.RSS_FEEDS.get("forexlive")
            if feed_url:
                resp = requests.get(feed_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200:
                    root = ET.fromstring(resp.text)
                    for item_elem in root.iter("item"):
                        title = item_elem.findtext("title", "")
                        # Szukaj slow kluczowych
                        keywords = ["FOMC", "NFP", "CPI", "GDP", "ECB", "rate decision",
                                    "non-farm", "inflation", "employment", "unemployment"]
                        title_lower = title.lower()

                        if any(kw.lower() in title_lower for kw in keywords):
                            # Proba wyciagniecia daty z tytulu lub pubDate
                            pub_date = item_elem.findtext("pubDate", "")
                            link = item_elem.findtext("link", "")

                            # Uproszczone parsowanie — daty w RSS sa rozne
                            rss_events.append(EconomicEvent(
                                name=title[:80],
                                country="US",  # Domyslnie
                                impact="medium",
                                date=now_cet().strftime("%Y-%m-%d"),  # Dzisiejsza data jako fallback
                                time="",
                                forecast="",
                                previous="",
                                is_recurring=False,
                            ))

        except Exception as e:
            logger.debug(f"EconomicCalendar: RSS fetch error: {e}")

        if rss_events:
            logger.info(f"EconomicCalendar: {len(rss_events)} eventow z RSS")

        return rss_events

    # ═════════════════════════════════════════════════════════════════════
    # STATS
    # ═════════════════════════════════════════════════════════════════════

    @property
    def stats(self) -> dict:
        """Statystyki kalendarza do monitorowania."""
        upcoming = self.get_all_events(include_past=False, days_ahead=7)
        high_impact = [ev for ev in upcoming if ev.impact == "high"]
        next_high = None
        if high_impact:
            next_high_event = high_impact[0]
            next_high = {
                "name": next_high_event.name,
                "date": next_high_event.date,
                "time_until": next_high_event.format_time_until(),
            }

        return {
            "total_hardcoded_events": len(self._hardcoded_events),
            "custom_events": len(self._custom_events),
            "upcoming_7d": len(upcoming),
            "high_impact_7d": len(high_impact),
            "next_high_impact": next_high,
            "reported_alerts": len(self._reported_events),
            "alert_hours_before": self.alert_hours_before,
            "last_rss_check": self._last_rss_check,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER: Generuj NFP dla dowolnego roku/miesiaca
# ═══════════════════════════════════════════════════════════════════════════════

def generate_nfp_dates(year: int) -> List[str]:
    """
    Generuj daty NFP (pierwszy piatek miesiaca) dla danego roku.
    
    NFP (Non-Farm Payrolls) jest publikowany w pierwszy piatek miesiaca,
    z wyjatkiem sytuacji gdy pierwszy piatek wypada na 1. (wowczas 2. piatek).
    
    Args:
        year: Rok
    
    Returns:
        Lista dat ISO ("2025-01-03", "2025-02-07", ...)
    """
    dates = []
    for month in range(1, 13):
        # Znajdz pierwszy piatek miesiaca
        first_day = datetime(year, month, 1)
        # weekday(): 0=Monday, 4=Friday
        days_until_friday = (4 - first_day.weekday()) % 7
        first_friday = first_day + timedelta(days=days_until_friday)

        # Jesli pierwszy piatek to 1. dnia miesiaca, NFP jest w drugi piatek
        if first_friday.day == 1:
            first_friday += timedelta(days=7)

        dates.append(first_friday.strftime("%Y-%m-%d"))

    return dates


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER: Generuj NBP MPC daty (przyblizone)
# ═══════════════════════════════════════════════════════════════════════════════

def generate_nbp_mpc_dates(year: int) -> List[str]:
    """
    Generuj przyblizone daty posiedzen NBP MPC dla danego roku.
    
    Uwaga: NBP publikuje oficjalny kalendarz na poczatku roku.
    Te daty sa przyblizone — zazwyczaj pierwsza sroda miesiaca.
    
    Args:
        year: Rok
    
    Returns:
        Lista dat ISO
    """
    dates = []
    # NBP MPC spotyka sie zazwyczaj w srody — zazwyczaj pierwszy lub drugi sroda miesiaca
    # Uzyj przyblizonych dat (moga sie roznic od oficjalnych)
    mpc_months = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]  # Zazwyczaj co miesiac

    for month in mpc_months:
        # Znajdz pierwsza srode miesiaca
        first_day = datetime(year, month, 1)
        days_until_wed = (2 - first_day.weekday()) % 7
        first_wed = first_day + timedelta(days=days_until_wed)

        # Zwykle jest to pierwsza lub druga sroda
        # Uzyj pierwszej srody jako przyblizenie
        dates.append(first_wed.strftime("%Y-%m-%d"))

    return dates


# ═══════════════════════════════════════════════════════════════════════════════
# TEST / DEMO
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """Test modulu — wyswietl kalendarz i alerty."""
    import json

    print("=" * 70)
    print("ECONOMIC CALENDAR — TEST")
    print("=" * 70)

    cal = EconomicCalendar(alert_hours_before=24)

    # Statystyki
    stats = cal.stats
    print(f"\n📊 Statystyki:")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    # Nadchodzace wydarzenia (7 dni)
    print(f"\n📅 Nadchodzace wydarzenia (7 dni):")
    events = cal.get_all_events(days_ahead=7)
    for ev in events[:20]:
        time_str = ev.format_time_until()
        print(f"  {ev.impact_emoji} {ev.country_flag} {ev.date} {ev.time} — {ev.name} ({time_str})")

    # Alerty
    print(f"\n🔔 Alerty:")
    alerts = cal.check_upcoming()
    for alert_level, event in alerts:
        print(f"  [{alert_level.upper()}] {event.name} ({event.country}) — {event.format_time_until()}")

    # Tygodniowy kalendarz
    print(f"\n📋 Tygodniowy kalendarz (Discord embed):")
    weekly = cal.format_weekly_calendar_discord()
    if weekly:
        print(json.dumps(weekly, indent=2, ensure_ascii=False))

    # Dzienny podglad
    print(f"\n📋 Dzienny podglad (Discord embed):")
    daily = cal.format_daily_preview_discord()
    if daily:
        print(json.dumps(daily, indent=2, ensure_ascii=False))
    else:
        print("  Brak wydarzen na dzis")

    # Test NFP generatora
    print(f"\n📅 NFP daty 2025:")
    nfp_dates = generate_nfp_dates(2025)
    for d in nfp_dates:
        print(f"  {d}")

    # Test dodania custom eventa
    print(f"\n➕ Dodaje custom event...")
    custom_ev = EconomicEvent(
        name="BTC ETF Decision",
        country="US",
        impact="high",
        date="2025-03-21",
        time="10:00",
        forecast="Approved",
        previous="Pending",
        is_recurring=False,
    )
    cal.add_event(custom_ev)
    print(f"  Dodano: {custom_ev.name} ({custom_ev.date})")

    # Formatuj jako Discord embed
    embed = cal.format_event_discord("warning", custom_ev)
    print(f"\n📋 Discord embed dla custom event:")
    print(json.dumps(embed, indent=2, ensure_ascii=False))

    print("\n" + "=" * 70)
    print("KONIEC TESTU")
    print("=" * 70)
