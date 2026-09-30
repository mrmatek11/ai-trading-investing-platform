"""
Breaking News Monitor — monitoruje RSS feeds i wysyla alerty na Discord
═══════════════════════════════════════════════════════════════════════════

Komponenty:
  1. BreakingNews — dataclass z informacja o newsie
  2. NewsMonitor — glowna klasa monitorujaca RSS feeds

Logika:
  - Sprawdza RSS feeds co 15 minut
  - Filtruje po high-impact keywordach (breaking, crash, SEC, Fed, hack...)
  - Deduplikacja URL (OrderedDict, TTL 24h, max 500)
  - Discord embed: RED = breaking, ORANGE = important
  - Opcjonalnie: GLM AI ocenia wplyw newsa na otwarte pozycje
  - Fallback: xml.etree.ElementTree gdy feedparser niedostepny

Koszty: ZERO — tylko standard lib + requests + feedparser (darmowy)

Uzycie:
  from analysis.news_monitor import NewsMonitor, BreakingNews
  monitor = NewsMonitor()
  if monitor.should_check():
      news = monitor.check_feeds()
      for n in news:
          embed = monitor.format_news_discord(n)
          discord.send_custom_embed(embed)
"""

import time
import logging
import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from collections import OrderedDict

import requests

logger = logging.getLogger(__name__)

# Próbuje zaimportować feedparser — jeśli niedostępny, fallback do XML
try:
    import feedparser
    _HAS_FEEDPARSER = True
except ImportError:
    _HAS_FEEDPARSER = False
    logger.debug("feedparser niedostepny — fallback do xml.etree.ElementTree")


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class BreakingNews:
    """Pojedynczy breaking news z RSS feedu."""
    title: str
    source: str
    url: str
    published: str                  # Oryginalny string daty z RSS
    impact: str                     # "breaking", "high", "medium"
    keywords_matched: List[str]     # Ktore keywordy sie dopasowaly
    timestamp: float = 0.0          # Unix timestamp

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def impact_emoji(self) -> str:
        """Emoji zalezne od impact levelu."""
        return {
            "breaking": "\U0001f534",   # 🔴
            "high": "\U0001f7e0",       # 🟠
            "medium": "\U0001f7e1",     # 🟡
        }.get(self.impact, "\u26aa")    # ⚪

    @property
    def impact_color(self) -> int:
        """Kolor Discord embed zalezny od impact levelu."""
        return {
            "breaking": 0xFF0000,   # RED
            "high": 0xFF9800,       # ORANGE
            "medium": 0xFFEB3B,     # YELLOW
        }.get(self.impact, 0x9E9E9E)  # GREY


# ═══════════════════════════════════════════════════════════════════════════════
# NEWS MONITOR — MAIN CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class NewsMonitor:
    """
    Monitoruje RSS feeds pod katem breaking news finansowych/krypto.

    Pętla:
      1. Sprawdz co 15 min czy sa nowe artykuly
      2. Filtruj po high-impact keywordach
      3. Deduplikuj (URL + TTL 24h)
      4. Formatuj jako Discord embed
      5. Opcjonalnie: GLM AI ocenia wplyw na otwarte pozycje
    """

    # ─── RSS FEEDS ─────────────────────────────────────────────────────────
    RSS_FEEDS: Dict[str, str] = {
        "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "cointelegraph": "https://cointelegraph.com/rss",
        "reuters_business": "https://feeds.reuters.com/reuters/businessNews",
        "reuters_technology": "https://feeds.reuters.com/reuters/technologyNews",
    }

    # ─── KEYWORDY ──────────────────────────────────────────────────────────
    # Breaking — natychmiastowy alert (kryzysowe sytuacje)
    BREAKING_KEYWORDS: List[str] = [
        "breaking", "emergency", "crash", "hack", "ban",
        "war", "sanctions", "default", "bankruptcy",
    ]

    # High impact — wazne, ale nie kryzys
    HIGH_IMPACT_KEYWORDS: List[str] = [
        "regulation", "SEC", "Fed", "rate", "inflation",
        "recall", "suspend",
    ]

    # Medium — wartosciowe, ale mniej pilne
    MEDIUM_IMPACT_KEYWORDS: List[str] = [
        "ETF", "lawsuit", "investigation", "fine", "penalty",
        "crackdown", "restriction", "shutdown", "seize", "freeze",
        "halving", "fork", "delist", "insolvency", "bailout",
    ]

    # ─── KONFIGURACJA ──────────────────────────────────────────────────────

    def __init__(
        self,
        custom_feeds: Optional[Dict[str, str]] = None,
        check_interval: int = 900,             # 15 min
        seen_ttl: int = 86400,                 # 24h dedup
        seen_max: int = 500,                   # Max wpisow w dedup cache
        request_timeout: int = 15,             # Timeout na HTTP request
        user_agent: str = "Mozilla/5.0 (compatible; CryptoSignalBot/5.0)",
        glm_analyst=None,                      # Opcjonalnie: GLMAnalyst instance
    ):
        """
        Args:
            custom_feeds: Dodatkowe RSS feeds {name: url}
            check_interval: Co ile sekund sprawdzac (default 15 min)
            seen_ttl: TTL dla dedup cache w sekundach (default 24h)
            seen_max: Max wpisow w dedup cache (default 500)
            request_timeout: HTTP timeout w sekundach
            user_agent: User-Agent header dla HTTP requestow
            glm_analyst: Opcjonalny GLMAnalyst do oceny wplywu na pozycje
        """
        self.check_interval = check_interval
        self._seen_ttl = seen_ttl
        self._seen_max = seen_max
        self._request_timeout = request_timeout
        self._user_agent = user_agent
        self._glm_analyst = glm_analyst

        # Dedup cache: url_hash -> timestamp (OrderedDict z TTL)
        self._seen_urls: OrderedDict = OrderedDict()

        # Stan monitora
        self._last_check: float = 0

        # Statystyki
        self._total_checks = 0
        self._total_articles_found = 0
        self._total_alerts_sent = 0
        self._feed_errors: Dict[str, int] = {}   # feed_name -> error count

        # RSS feeds — domyslne + custom
        self._feeds: Dict[str, str] = dict(self.RSS_FEEDS)
        if custom_feeds:
            self._feeds.update(custom_feeds)

        # Prekompiluj regex dla keywordow (case-insensitive)
        self._breaking_pattern = re.compile(
            r'\b(' + '|'.join(re.escape(kw) for kw in self.BREAKING_KEYWORDS) + r')\b',
            re.IGNORECASE,
        )
        self._high_pattern = re.compile(
            r'\b(' + '|'.join(re.escape(kw) for kw in self.HIGH_IMPACT_KEYWORDS) + r')\b',
            re.IGNORECASE,
        )
        self._medium_pattern = re.compile(
            r'\b(' + '|'.join(re.escape(kw) for kw in self.MEDIUM_IMPACT_KEYWORDS) + r')\b',
            re.IGNORECASE,
        )

        logger.info(
            f"NewsMonitor: INIT | Feeds={len(self._feeds)} | "
            f"Interval={check_interval}s | TTL={seen_ttl}s | "
            f"feedparser={'YES' if _HAS_FEEDPARSER else 'NO (fallback XML)'}"
        )

    # ═════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═════════════════════════════════════════════════════════════════════

    def should_check(self) -> bool:
        """Czy juz czas na nastepne sprawdzenie RSS feeds?"""
        return time.time() - self._last_check >= self.check_interval

    def check_feeds(self) -> List[BreakingNews]:
        """
        Sprawdz wszystkie RSS feeds i zwroc nowe breaking news.

        Logika:
          1. Pobierz kazdy feed (z timeout i error handling)
          2. Parsuj artykuly (feedparser lub fallback XML)
          3. Filtruj po keywordach (breaking > high > medium)
          4. Deduplikuj (URL + TTL)
          5. Sortuj po impact (breaking first)

        Returns:
            Lista BreakingNews — tylko nowe, niefiltrowane wczesniej
        """
        self._total_checks += 1
        self._last_check = time.time()

        # Cleanup stare wpisy z dedup cache
        self._cleanup_seen_urls()

        all_news: List[BreakingNews] = []

        for feed_name, feed_url in self._feeds.items():
            try:
                articles = self._fetch_and_parse(feed_name, feed_url)
                for article in articles:
                    news = self._classify_article(article, feed_name)
                    if news and self._is_new(news):
                        all_news.append(news)
            except Exception as e:
                self._feed_errors[feed_name] = self._feed_errors.get(feed_name, 0) + 1
                logger.debug(f"NewsMonitor: feed error [{feed_name}]: {e}")
                continue

        # Sortuj: breaking first, potem high, potem medium
        impact_order = {"breaking": 0, "high": 1, "medium": 2}
        all_news.sort(key=lambda n: (impact_order.get(n.impact, 3), -n.timestamp))

        self._total_articles_found += len(all_news)
        if all_news:
            self._total_alerts_sent += len(all_news)
            logger.info(
                f"NewsMonitor: {len(all_news)} nowych alertow "
                f"(breaking={sum(1 for n in all_news if n.impact == 'breaking')}, "
                f"high={sum(1 for n in all_news if n.impact == 'high')}, "
                f"medium={sum(1 for n in all_news if n.impact == 'medium')})"
            )

        return all_news

    def add_feed(self, name: str, url: str) -> None:
        """Dodaj custom RSS feed do monitora."""
        self._feeds[name] = url
        logger.info(f"NewsMonitor: dodano feed '{name}' -> {url}")

    def remove_feed(self, name: str) -> bool:
        """Usun RSS feed z monitora. Zwraca True jesli usuniety."""
        if name in self._feeds:
            del self._feeds[name]
            logger.info(f"NewsMonitor: usuniety feed '{name}'")
            return True
        return False

    def format_news_discord(self, news: BreakingNews) -> Dict:
        """
        Formatuj pojedynczy news jako Discord embed.

        Kolory:
          - RED (0xFF0000) = breaking
          - ORANGE (0xFF9800) = high / important
          - YELLOW (0xFFEB3B) = medium
        """
        keywords_text = ", ".join(f"**{kw}**" for kw in news.keywords_matched)

        fields = [
            {
                "name": "Source",
                "value": news.source,
                "inline": True,
            },
            {
                "name": "Published",
                "value": news.published or "Unknown",
                "inline": True,
            },
            {
                "name": "Keywords",
                "value": keywords_text or "N/A",
                "inline": False,
            },
        ]

        # Link do artykulu
        if news.url:
            fields.append({
                "name": "Link",
                "value": f"[Read article]({news.url})",
                "inline": False,
            })

        embed = {
            "title": f"{news.impact_emoji} BREAKING: {news.title}" if news.impact == "breaking"
                     else f"{news.impact_emoji} {news.title}",
            "color": news.impact_color,
            "fields": fields,
            "footer": {
                "text": f"News Monitor | Impact: {news.impact.upper()}",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return embed

    def format_news_batch_discord(self, news_list: List[BreakingNews]) -> Optional[Dict]:
        """
        Formatuj batch newsow jako pojedynczy Discord embed.

        Przydatne gdy jest kilka alertow na raz — jeden embed
        zamiast spamu pojedynczych wiadomosci.

        Returns:
            Discord embed dict lub None jesli lista pusta
        """
        if not news_list:
            return None

        # Grupuj po impact
        breaking = [n for n in news_list if n.impact == "breaking"]
        high = [n for n in news_list if n.impact == "high"]
        medium = [n for n in news_list if n.impact == "medium"]

        fields = []

        if breaking:
            lines = []
            for n in breaking[:5]:   # Max 5 breaking
                lines.append(f"{n.impact_emoji} **{n.title}** [{n.source}]")
            fields.append({
                "name": f"\U0001f534 BREAKING ({len(breaking)})",
                "value": "\n".join(lines),
                "inline": False,
            })

        if high:
            lines = []
            for n in high[:5]:   # Max 5 high
                lines.append(f"{n.impact_emoji} **{n.title}** [{n.source}]")
            fields.append({
                "name": f"\U0001f7e0 HIGH IMPACT ({len(high)})",
                "value": "\n".join(lines),
                "inline": False,
            })

        if medium:
            lines = []
            for n in medium[:5]:   # Max 5 medium
                lines.append(f"{n.impact_emoji} {n.title} [{n.source}]")
            fields.append({
                "name": f"\U0001f7e1 MEDIUM ({len(medium)})",
                "value": "\n".join(lines),
                "inline": False,
            })

        # Kolor embed — najwyzszy impact decyduje
        if breaking:
            color = 0xFF0000       # RED
        elif high:
            color = 0xFF9800       # ORANGE
        else:
            color = 0xFFEB3B       # YELLOW

        embed = {
            "title": f"\U0001f4f0 Breaking News Monitor — {len(news_list)} alert(s)",
            "color": color,
            "fields": fields,
            "footer": {
                "text": f"News Monitor | {len(breaking)} breaking, {len(high)} high, {len(medium)} medium",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return embed

    async def evaluate_impact_on_positions(
        self,
        news: BreakingNews,
        open_positions: List[Dict[str, Any]],
    ) -> Optional[str]:
        """
        Opcjonalnie: GLM AI ocenia czy news wplywa na otwarte pozycje.

        Args:
            news: Breaking news
            open_positions: Lista otwartych pozycji z tracking/position_tracker

        Returns:
            AI assessment string lub None jesli GLM niedostepny
        """
        if not self._glm_analyst or not open_positions:
            return None

        # Buduj prompt z pozycjami
        positions_text = ""
        for pos in open_positions[:5]:
            positions_text += (
                f"- {pos.get('symbol', '?')} {pos.get('direction', '?')} "
                f"Entry: ${pos.get('entry_price', 0):,.2f} "
                f"PnL: ${pos.get('unrealized_pnl', 0):+,.2f}\n"
            )

        prompt = (
            f"Breaking news moze wplywac na otwarte pozycje:\n\n"
            f"NEWS: [{news.source}] {news.title}\n"
            f"Keywords: {', '.join(news.keywords_matched)}\n"
            f"Impact: {news.impact}\n\n"
            f"OTWARTE POZYCJE:\n{positions_text}\n"
            f"Czy ten news wplywa na ktoraś z pozycji? Odpowiedz krotko (1-2 zdania)."
        )

        try:
            messages = [
                {"role": "system", "content": "Jestes analitykiem tradingowym. Oceniasz wplyw newsow na otwarte pozycje krypto. Odpowiadaj krotko i konkretnie."},
                {"role": "user", "content": prompt},
            ]
            response = self._glm_analyst.client.chat(messages, temperature=0.2, max_tokens=200)
            return response
        except Exception as e:
            logger.debug(f"NewsMonitor: GLM evaluation error: {e}")
            return None

    @property
    def stats(self) -> Dict:
        """Statystyki monitora."""
        now = time.time()
        return {
            "feeds_count": len(self._feeds),
            "feeds": list(self._feeds.keys()),
            "check_interval": self.check_interval,
            "last_check_ago": int(now - self._last_check) if self._last_check else 0,
            "total_checks": self._total_checks,
            "total_articles_found": self._total_articles_found,
            "total_alerts_sent": self._total_alerts_sent,
            "seen_urls_cached": len(self._seen_urls),
            "seen_max": self._seen_max,
            "seen_ttl": self._seen_ttl,
            "feed_errors": dict(self._feed_errors),
            "has_feedparser": _HAS_FEEDPARSER,
            "has_glm_analyst": self._glm_analyst is not None,
        }

    # ═════════════════════════════════════════════════════════════════════
    # INTERNAL — RSS PARSING
    # ═════════════════════════════════════════════════════════════════════

    def _fetch_and_parse(
        self,
        feed_name: str,
        feed_url: str,
    ) -> List[Dict[str, str]]:
        """
        Pobierz i sparsuj RSS feed.

        Probuje feedparser, fallback do xml.etree.ElementTree.

        Returns:
            Lista dictow: {title, url, published, description}
        """
        # Pobierz XML z timeout
        try:
            resp = requests.get(
                feed_url,
                timeout=self._request_timeout,
                headers={"User-Agent": self._user_agent},
            )
            if resp.status_code != 200:
                logger.debug(f"NewsMonitor: [{feed_name}] HTTP {resp.status_code}")
                return []
        except requests.exceptions.Timeout:
            logger.debug(f"NewsMonitor: [{feed_name}] timeout")
            return []
        except requests.exceptions.ConnectionError:
            logger.debug(f"NewsMonitor: [{feed_name}] connection error")
            return []
        except Exception as e:
            logger.debug(f"NewsMonitor: [{feed_name}] fetch error: {e}")
            return []

        # Parsuj — feedparser lub fallback XML
        if _HAS_FEEDPARSER:
            return self._parse_feedparser(feed_name, resp.text)
        else:
            return self._parse_xml_fallback(feed_name, resp.text)

    def _parse_feedparser(
        self,
        feed_name: str,
        xml_text: str,
    ) -> List[Dict[str, str]]:
        """Parsuj RSS za pomoca feedparser (preferowany)."""
        articles = []

        try:
            feed = feedparser.parse(xml_text)

            # feedparser zwraca feed.bozo jezeli XML ma bledy
            if feed.bozo and not feed.entries:
                logger.debug(f"NewsMonitor: [{feed_name}] feedparser bozo, probuje fallback XML")
                return self._parse_xml_fallback(feed_name, xml_text)

            for entry in feed.entries[:30]:   # Max 30 artykulow na feed
                title = getattr(entry, "title", "").strip()
                link = getattr(entry, "link", "").strip()
                published = getattr(entry, "published", "") or getattr(entry, "updated", "")
                description = getattr(entry, "summary", "") or getattr(entry, "description", "")

                if not title:
                    continue

                articles.append({
                    "title": title,
                    "url": link,
                    "published": published,
                    "description": description[:500] if description else "",
                })

        except Exception as e:
            logger.debug(f"NewsMonitor: [{feed_name}] feedparser error: {e}, probuje fallback XML")
            return self._parse_xml_fallback(feed_name, xml_text)

        return articles

    def _parse_xml_fallback(
        self,
        feed_name: str,
        xml_text: str,
    ) -> List[Dict[str, str]]:
        """Fallback: parsuj RSS za pomoca xml.etree.ElementTree."""
        articles = []

        try:
            # Usun namespace declarations zeby uproscic parsowanie
            # (niektore feedy wstawiaja ns:dc, ns:content, itp.)
            clean_xml = re.sub(r'\sxmlns[^"]*"[^"]*"', '', xml_text)
            root = ET.fromstring(clean_xml)

            # RSS 2.0: <rss><channel><item>
            # Atom: <feed><entry>
            # Szukamy <item> lub <entry>

            # RSS 2.0
            items = root.iter("item")
            if not any(True for _ in root.iter("item")):
                # Sprobuj Atom
                items = root.iter("entry")

            for item_elem in items:
                title = (item_elem.findtext("title") or "").strip()
                link = self._extract_link(item_elem)
                pub_date = (
                    item_elem.findtext("pubDate")
                    or item_elem.findtext("published")
                    or item_elem.findtext("updated")
                    or ""
                )
                description = (
                    item_elem.findtext("description")
                    or item_elem.findtext("summary")
                    or item_elem.findtext("content")
                    or ""
                )

                if not title:
                    continue

                articles.append({
                    "title": title,
                    "url": link,
                    "published": pub_date,
                    "description": description[:500] if description else "",
                })

        except ET.ParseError as e:
            logger.debug(f"NewsMonitor: [{feed_name}] XML parse error: {e}")
        except Exception as e:
            logger.debug(f"NewsMonitor: [{feed_name}] XML fallback error: {e}")

        return articles[:30]   # Max 30 artykulow na feed

    @staticmethod
    def _extract_link(item_elem) -> str:
        """
        Wyciagnij link z elementu RSS/Atom.

        Atom: <link href="..."/> (atrybut)
        RSS: <link>text</link> (tekst)
        """
        # Najpierw sprawdz <link> jako tekst (RSS 2.0)
        link_elem = item_elem.find("link")
        if link_elem is not None:
            # Atom: link ma atrybut href
            href = link_elem.get("href")
            if href:
                return href.strip()
            # RSS: link ma tekst
            if link_elem.text:
                return link_elem.text.strip()

        return ""

    # ═════════════════════════════════════════════════════════════════════
    # INTERNAL — KLASYFIKACJA I DEDUP
    # ═════════════════════════════════════════════════════════════════════

    def _classify_article(
        self,
        article: Dict[str, str],
        feed_name: str,
    ) -> Optional[BreakingNews]:
        """
        Klasyfikuj artykul po keywordach.

        Szuka keywordow w title i description.
        Priorytet: breaking > high > medium.
        Zwraca None jesli zaden keyword nie pasuje.
        """
        # Polacz title + description do wyszukiwania
        search_text = f"{article['title']} {article.get('description', '')}"

        # Szukaj breaking keywords (najwyzszy priorytet)
        breaking_matches = self._breaking_pattern.findall(search_text)
        if breaking_matches:
            return BreakingNews(
                title=article["title"],
                source=feed_name,
                url=article.get("url", ""),
                published=article.get("published", ""),
                impact="breaking",
                keywords_matched=list(set(kw.lower() for kw in breaking_matches)),
            )

        # Szukaj high-impact keywords
        high_matches = self._high_pattern.findall(search_text)
        if high_matches:
            return BreakingNews(
                title=article["title"],
                source=feed_name,
                url=article.get("url", ""),
                published=article.get("published", ""),
                impact="high",
                keywords_matched=list(set(kw.lower() for kw in high_matches)),
            )

        # Szukaj medium-impact keywords
        medium_matches = self._medium_pattern.findall(search_text)
        if medium_matches:
            return BreakingNews(
                title=article["title"],
                source=feed_name,
                url=article.get("url", ""),
                published=article.get("published", ""),
                impact="medium",
                keywords_matched=list(set(kw.lower() for kw in medium_matches)),
            )

        return None

    def _is_new(self, news: BreakingNews) -> bool:
        """
        Sprawdz czy news jest nowy (nie byl juz wyslany).

        Dedup po URL (hash MD5) z TTL 24h.
        OrderedDict trzyma max 500 wpisow.
        """
        # Klucz dedup — hash URL + title (URL moze byc pusty u niektorych feedow)
        dedup_key = hashlib.md5(
            f"{news.url}:{news.title}".encode()
        ).hexdigest()

        if dedup_key in self._seen_urls:
            return False

        # Dodaj do seen
        self._seen_urls[dedup_key] = time.time()

        # Trim jezeli za duzo
        while len(self._seen_urls) > self._seen_max:
            self._seen_urls.popitem(last=False)

        return True

    def _cleanup_seen_urls(self) -> None:
        """
        Usuń stare wpisy z dedup cache (TTL).
        
        FIX: oryginał zakładał że OrderedDict jest posortowany po timestamp value
        i używał `break` przy pierwszym nie-wygasłym wpisie — BŁĄD.
        
        Poprawka: iteruj przez wszystkie wpisy i usuń każdy wygasły.
        Nie używaj `break` — czas wstawienia != timestamp value.
        """
        now = time.time()
        expired_keys = [
            key for key, ts in self._seen_urls.items()
            if now - ts > self._seen_ttl
        ]
        for key in expired_keys:
            del self._seen_urls[key]
        
        if expired_keys:
            logger.debug(f"NewsMonitor: cleaned {len(expired_keys)} expired dedup entries")
