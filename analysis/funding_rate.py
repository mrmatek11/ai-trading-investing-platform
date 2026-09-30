"""
Funding Rate Monitor Module
═══════════════════════════════════════════════════════════════════════════

Monitoruje funding rates na Binance Perpetual Futures i alarmuje
gdy stawki sa ekstremalne (longowie placza za duzo / shortowie placza za duzo).

Logika:
  - Pobiera funding rates z Binance Futures API (darmowe, publiczne)
  - Sprawdza co 8h (kiedy funding rate sie aktualizuje) lub na zadanie
  - Alarm gdy: funding rate > 0.1% (longs pay) lub < -0.1% (shorts pay)
  - Oblicza annualizowany rate: funding_rate * 3 * 365
  - Kategoryzuje: normal / elevated / extreme
  - Wysyla na Discord jako color-coded embed

Uzycie:
  from analysis.funding_rate import FundingRateMonitor
  monitor = FundingRateMonitor()
  rates = monitor.fetch_rates()
  extreme = monitor.check_extreme_rates()
  embed = monitor.format_discord(extreme)
"""

import time
import logging
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FundingRateData:
    """Dane funding rate dla pojedynczego symbolu."""
    symbol: str
    funding_rate: float         # np. 0.001 = 0.1%
    annual_rate: float          # funding_rate * 3 * 365
    mark_price: float
    next_funding_time: str      # ISO format
    direction: str              # "longs_pay" lub "shorts_pay"
    severity: str               # "normal", "elevated", "extreme"

    @property
    def funding_rate_pct(self) -> str:
        """Funding rate jako procent, np. '+0.100%'."""
        return f"{self.funding_rate * 100:+.4f}%"

    @property
    def annual_rate_pct(self) -> str:
        """Annualizowany rate jako procent, np. '+109.5%'."""
        return f"{self.annual_rate * 100:+.1f}%"

    @property
    def severity_emoji(self) -> str:
        """Emoji w zaleznosci od severity."""
        if self.severity == "extreme":
            return ":fire:" if self.direction == "longs_pay" else ":snowflake:"
        elif self.severity == "elevated":
            return ":warning:"
        return ":white_circle:"


# ═══════════════════════════════════════════════════════════════════════════════
# FUNDING RATE MONITOR — MAIN CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class FundingRateMonitor:
    """
    Monitor funding rates na Binance Perpetual Futures.

    Pobiera aktualne stawki fundingowe z publicznego API Binance,
    kategoryzuje je (normal/elevated/extreme) i formatuje
    jako Discord embed z kolorami.

    Funding rate = koszt trzymania pozycji dluzej niz 8h:
      - Dodatni (np. +0.1%) = LONGowie placza SHORTom
      - Ujemny (np. -0.1%) = SHORTowie placza LONGom
      - Aktualizacja co 8h (00:00, 08:00, 16:00 UTC)

    Co oznaczaja ekstremalne stawki:
      - Wysoki dodatni = rynek bardzo long-biased (mozliwy squeeze)
      - Wysoki ujemny = rynek bardzo short-biased (mozliwy short squeeze)
      - Oba = okazja kontrarian
    """

    # Binance Futures API — premiumIndex zawiera aktualny funding rate + mark price
    BINANCE_FUNDING_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"

    # Perpetual futures do monitorowania
    SYMBOLS = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
        "XRPUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT",
        "DOTUSDT", "LINKUSDT",
    ]

    # Progi severity
    THRESHOLDS = {
        "elevated": 0.0005,   # 0.05%
        "extreme": 0.001,     # 0.1%
    }

    # Kolory Discord embed
    COLORS = {
        "extreme": 0xFF1744,    # Czerwony
        "elevated": 0xFF9800,   # Pomaranczowy
        "normal": 0x4CAF50,     # Zielony
    }

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        elevated_threshold: float = 0.0005,
        extreme_threshold: float = 0.001,
    ):
        """
        Inicjalizuj monitor funding rates.

        Args:
            symbols: Lista symboli perpetual futures do monitorowania
            elevated_threshold: Prog dla "elevated" severity (default 0.05%)
            extreme_threshold: Prog dla "extreme" severity (default 0.1%)
        """
        self.symbols = symbols or self.SYMBOLS
        self.thresholds = {
            "elevated": elevated_threshold,
            "extreme": extreme_threshold,
        }
        self._last_check: float = 0
        self._last_rates: Dict[str, FundingRateData] = {}
        self._fetch_count: int = 0
        self._error_count: int = 0

        logger.info(
            f"FundingRateMonitor: INIT | Symbols={len(self.symbols)} | "
            f"Elevated={elevated_threshold * 100:.3f}% | "
            f"Extreme={extreme_threshold * 100:.2f}%"
        )

    # ═════════════════════════════════════════════════════════════════════
    # FETCH FUNDING RATES
    # ═════════════════════════════════════════════════════════════════════

    def fetch_rates(self) -> List[FundingRateData]:
        """
        Pobierz aktualne funding rates z Binance API.

        Uzywa endpointu premiumIndex ktory zwraca:
          - symbol, markPrice, lastFundingRate, nextFundingTime

        Returns:
            Lista FundingRateData posortowana po |funding_rate| malejaco
        """
        try:
            # Pobierz dane z Binance (jeden request dla wszystkich symboli)
            resp = requests.get(
                self.BINANCE_FUNDING_URL,
                timeout=15,
                params={"symbol": ""},  # Pusty = wszystkie symbole
            )

            if resp.status_code != 200:
                logger.warning(
                    f"FundingRateMonitor: Binance API zwracilo {resp.status_code} — {resp.text[:200]}"
                )
                self._error_count += 1
                # Zwroc stare dane jesli sa
                if self._last_rates:
                    logger.info("FundingRateMonitor: Uzywam cache z poprzedniego sprawdzenia")
                    return list(self._last_rates.values())
                return []

            data = resp.json()

            # Filtruj tylko nasze symbole
            symbol_set = set(self.symbols)
            rates: List[FundingRateData] = []

            for item in data:
                symbol = item.get("symbol", "")
                if symbol not in symbol_set:
                    continue

                try:
                    funding_rate = float(item.get("lastFundingRate", "0"))
                    mark_price = float(item.get("markPrice", "0"))
                    next_funding_ts = int(item.get("nextFundingTime", "0"))
                except (ValueError, TypeError):
                    logger.debug(f"FundingRateMonitor: Nie mozna sparsowac danych dla {symbol}")
                    continue

                # Pomin jezeli funding rate = 0 (niektore nowe pary)
                if funding_rate == 0 and mark_price == 0:
                    continue

                # Oblicz annualizowany rate
                # 3 okresy fundingowe dziennie * 365 dni
                annual_rate = funding_rate * 3 * 365

                # Kierunek: kto placi komu
                if funding_rate > 0:
                    direction = "longs_pay"     # Longowie placa shortom
                elif funding_rate < 0:
                    direction = "shorts_pay"    # Shortowie placa longom
                else:
                    direction = "neutral"

                # Severity (na podstawie wartosci bezwzglednej)
                abs_rate = abs(funding_rate)
                if abs_rate >= self.thresholds["extreme"]:
                    severity = "extreme"
                elif abs_rate >= self.thresholds["elevated"]:
                    severity = "elevated"
                else:
                    severity = "normal"

                # Formatuj next funding time
                if next_funding_ts > 0:
                    next_funding_str = datetime.fromtimestamp(
                        next_funding_ts / 1000, tz=timezone.utc
                    ).strftime("%H:%M UTC")
                else:
                    next_funding_str = "N/A"

                rate_data = FundingRateData(
                    symbol=symbol,
                    funding_rate=funding_rate,
                    annual_rate=annual_rate,
                    mark_price=mark_price,
                    next_funding_time=next_funding_str,
                    direction=direction,
                    severity=severity,
                )
                rates.append(rate_data)

            # Sortuj po wartosci bezwzglednej funding rate (najwieksze pierwsze)
            rates.sort(key=lambda r: abs(r.funding_rate), reverse=True)

            # Zapisz do cache
            self._last_rates = {r.symbol: r for r in rates}
            self._last_check = time.time()
            self._fetch_count += 1

            # Loguj podsumowanie
            extreme_count = sum(1 for r in rates if r.severity == "extreme")
            elevated_count = sum(1 for r in rates if r.severity == "elevated")
            logger.info(
                f"FundingRateMonitor: Pobrano {len(rates)} symboli | "
                f"Extreme={extreme_count} | Elevated={elevated_count} | "
                f"Top={rates[0].symbol} {rates[0].funding_rate_pct}" if rates else ""
            )

            return rates

        except requests.exceptions.Timeout:
            logger.warning("FundingRateMonitor: Binance API timeout (>15s)")
            self._error_count += 1
            if self._last_rates:
                return list(self._last_rates.values())
            return []

        except requests.exceptions.ConnectionError:
            logger.warning("FundingRateMonitor: Brak polaczenia z Binance API")
            self._error_count += 1
            if self._last_rates:
                return list(self._last_rates.values())
            return []

        except Exception as e:
            logger.error(f"FundingRateMonitor: Nieoczekiwany blad: {e}")
            self._error_count += 1
            if self._last_rates:
                return list(self._last_rates.values())
            return []

    # ═════════════════════════════════════════════════════════════════════
    # CHECK EXTREME RATES
    # ═════════════════════════════════════════════════════════════════════

    def check_extreme_rates(self) -> List[FundingRateData]:
        """
        Zwroc tylko elevated i extreme funding rates.

        Przydatne do generowania alertow — nie spamuj Discord
        normalnymi stawkami, tylko te ktore sa nietypowe.

        Returns:
            Lista FundingRateData z severity >= elevated, posortowana
            po |funding_rate| malejaco
        """
        rates = self.fetch_rates()

        extreme_rates = [
            r for r in rates
            if r.severity in ("elevated", "extreme")
        ]

        if extreme_rates:
            # Sortuj: extreme first, potem elevated, potem po |rate|
            severity_order = {"extreme": 0, "elevated": 1}
            extreme_rates.sort(
                key=lambda r: (severity_order.get(r.severity, 2), -abs(r.funding_rate))
            )

            logger.info(
                f"FundingRateMonitor: Znaleziono {len(extreme_rates)} nietypowych stawek"
            )

        return extreme_rates

    # ═════════════════════════════════════════════════════════════════════
    # FORMAT DISCORD EMBED
    # ═════════════════════════════════════════════════════════════════════

    def format_discord(self, rates: List[FundingRateData]) -> Optional[Dict]:
        """
        Formatuj funding rates jako Discord embed.

        Pokazuje:
          - Top rates (najdrozsze dla longow/shortow)
          - Kolor zalezny od najwyzszego severity
          - Annualizowane stawki
          - Direction info (kto placi komu)

        Args:
            rates: Lista FundingRateData do wyswietlenia

        Returns:
            Dict z Discord embed structure lub None jesli brak danych
        """
        if not rates:
            return None

        # Okresl kolor embed na podstawie najwyzszego severity
        max_severity = "normal"
        for r in rates:
            if r.severity == "extreme":
                max_severity = "extreme"
                break
            elif r.severity == "elevated" and max_severity == "normal":
                max_severity = "elevated"

        embed_color = self.COLORS.get(max_severity, self.COLORS["normal"])

        # Podziel na longs_pay i shorts_pay
        longs_pay = [r for r in rates if r.direction == "longs_pay"]
        shorts_pay = [r for r in rates if r.direction == "shorts_pay"]
        neutral = [r for r in rates if r.direction == "neutral"]

        fields = []

        # ── Longs Pay (dodatni funding) ──
        if longs_pay:
            longs_lines = []
            for r in longs_pay[:5]:  # Top 5
                emoji = r.severity_emoji
                longs_lines.append(
                    f"{emoji} **{r.symbol}** {r.funding_rate_pct} "
                    f"(ann: {r.annual_rate_pct}) "
                    f"@ ${r.mark_price:,.2f}"
                )
            longs_text = "\n".join(longs_lines)
            fields.append({
                "name": ":red_circle: Longowie placa (positive funding)",
                "value": longs_text,
                "inline": False,
            })

        # ── Shorts Pay (ujemny funding) ──
        if shorts_pay:
            shorts_lines = []
            for r in shorts_pay[:5]:  # Top 5
                emoji = r.severity_emoji
                shorts_lines.append(
                    f"{emoji} **{r.symbol}** {r.funding_rate_pct} "
                    f"(ann: {r.annual_rate_pct}) "
                    f"@ ${r.mark_price:,.2f}"
                )
            shorts_text = "\n".join(shorts_lines)
            fields.append({
                "name": ":large_blue_circle: Shortowie placa (negative funding)",
                "value": shorts_text,
                "inline": False,
            })

        # ── Normal rates (kompaktowo) ──
        if neutral:
            neutral_text = ", ".join(r.symbol for r in neutral[:5])
            fields.append({
                "name": ":white_circle: Neutralne",
                "value": neutral_text,
                "inline": False,
            })

        # ── Podsumowanie ──
        extreme_count = sum(1 for r in rates if r.severity == "extreme")
        elevated_count = sum(1 for r in rates if r.severity == "elevated")
        normal_count = sum(1 for r in rates if r.severity == "normal")

        # Sredni funding rate
        avg_rate = sum(r.funding_rate for r in rates) / len(rates) if rates else 0
        avg_annual = avg_rate * 3 * 365

        summary_lines = [
            f"Extreme: **{extreme_count}** | Elevated: **{elevated_count}** | Normal: **{normal_count}**",
            f"Sredni funding: {avg_rate * 100:+.4f}% (ann: {avg_annual * 100:+.1f}%)",
        ]

        # Najwyzszy/najnizszy rate
        if rates:
            highest = max(rates, key=lambda r: r.funding_rate)
            lowest = min(rates, key=lambda r: r.funding_rate)
            summary_lines.append(
                f"Najwyzszy: {highest.symbol} {highest.funding_rate_pct} | "
                f"Najnizszy: {lowest.symbol} {lowest.funding_rate_pct}"
            )

        # Kiedy nastepny funding
        next_funding = rates[0].next_funding_time if rates else "N/A"
        summary_lines.append(f"Nastepny funding: {next_funding}")

        fields.append({
            "name": ":bar_chart: Podsumowanie",
            "value": "\n".join(summary_lines),
            "inline": False,
        })

        # Tytul z severity
        if max_severity == "extreme":
            title = ":fire: EXTREME Funding Rates"
        elif max_severity == "elevated":
            title = ":warning: Elevated Funding Rates"
        else:
            title = ":chart_with_upwards_trend: Funding Rates Overview"

        return {
            "title": title,
            "color": embed_color,
            "fields": fields,
            "footer": {
                "text": (
                    f"Funding Rate Monitor | "
                    f"Thresholds: elevated >{self.thresholds['elevated'] * 100:.3f}% "
                    f"extreme >{self.thresholds['extreme'] * 100:.2f}%"
                ),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ═════════════════════════════════════════════════════════════════════
    # FORMAT DISCORD — ALERT (tylko extreme)
    # ═════════════════════════════════════════════════════════════════════

    def format_alert_discord(self, extreme_rates: List[FundingRateData]) -> Optional[Dict]:
        """
        Formatuj ALERT tylko dla extreme funding rates.

        Uzywane gdy chcesz wyslac powiadomienie TYLKO o ekstremalnych
        stawkach (nie wysylaj calego raportu).

        Args:
            extreme_rates: Lista z check_extreme_rates()

        Returns:
            Dict z Discord embed lub None
        """
        if not extreme_rates:
            return None

        # Czy jakikolwiek extreme?
        has_extreme = any(r.severity == "extreme" for r in extreme_rates)

        lines = []
        for r in extreme_rates:
            if r.direction == "longs_pay":
                dir_text = "LONG -> SHORT"
                dir_emoji = ":hot_face:"
            else:
                dir_text = "SHORT -> LONG"
                dir_emoji = ":cold_face:"

            lines.append(
                f"{r.severity_emoji} **{r.symbol}** — {r.funding_rate_pct} "
                f"(ann: {r.annual_rate_pct})\n"
                f"  {dir_emoji} {dir_text} | "
                f"Mark: ${r.mark_price:,.2f} | "
                f"Next: {r.next_funding_time}"
            )

        text = "\n".join(lines)

        # Interpretacja
        interpretation = self._interpret_rates(extreme_rates)

        embed_color = self.COLORS["extreme"] if has_extreme else self.COLORS["elevated"]

        return {
            "title": (
                ":rotating_light: EXTREME Funding Rate Alert"
                if has_extreme
                else ":warning: Elevated Funding Rate Alert"
            ),
            "color": embed_color,
            "fields": [
                {
                    "name": f"{len(extreme_rates)} nietypowych stawek",
                    "value": text,
                    "inline": False,
                },
                {
                    "name": ":brain: Interpretacja",
                    "value": interpretation,
                    "inline": False,
                },
            ],
            "footer": {
                "text": "Funding Rate Monitor | Binance Futures",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ═════════════════════════════════════════════════════════════════════
    # INTERPRETACJA
    # ═════════════════════════════════════════════════════════════════════

    @staticmethod
    def _interpret_rates(rates: List[FundingRateData]) -> str:
        """
        Zinterpretuj aktualne funding rates — co oznaczaja dla rynku.

        Zwraca krotki opis sytuacji rynkowej na podstawie stawek.
        """
        if not rates:
            return "Brak danych do interpretacji"

        longs_extreme = sum(1 for r in rates if r.direction == "longs_pay" and r.severity == "extreme")
        shorts_extreme = sum(1 for r in rates if r.direction == "shorts_pay" and r.severity == "extreme")
        longs_elevated = sum(1 for r in rates if r.direction == "longs_pay" and r.severity == "elevated")
        shorts_elevated = sum(1 for r in rates if r.direction == "shorts_pay" and r.severity == "elevated")

        parts = []

        # Ekstremalnie dodatni = long squeeze risk
        if longs_extreme > 0:
            parts.append(
                f":fire: **{longs_extreme} symboli** z ekstremalnie wysokim fundingiem — "
                f"longowie przepalaja kapital, mozliwy **long squeeze** lub korekta."
            )

        # Ekstremalnie ujemny = short squeeze risk
        if shorts_extreme > 0:
            parts.append(
                f":snowflake: **{shorts_extreme} symboli** z ekstremalnie ujemnym fundingiem — "
                f"shortowie placza, mozliwy **short squeeze** lub odbicie."
            )

        # Podwyzszone dodatnie = overleveraged longs
        if longs_elevated > 0:
            parts.append(
                f":warning: **{longs_elevated} symboli** z podwyzszonym fundingiem — "
                f"rynek long-biased, uwaga na korekte."
            )

        # Podwyzszone ujemne = overleveraged shorts
        if shorts_elevated > 0:
            parts.append(
                f":warning: **{shorts_elevated} symboli** z podwyzszonym ujemnym fundingiem — "
                f"rynek short-biased, uwaga na odbicie."
            )

        # Brak interpretacji
        if not parts:
            parts.append("Funding rates w normie — brak sygnalow ekstremalnych.")

        return "\n".join(parts)

    # ═════════════════════════════════════════════════════════════════════
    # HELPERS
    # ═════════════════════════════════════════════════════════════════════

    def should_check(self, interval_seconds: int = 28800) -> bool:
        """
        Czy juz czas sprawdzic funding rates?

        Domyjlnie co 8h (28800s) — tyle trwa okres fundingowy.

        Args:
            interval_seconds: Interwal w sekundach (default 8h)

        Returns:
            True jezeli minal interval od ostatniego sprawdzenia
        """
        if self._last_check == 0:
            return True  # Pierwsze sprawdzenie
        return time.time() - self._last_check >= interval_seconds

    def get_rate(self, symbol: str) -> Optional[FundingRateData]:
        """
        Pobierz funding rate dla konkretnego symbolu z cache.

        Args:
            symbol: Symbol perpetual futures, np. "BTCUSDT"

        Returns:
            FundingRateData lub None jezeli nie ma w cache
        """
        return self._last_rates.get(symbol)

    def get_top_rates(self, n: int = 5) -> List[FundingRateData]:
        """
        Pobierz top N funding rates (najdrozsze bezwzglednie).

        Args:
            n: Ile symboli zwrocic

        Returns:
            Lista FundingRateData
        """
        rates = list(self._last_rates.values())
        rates.sort(key=lambda r: abs(r.funding_rate), reverse=True)
        return rates[:n]

    def get_most_expensive_long(self, n: int = 3) -> List[FundingRateData]:
        """
        Pobierz N najdrozszych symboli dla longow (najwyzszy dodatni funding).

        Przydatne dla strategii contrarian — unikaj longowania
        symboli z najwyzszym fundingiem.

        Args:
            n: Ile symboli zwrocic

        Returns:
            Lista FundingRateData z direction="longs_pay"
        """
        longs = [r for r in self._last_rates.values() if r.direction == "longs_pay"]
        longs.sort(key=lambda r: r.funding_rate, reverse=True)
        return longs[:n]

    def get_most_expensive_short(self, n: int = 3) -> List[FundingRateData]:
        """
        Pobierz N najdrozszych symboli dla shortow (najnizszy ujemny funding).

        Przydatne dla strategii contrarian — unikaj shortowania
        symboli z najbardziej ujemnym fundingiem.

        Args:
            n: Ile symboli zwrocic

        Returns:
            Lista FundingRateData z direction="shorts_pay"
        """
        shorts = [r for r in self._last_rates.values() if r.direction == "shorts_pay"]
        shorts.sort(key=lambda r: r.funding_rate)  # Najbardziej ujemny = pierwszy
        return shorts[:n]

    # ═════════════════════════════════════════════════════════════════════
    # STATS
    # ═════════════════════════════════════════════════════════════════════

    @property
    def stats(self) -> dict:
        """Statystyki monitora."""
        rates = list(self._last_rates.values())

        return {
            "symbols_monitored": len(self.symbols),
            "last_check": self._last_check,
            "last_check_ago": f"{int(time.time() - self._last_check)}s" if self._last_check else "never",
            "rates_cached": len(rates),
            "fetch_count": self._fetch_count,
            "error_count": self._error_count,
            "extreme_count": sum(1 for r in rates if r.severity == "extreme"),
            "elevated_count": sum(1 for r in rates if r.severity == "elevated"),
            "normal_count": sum(1 for r in rates if r.severity == "normal"),
            "thresholds": {
                "elevated": f">{self.thresholds['elevated'] * 100:.3f}%",
                "extreme": f">{self.thresholds['extreme'] * 100:.2f}%",
            },
        }
