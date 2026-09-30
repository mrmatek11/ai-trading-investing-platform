"""
Fear & Greed Index Monitor
═════════════════════════════════════════════════════════════════════════

Monitoruje Fear & Greed Index z alternative.me API i wysyła alerty
na Discord gdy indeks zmieni się znacząco lub wejdzie w strefy ekstremalne.

Logika:
  - Sprawdzaj co 6h (indeks aktualizuje się raz dziennie)
  - Alert gdy: zmiana o 15+ punktów, lub wejście w Extreme Fear/Greed
  - Kolor embed zależy od wartości: czerwony → pomarańczowy → żółty → limonka → zielony
  - Cache: nie pobieraj ponownie w ramach interwału

API:
  - https://api.alternative.me/fng/ — darmowe, bez klucza API
  - Zwraca: {value: "25", value_classification: "Fear", timestamp: "..."}

Użycie:
  from analysis.fear_greed import FearGreedMonitor
  monitor = FearGreedMonitor(check_interval=21600, alert_threshold=15)
  reading = monitor.check_and_alert()
  if reading:
      embed = monitor.format_discord(reading)
      # wyślij embed na Discord
"""

import time
import logging
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASS — Pojedynczy odczyt Fear & Greed Index
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FearGreedReading:
    """Pojedynczy odczyt Fear & Greed Index."""
    value: int              # 0-100
    classification: str     # "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"
    previous_value: int     # Poprzedni odczyt (0 jeśli pierwszy)
    change: int             # value - previous_value
    timestamp: float        # Unix timestamp

    @property
    def is_extreme(self) -> bool:
        """Czy indeks w strefie ekstremalnej?"""
        return self.value <= 10 or self.value >= 90

    @property
    def trend_direction(self) -> str:
        """Kierunek trendu: rising / falling / stable."""
        if self.change > 5:
            return "rising"
        elif self.change < -5:
            return "falling"
        return "stable"

    @property
    def emoji(self) -> str:
        """Emoji odpowiadające klasyfikacji."""
        emojis = {
            "Extreme Fear": "😱",
            "Fear": "😨",
            "Neutral": "😐",
            "Greed": "😊",
            "Extreme Greed": "🤑",
        }
        return emojis.get(self.classification, "❓")


# ═══════════════════════════════════════════════════════════════════════════════
# FEAR & GREED MONITOR — Główna klasa monitorująca
# ═══════════════════════════════════════════════════════════════════════════════

class FearGreedMonitor:
    """
    Monitor Fear & Greed Index z alternative.me.

    Pętla:
      1. Sprawdzaj co 6h czy jest nowy odczyt
      2. Porównaj z poprzednim — czy zmiana >= threshold?
      3. Sprawdz czy indeks wszedł w strefę ekstremalną
      4. Jeśli alert → zwróć reading do wysłania na Discord

    Alerty:
      - Zmiana o 15+ punktów od ostatniego odczytu
      - Wejście w strefę Extreme Fear (0-10) lub Extreme Greed (90-100)
    """

    API_URL = "https://api.alternative.me/fng/"

    # Przedziały klasyfikacji — zgodne z alternative.me
    CLASSIFICATIONS: Dict[Tuple[int, int], str] = {
        (0, 10): "Extreme Fear",
        (11, 25): "Fear",
        (26, 45): "Fear",
        (46, 55): "Neutral",
        (56, 75): "Greed",
        (76, 89): "Greed",
        (90, 100): "Extreme Greed",
    }

    # Kolory Discord embed — od strachu do chciwości
    EMBED_COLORS: Dict[str, int] = {
        "Extreme Fear": 0xFF0000,   # Czerwony
        "Fear": 0xFF8C00,           # Pomarańczowy
        "Neutral": 0xFFD700,        # Żółty
        "Greed": 0x32CD32,          # Limonka
        "Extreme Greed": 0x00C853,  # Zielony
    }

    # Kolory gauge bara (hex stringi dla wizualizacji w embed)
    GAUGE_COLORS: Dict[str, str] = {
        "Extreme Fear": "🔴",
        "Fear": "🟠",
        "Neutral": "🟡",
        "Greed": "🟢",
        "Extreme Greed": "💚",
    }

    def __init__(
        self,
        check_interval: int = 21600,   # 6 godzin w sekundach
        alert_threshold: int = 15,      # Minimalna zmiana do alertu
        request_timeout: int = 10,      # Timeout HTTP w sekundach
        max_history: int = 100,         # Maksymalna liczba odczytów w historii
    ):
        self.check_interval = check_interval
        self.alert_threshold = alert_threshold
        self.request_timeout = request_timeout
        self.max_history = max_history

        # Stan wewnętrzny
        self._last_reading: Optional[FearGreedReading] = None
        self._last_check: float = 0
        # Historia odczytów — zwykła lista (nie dataclass field)
        self._history: List[FearGreedReading] = []
        self._api_errors: int = 0
        self._total_checks: int = 0
        self._alerts_sent: int = 0

        logger.info(
            f"FearGreedMonitor: INIT | interval={check_interval}s | "
            f"threshold={alert_threshold}pts | timeout={request_timeout}s"
        )

    # ═════════════════════════════════════════════════════════════════════
    # KLASYFIKACJA — mapuj wartość na nazwę strefy
    # ═════════════════════════════════════════════════════════════════════

    @classmethod
    def classify(cls, value: int) -> str:
        """
        Klasyfikuj wartość Fear & Greed Index.

        Args:
            value: 0-100

        Returns:
            Nazwa klasyfikacji, np. "Extreme Fear", "Greed"
        """
        for (low, high), label in cls.CLASSIFICATIONS.items():
            if low <= value <= high:
                return label
        # Fallback — nie powinno się zdarzyć, ale bezpiecznie
        if value <= 25:
            return "Fear"
        elif value <= 55:
            return "Neutral"
        else:
            return "Greed"

    # ═════════════════════════════════════════════════════════════════════
    # SHOULD CHECK — czy czas na nowy odczyt?
    # ═════════════════════════════════════════════════════════════════════

    def should_check(self) -> bool:
        """
        Czy minął wystarczający czas od ostatniego sprawdzenia?

        Returns:
            True jeśli należy wykonać nowy odczyt
        """
        # Pierwsze sprawdzenie — zawsze
        if self._last_check == 0:
            return True
        return (time.time() - self._last_check) >= self.check_interval

    # ═════════════════════════════════════════════════════════════════════
    # FETCH INDEX — pobierz z API
    # ═════════════════════════════════════════════════════════════════════

    def fetch_index(self) -> Optional[FearGreedReading]:
        """
        Pobierz Fear & Greed Index z alternative.me API.

        API zwraca:
          {
            "data": [
              {
                "value": "25",
                "value_classification": "Fear",
                "timestamp": "1234567890",
                "time_until_update": "12345"
              }
            ]
          }

        Returns:
            FearGreedReading lub None w przypadku błędu
        """
        try:
            # Pobierz obecny i poprzedni odczyt (limit=2)
            resp = requests.get(
                f"{self.API_URL}?limit=2",
                timeout=self.request_timeout,
                headers={"User-Agent": "CryptoSignalBot/5.0"},
            )

            if resp.status_code != 200:
                logger.warning(
                    f"FearGreedMonitor: API zwróciło status {resp.status_code}"
                )
                self._api_errors += 1
                return None

            data = resp.json()
            entries = data.get("data", [])

            if not entries or len(entries) < 1:
                logger.warning("FearGreedMonitor: Pusta odpowiedź API")
                self._api_errors += 1
                return None

            # Obecny odczyt
            current = entries[0]
            value = int(current.get("value", 0))
            classification = self.classify(value)  # Użyj własnej klasyfikacji (spójna)
            timestamp = float(current.get("timestamp", time.time()))

            # Poprzedni odczyt (drugi wpis z API, lub z cache)
            previous_value = 0
            if len(entries) >= 2:
                previous_value = int(entries[1].get("value", 0))
            elif self._last_reading is not None:
                previous_value = self._last_reading.value

            reading = FearGreedReading(
                value=value,
                classification=classification,
                previous_value=previous_value,
                change=value - previous_value,
                timestamp=timestamp,
            )

            # Zapisz do historii
            self._history.append(reading)
            if len(self._history) > self.max_history:
                self._history = self._history[-self.max_history:]

            self._last_check = time.time()
            self._total_checks += 1

            logger.info(
                f"FearGreedMonitor: {reading.emoji} {reading.classification} "
                f"({reading.value}) | change={reading.change:+d} | "
                f"trend={reading.trend_direction}"
            )

            return reading

        except requests.exceptions.Timeout:
            logger.warning("FearGreedMonitor: Timeout API — brak odpowiedzi")
            self._api_errors += 1
            return None
        except requests.exceptions.ConnectionError:
            logger.warning("FearGreedMonitor: Błąd połączenia z API")
            self._api_errors += 1
            return None
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"FearGreedMonitor: Błąd parsowania API: {e}")
            self._api_errors += 1
            return None
        except Exception as e:
            logger.error(f"FearGreedMonitor: Nieoczekiwany błąd: {e}")
            self._api_errors += 1
            return None

    # ═════════════════════════════════════════════════════════════════════
    # CHECK AND ALERT — główna logika alertowania
    # ═════════════════════════════════════════════════════════════════════

    def check_and_alert(self) -> Optional[FearGreedReading]:
        """
        Sprawdź Fear & Greed Index i zwróć odczyt jeśli kwalifikuje się do alertu.

        Kryteria alertu:
          1. Zmiana wartości o alert_threshold (15+) punktów od poprzedniego odczytu
          2. Indeks wszedł w strefę Extreme Fear (0-10) lub Extreme Greed (90-100)
          3. Pierwszy odczyt (brak poprzedniego) — zawsze wysyłaj

        Returns:
            FearGreedReading jeśli alert, None w przeciwnym razie
        """
        # Nie sprawdzaj jeśli nie minął interwał
        if not self.should_check():
            return None

        # Pobierz aktualny odczyt
        reading = self.fetch_index()
        if reading is None:
            return None

        # Pierwszy odczyt — zawsze powiadom
        if self._last_reading is None:
            self._last_reading = reading
            self._alerts_sent += 1
            logger.info(
                f"FearGreedMonitor: PIERWSZY ODCZYT — alert! "
                f"{reading.emoji} {reading.value} ({reading.classification})"
            )
            return reading

        # Sprawdz kryteria alertu
        should_alert = False
        alert_reason = ""

        # 1. Duża zmiana wartości
        if abs(reading.change) >= self.alert_threshold:
            should_alert = True
            direction = "wzrost" if reading.change > 0 else "spadek"
            alert_reason = (
                f"Znaczna zmiana: {reading.change:+d} pkt ({direction}) — "
                f"próg: ±{self.alert_threshold}"
            )

        # 2. Wejście w strefę ekstremalną
        if reading.is_extreme:
            # Sprawdz czy to NOWE wejście (poprzednio nie było extreme)
            was_extreme = (
                self._last_reading.is_extreme
                if self._last_reading
                else False
            )
            if not was_extreme:
                should_alert = True
                zone = "EXTREME FEAR" if reading.value <= 10 else "EXTREME GREED"
                alert_reason = (
                    f"Indeks wszedł w strefę {zone}! "
                    f"Wartość: {reading.value}"
                )
            # Jeśli był już w extreme i się zmienił strefa (Fear ↔ Extreme Fear)
            elif self._last_reading.classification != reading.classification:
                should_alert = True
                alert_reason = (
                    f"Zmiana klasyfikacji: {self._last_reading.classification} → "
                    f"{reading.classification} ({self._last_reading.value} → {reading.value})"
                )

        # Zapisz jako ostatni odczyt
        self._last_reading = reading

        if should_alert:
            self._alerts_sent += 1
            logger.info(
                f"FearGreedMonitor: ALERT! {reading.emoji} {reading.value} "
                f"({reading.classification}) | Powód: {alert_reason}"
            )
            return reading

        logger.debug(
            f"FearGreedMonitor: Brak alertu — {reading.value} "
            f"({reading.classification}), change={reading.change:+d}"
        )
        return None

    # ═════════════════════════════════════════════════════════════════════
    # FORMAT DISCORD — buduj embed
    # ═════════════════════════════════════════════════════════════════════

    def format_discord(self, reading: FearGreedReading) -> Dict:
        """
        Formatuj odczyt jako Discord embed z kolorowym wskaźnikiem.

        Embed zawiera:
          - Tytuł z emoji i wartością
          - Wizualny gauge bar (10 segmentów)
          - Klasyfikację, zmianę, trend
          - Poprzednią wartość
          - Footer z timestamp

        Args:
            reading: Odczyt Fear & Greed Index

        Returns:
            Dict — Discord embed gotowy do wysłania
        """
        # Kolor embed — zależny od klasyfikacji
        embed_color = self.EMBED_COLORS.get(
            reading.classification, 0xFFFFFF
        )

        # Gauge bar — wizualna reprezentacja 0-100 w 10 segmentach
        gauge_bar = self._build_gauge_bar(reading.value)

        # Kierunek zmiany — strzałki
        if reading.change > 0:
            change_emoji = "📈"
            change_text = f"+{reading.change}"
        elif reading.change < 0:
            change_emoji = "📉"
            change_text = f"{reading.change}"
        else:
            change_emoji = "➡️"
            change_text = "0"

        # Trend
        trend_emojis = {
            "rising": "⬆️ Rosnący",
            "falling": "⬇️ Spadający",
            "stable": "↔️ Stabilny",
        }
        trend_text = trend_emojis.get(reading.trend_direction, "❓")

        # Alert reason (ekstremalne strefy)
        alert_note = ""
        if reading.value <= 10:
            alert_note = (
                "\n⚠️ **EXTREME FEAR** — rynku panice, może być okazja do kupna "
                "(\"be greedy when others are fearful\")"
            )
        elif reading.value >= 90:
            alert_note = (
                "\n⚠️ **EXTREME GREED** — euforia rynkowa, ostrożność zalecana "
                "(\"be fearful when others are greedy\")"
            )
        elif abs(reading.change) >= self.alert_threshold:
            direction = "wzrost" if reading.change > 0 else "spadek"
            alert_note = (
                f"\n🔔 Znaczny {direction} indeksu o {abs(reading.change)} pkt"
            )

        # Czas odczytu
        reading_time = datetime.fromtimestamp(
            reading.timestamp, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")

        fields = [
            {
                "name": "Wskaźnik",
                "value": f"{gauge_bar} **{reading.value}/100**",
                "inline": False,
            },
            {
                "name": "Klasyfikacja",
                "value": f"{reading.emoji} **{reading.classification}**",
                "inline": True,
            },
            {
                "name": "Zmiana",
                "value": f"{change_emoji} {change_text} pkt",
                "inline": True,
            },
            {
                "name": "Trend",
                "value": trend_text,
                "inline": True,
            },
            {
                "name": "Poprzedni odczyt",
                "value": f"{reading.previous_value} "
                         f"({self.classify(reading.previous_value)})",
                "inline": True,
            },
            {
                "name": "Czas",
                "value": reading_time,
                "inline": True,
            },
        ]

        # Alert note — dodaj jeśli istnieje
        if alert_note:
            fields.append({
                "name": "Uwaga",
                "value": alert_note.strip(),
                "inline": False,
            })

        embed = {
            "title": f"{reading.emoji} Fear & Greed Index: {reading.value}",
            "color": embed_color,
            "fields": fields,
            "footer": {
                "text": "Fear & Greed Monitor | alternative.me API | Crypto Signal Bot",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return embed

    def _build_gauge_bar(self, value: int) -> str:
        """
        Zbuduj wizualny gauge bar z 10 segmentów.

        Każdy segment = 10 punktów. Wypełnione segmenty mają kolor
        odpowiadający strefie, puste są szare.

        Args:
            value: 0-100

        Returns:
            String z emoji gauge bara
        """
        filled = max(0, min(10, value // 10))

        # Kolor wypełnionych segmentów — zależy od wartości
        if value <= 10:
            fill = "🔴"
        elif value <= 25:
            fill = "🟠"
        elif value <= 45:
            fill = "🟧"
        elif value <= 55:
            fill = "🟡"
        elif value <= 75:
            fill = "🟢"
        elif value <= 89:
            fill = "🟩"
        else:
            fill = "💚"

        empty = "⬛"

        bar = fill * filled + empty * (10 - filled)
        return bar

    # ═════════════════════════════════════════════════════════════════════
    # STATS — statystyki monitora
    # ═════════════════════════════════════════════════════════════════════

    @property
    def stats(self) -> dict:
        """
        Statystyki monitora Fear & Greed.

        Returns:
            Dict z aktualnymi statystykami
        """
        # Oblicz średnią z historii (jeśli jest)
        avg_value = 0
        min_value = 0
        max_value = 0
        if self._history:
            values = [r.value for r in self._history]
            avg_value = round(sum(values) / len(values), 1)
            min_value = min(values)
            max_value = max(values)

        # Czas od ostatniego sprawdzenia
        last_check_ago = int(time.time() - self._last_check) if self._last_check > 0 else 0

        return {
            "current_value": self._last_reading.value if self._last_reading else None,
            "current_classification": (
                self._last_reading.classification
                if self._last_reading
                else None
            ),
            "previous_value": (
                self._last_reading.previous_value
                if self._last_reading
                else None
            ),
            "last_change": (
                self._last_reading.change
                if self._last_reading
                else None
            ),
            "trend_direction": (
                self._last_reading.trend_direction
                if self._last_reading
                else None
            ),
            "last_check_ago_seconds": last_check_ago,
            "check_interval": self.check_interval,
            "alert_threshold": self.alert_threshold,
            "history_size": len(self._history),
            "avg_value": avg_value,
            "min_value": min_value,
            "max_value": max_value,
            "total_checks": self._total_checks,
            "api_errors": self._api_errors,
            "alerts_sent": self._alerts_sent,
        }

    def get_history_summary(self, last_n: int = 10) -> List[Dict]:
        """
        Zwróć podsumowanie ostatnich N odczytów.

        Args:
            last_n: Ile ostatnich odczytów zwrócić

        Returns:
            Lista dictów z podsumowaniem
        """
        recent = self._history[-last_n:] if self._history else []
        return [
            {
                "value": r.value,
                "classification": r.classification,
                "change": r.change,
                "trend": r.trend_direction,
                "time": datetime.fromtimestamp(
                    r.timestamp, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M"),
            }
            for r in recent
        ]

    def format_history_discord(self, last_n: int = 10) -> Optional[Dict]:
        """
        Formatuj historię odczytów jako Discord embed.

        Args:
            last_n: Ile ostatnich odczytów pokazać

        Returns:
            Discord embed lub None jeśli brak historii
        """
        if not self._history:
            return None

        recent = self._history[-last_n:]
        lines = []
        for r in recent:
            change_str = f"{r.change:+d}" if r.change != 0 else "  0"
            arrow = "📈" if r.change > 0 else ("📉" if r.change < 0 else "➡️")
            t = datetime.fromtimestamp(
                r.timestamp, tz=timezone.utc
            ).strftime("%m-%d %H:%M")
            lines.append(
                f"{arrow} `{t}` — **{r.value}** ({r.classification}) {change_str}pts"
            )

        history_text = "\n".join(lines)

        # Statystyki
        values = [r.value for r in recent]
        stats_text = (
            f"Średnia: **{sum(values) / len(values):.0f}** | "
            f"Min: **{min(values)}** | Max: **{max(values)}**"
        )

        # Kolor embed — na podstawie ostatniego odczytu
        last = recent[-1]
        color = self.EMBED_COLORS.get(last.classification, 0xFFFFFF)

        return {
            "title": "📊 Fear & Greed — Historia",
            "color": color,
            "fields": [
                {
                    "name": f"Ostatnie {len(recent)} odczytów",
                    "value": history_text,
                    "inline": False,
                },
                {
                    "name": "Statystyki",
                    "value": stats_text,
                    "inline": False,
                },
            ],
            "footer": {
                "text": "Fear & Greed Monitor | Crypto Signal Bot",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
