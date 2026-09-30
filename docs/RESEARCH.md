# Research: pomysły, problemy traderów, luki w konkurencji

Żywy dokument — dopisuj tu nowe pomysły i obserwacje. Stan: 2026-09-28.

> **O źródłach.** Reddit blokuje automatyczne wyszukiwanie, więc ta wersja opiera się na recenzjach
> (Trustpilot, porównywarki journali), forum ForexFactory i materiałach prop firm — tam powtarzają się
> te same skargi co na r/Daytrading, r/Forex i r/Trading. Wklejaj tu wątki z Reddita, na które trafisz —
> dopiszę je do tabeli z linkiem.

## 1. Twoje pomysły (log)

| # | Pomysł | Status | Gdzie |
|---|--------|--------|-------|
| 1 | Terminal w stylu Bloomberga | Zaplanowany (faza 3), makieta | `PRODUCT_SPEC.md` 2.3 |
| 2 | Trading journal | **W budowie** (faza 1) | `tape/` |
| 3 | Portfolio analyser | Zaplanowany (faza 2) | 2.2 |
| 4 | Platforma botów AI | Zaplanowana (faza 4), laboratorium strategii gotowe | 2.4, `research/strategy_lab.py` |
| 5 | Jeden SaaS łączący wszystko | Specyfikacja + szkielet | `PRODUCT_SPEC.md`, `tape/` |
| 6 | Import z brokerów | **W budowie**: XTB, MT5, uniwersalny CSV | 3, `tape/backend/tape/importers/` |
| 7 | Minimalistyczny design | Makieta + tokeny w aplikacji | 5, makieta |
| 8 | Skupienie na złocie i srebrze | Przyjęte jako główny rynek | 1 |
| 9 | Globus 3D ze zdarzeniami (wojny, banki centralne, makro, newsy) | **W budowie** (globe.gl) | 2.5, `tape/web` |
| 10 | AI: analiza newsów → long / short | **Pipeline w trybie shadow gotowy** (GDELT → Claude → log ocen → trafność) | 4.6, `tape/backend/tape/news/` |
| 11 | Przetestować strategię na złocie i kilka innych | **Zrobione** | `BACKTEST_RESULTS.md` |

## 2. Problemy ludzi i odpowiedź Tape

| Problem | Dowód | Odpowiedź w Tape |
|---------|-------|------------------|
| **Synchronizacja z brokerem się psuje, importy są błędne** | TraderSync: błędy i problemy z importem to najczęstsze skargi (po 36%) [2]; w Tradezella 1-gwiazdkowe recenzje najczęściej dotyczą syncu — m.in. złożonego Flex Query w IBKR [1] | Raport importu, rekoncyliacja sald z brokerem, testy parserów na prawdziwych plikach, **kreator IBKR Flex krok po kroku** |
| **Ręczny import CSV to strata czasu** | Edgewonk: import tylko z plików, brak auto-syncu [4] | EA dla MT5 (real-time), API tam, gdzie się da, szablony mapowania |
| **Transakcje gubione przy złożonych zleceniach** | TraderSync gubi zamykające nogi złożonych transakcji [2] | Fill-e jako źródło prawdy, pozycje zawsze przeliczalne od zera |
| **Drogo, bez triala, bez zwrotów** | Tradezella: skargi na brak triala i zwrotów [1]; TraderSync: od $30 do prawie $1000 rocznie za pełnię [5] | Darmowy plan + 14 dni Pro, zwrot w 14 dni, uczciwa cena |
| **Za dużo funkcji, trudny start** | TraderSync: „dużo do ogarnięcia na początku” [5] | Minimalistyczny UI, jeden ekran = jedno pytanie, onboarding: połącz konto → pierwszy wniosek |
| **AI odpowiada tylko na to, o co już podejrzewasz** | Insighty AI działają, gdy użytkownik sam zada właściwe pytanie [5] | Kod sam szuka istotnych segmentów (t-stat) i proponuje wnioski |
| **Prop firmy: konta padają na regułach, nie na strategii** | Większość oblanych challenge'y to złamana reguła, najczęściej dzienny drawdown [6] | Tracker reguł prop firm z alertami, **symulator: trailing vs statyczny drawdown** |
| **Złoto traktowane jak EUR/USD** | Traderzy przenoszą sizing z EUR/USD na złoto o dużo większym dziennym zasięgu [6] | **Kalkulator wielkości pozycji dla złota** oparty o ATR / dzienny zasięg i limit dzienny prop firmy |
| **Zakazy handlu wokół newsów** | Część firm zabrania otwierania pozycji ±kilka minut wokół ważnych publikacji [6] | Alert „okno newsowe” z kalendarza, per reguły Twojej firmy |
| **Bloomberg za drogi, a detalista potrzebuje 3–4 funkcji** | Typowy zestaw zamienników kosztuje $0–150 / mies. [3] | Journal + news + kalendarz + terminal metali w jednym planie |

## 3. Luki w konkurencji

- Nikt nie łączy **journala z kontekstem specyficznym dla metali**: realne rentowności, DXY, COT, przepływy ETF, relacja złoto/srebro.
- Brak **globusa zdarzeń** połączonego z Twoimi transakcjami („co działo się na świecie, gdy otwierałem pozycję”).
- Brak **uczciwego AI** z t-statem i track recordem. Konkurencja pokazuje wnioski bez informacji o istotności.
- Brak obsługi **polskich brokerów i PIT-38** u globalnych graczy.

## 4. Backlog pomysłów z researchu

1. ✅ Kalkulator pozycji dla złota (ATR, dzienny zasięg, dzienny limit prop firmy) — szybki, darmowy lead magnet.
2. ✅ Symulator reguł prop firm (trailing vs statyczny DD, dzienny limit) na Twojej historii: „na którym koncie przeżyłbyś ten miesiąc?”.
3. Alert „okno newsowe” zsynchronizowany z kalendarzem i regułami firmy.
4. Kreator IBKR Flex Query z gotowym szablonem zapytania.
5. Panel „zdrowie importu”: ostatni sync, rozbieżności sald, brakujące dni.
6. London breakout na złocie jako pierwszy kandydat do dalszych badań (patrz `BACKTEST_RESULTS.md`).

## Źródła

1. [TradeZella — Trustpilot](https://www.trustpilot.com/review/tradezella.com), [TradeZella Review 2026 — Traders Second Brain](https://traderssecondbrain.com/guides/tradezella-review), [Tradezella Review — FinancialTechWiz](https://www.financialtechwiz.com/post/tradezella-review/)
2. [TraderSync — Trustpilot](https://www.trustpilot.com/review/tradersync.com), [TraderSync Review — StockBrokers.com](https://www.stockbrokers.com/review/tools/tradersync), [TraderSync Review — Tradespad](https://tradespad.com/blog/tradersync-review), [TraderSync Review — TradeZully](https://tradezully.com/blog/tradersync-reviews-and-alternative)
3. [Best Bloomberg Terminal Alternatives — Helm Terminal](https://helmterminal.dev/blog/best-bloomberg-terminal-alternatives), [WallStreetZen](https://www.wallstreetzen.com/blog/bloomberg-terminal-alternatives/), [Benzinga](https://www.benzinga.com/investing/best-alternatives-to-bloomberg-terminal)
4. [Edgewonk Review — Bullish Bears](https://bullishbears.com/edgewonk-review/), [Edgewonk alternative — Traders Second Brain](https://traderssecondbrain.com/guides/edgewonk-alternative), [PipJournal: MT4/MT5 import](https://pipjournal.ai/best/best-trading-journal-with-mt4-mt5-import/)
5. [TraderSync Review — Traders Second Brain](https://traderssecondbrain.com/guides/tradersync-review), [AI Trading Journal — JournalX](https://www.journalx.io/blog/ai-trading-journal)
6. [Daily Drawdown Rules — For Traders](https://fortraders.com/blog/daily-drawdown-rules-how-to-stay-within-limits), [Prop Firm Rules Explained — Velotrade](https://velotrade.com/blog/prop-firm-rules-explained), [Best Prop Firms for Gold — FXNX](https://fxnx.com/en/blog/best-prop-firms-gold-trading-mastering-xauusd-volatility)
7. [ForexFactory: What is your favourite trading journal?](https://www.forexfactory.com/thread/1261626-what-is-your-favourite-trading-journal), [TradesViz MT5](https://www.tradesviz.com/brokers/MetaTrader5)
