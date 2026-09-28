# Tape — specyfikacja produktu

> **Tape** to nazwa robocza (od *reading the tape*). Jedno miejsce dla aktywnego tradera:
> **terminal rynkowy, trading journal, analiza portfela i platforma botów — spięte warstwą AI,
> która zna Twoje transakcje.**
>
> Dokument startowy dla **osobnego repo**. Bot sygnałowy z tego repo zostaje osobnym projektem;
> Tape reużywa z niego wybrane moduły (sekcja 11), ale **nie** `neural_weight_oscillator.py`
> (licencja CC BY-NC-SA 4.0 zabrania użycia komercyjnego).

## Spis treści

1. [Wizja i pozycjonowanie](#1-wizja-i-pozycjonowanie)
2. [Moduły](#2-moduły)
3. [Import z brokerów i giełd](#3-import-z-brokerów-i-giełd)
4. [Warstwa AI](#4-warstwa-ai)
5. [Design](#5-design)
6. [Stack technologiczny](#6-stack-technologiczny)
7. [Architektura](#7-architektura)
8. [Model danych](#8-model-danych)
9. [Bezpieczeństwo](#9-bezpieczeństwo)
10. [Prawo i podatki](#10-prawo-i-podatki)
11. [Co przenieść z bota](#11-co-przenieść-z-bota)
12. [Monetyzacja](#12-monetyzacja)
13. [Roadmapa](#13-roadmapa)
14. [Ryzyka i otwarte pytania](#14-ryzyka-i-otwarte-pytania)

---

## 1. Wizja i pozycjonowanie

### Problem

Aktywny trader detaliczny ma dziś 5–8 narzędzi: TradingView do wykresów, arkusz albo płatny
journal do transakcji, aplikacje brokerów do sald, osobny kalkulator PIT, Discord z sygnałami,
czasem 3Commas do botów. Żadne z nich nie wie, co robią pozostałe, więc nikt nie odpowiada na
najważniejsze pytanie: **w jakich warunkach ja konkretnie zarabiam, a w jakich tracę?**

### Rozwiązanie

Jedna aplikacja, w której **dane o Twoich transakcjach są centrum**, a wszystko inne je wzbogaca:

```
                ┌───────────── Terminal ─────────────┐
                │  rynek teraz: ceny, news, makro,   │
                │  funding, reżim, kalendarz         │
                └──────────────┬─────────────────────┘
                               │ kontekst rynkowy każdej transakcji
┌──────────────┐     ┌─────────▼─────────┐     ┌──────────────────┐
│  Import      │────▶│  Journal          │────▶│  Portfolio       │
│  (brokerzy,  │     │  (Twoje transakcje│     │  (ekspozycja,    │
│  giełdy, CSV)│     │  + kontekst)      │     │  ryzyko, PIT)    │
└──────────────┘     └─────────┬─────────┘     └──────────────────┘
                               │ Twój udokumentowany edge
                     ┌─────────▼─────────┐
                     │  Boty             │
                     │  (backtest →      │
                     │  paper → live)    │
                     └───────────────────┘
          AI: czyta wszystko powyżej, odpowiada liczbami z Twoich danych
```

### Wyróżniki (dlaczego nie TradingView + Tradezella + 3Commas)

1. **Zero ręcznej pracy.** Import z API brokerów i giełd, pliki od polskich brokerów, EA dla MT4/MT5.
2. **AI, które liczy na Twoich danych, a nie ogólnikach.** „Transakcje otwarte w 30 min po stracie:
   41 szt., −0,45R średnio, −1 870 USD” zamiast „pamiętaj o zarządzaniu emocjami”.
3. **Kontekst rynkowy przy każdej transakcji.** Reżim, funding, sesja, eventy makro w momencie wejścia.
   Pozwala odpowiedzieć, *kiedy* Twój edge działa.
4. **Rynek polski.** Import z XTB, mBanku, Bossy, rozliczenie PIT-38 z kursem NBP. Globalni gracze tego nie robią.
5. **Boty tylko na udokumentowanym edge'u.** Strategię uruchamiasz dopiero po backteście z walk-forwardem i paper tradingu.

### Dla kogo (kolejność)

1. **Aktywni traderzy crypto** (perpy, spot) — dane przez API są darmowe i dostępne, a społeczność gotowa (Discord bota).
2. **Traderzy prop firm** (MT5, cTrader, TradeLocker) — płacą za narzędzia i muszą pilnować reguł drawdownu.
3. **Polscy inwestorzy giełdowi** (XTB, IBKR, mBank) — portfolio i PIT-38.

---

## 2. Moduły

### 2.1 Journal (rdzeń — faza 1)

| Funkcja | Opis |
|---------|------|
| Auto-import | Sekcja 3. Fill-e → pozycje (FIFO, częściowe zamknięcia, fees, funding, swap). |
| Widok transakcji | Wykres z zaznaczonym wejściem, wyjściem, SL i TP. Replay świec od wejścia. Oś czasu fill-i. |
| R-multiple | Z initial SL. Gdy SL nie jest znany: pytanie do użytkownika albo szacunek z ATR, oznaczony jako szacunek. |
| Playbooki | Zdefiniowane setupy z checklistą reguł. Każdą transakcję przypisujesz do setupu i odhaczasz reguły. |
| Taksonomia błędów | Tagi typu FOMO, przesunięty SL, za duży size, wejście przed newsem. Statystyki kosztu każdego błędu. |
| Notatki i screenshoty | Markdown i obrazy (paste ze schowka). |
| Kalendarz PnL | Heatmapa dni, klik otwiera dzień. |
| Daily / weekly review | Szablon + AI review (sekcja 4). |
| Reguły prop firm | Limity dziennego i całkowitego DD, profit target, min. dni tradingowe — z alertem przy zbliżaniu się. |

**Statystyki:** net PnL, win rate, avg R, expectancy, profit factor, max DD, średni czas trzymania,
MAE/MFE (z danych świecowych), rozkład R, segmentacja po: setupie, symbolu, kierunku, godzinie,
dniu tygodnia, sesji, reżimie rynku, „po stracie / po wygranej”, wielkości pozycji.
Przy każdej różnicy między segmentami pokazywany jest **t-stat**. Mała próba jest oznaczona i nie udaje wniosku.

### 2.2 Portfolio analyser (faza 2)

| Funkcja | Opis |
|---------|------|
| Konsolidacja | Wszystkie konta (brokerzy, giełdy, portfele on-chain) w walucie bazowej użytkownika. |
| Alokacja i ekspozycja | Klasa aktywów, sektor, region, waluta. Net i gross exposure, lewar efektywny. |
| Wyniki | **TWR** (ocena strategii, bez wpływu wpłat) i **MWR/IRR** (Twój realny wynik). Benchmark: S&P 500, WIG, BTC. |
| Ryzyko | Zmienność, beta, korelacje (macierz), VaR/CVaR historyczny, max DD, koncentracja (top 5 pozycji). |
| Koszty | Suma prowizji, spreadów, fundingu, swapów, podatku u źródła. Ile kosztuje Cię obracanie portfelem. |
| Dywidendy i odsetki | Kalendarz, podatek u źródła, zwrot z pozycji z dywidendami. |
| Scenariusze | „BTC −30%”, „USD/PLN +10%” — wpływ na portfel. |
| **PIT-38** | Sekcja 10. Raport roczny z pozycjami do formularza, eksport PDF i CSV. |

### 2.3 Terminal (faza 3)

Minimalistyczny, sterowany klawiaturą, w stylu Bloomberga, ale bez jego chaosu.

- **Linia poleceń** (zawsze na górze, `/` lub `⌘K`): `<symbol> <funkcja>`
  - `BTC GP` wykres · `BTC DES` opis/fundamenty · `ETH NEWS` newsy · `SOL FUND` funding i OI
  - `ECO` kalendarz makro · `WEI` indeksy świata · `CORR BTC ETH SOL` korelacje · `HEAT` heatmapa rynku
  - `JRNL SOL` Twoje transakcje na SOL · `ASK <pytanie>` AI
- **Workspace'y:** dokowalne panele (wykres, watchlista, news, order book, kalendarz, pozycje),
  zapisywane układy, synchronizacja symbolu między panelami (grupy kolorów jak w Bloombergu).
- **Dane:** crypto real-time (websockety giełd), akcje i FX z licencjonowanego dostawcy (sekcja 6.6),
  news z AI-streszczeniem i oceną wpływu, kalendarz makro, funding, OI, liquidations, F&G.
- **Alerty:** cena, zmienność, funding, news o symbolu z Twojego portfela, poziom S/R.
- **Twoje transakcje na wykresie:** każdy wykres pokazuje Twoje historyczne wejścia i wyjścia.

### 2.4 Platforma botów (faza 4)

| Etap | Opis |
|------|------|
| Builder | Reguły wizualnie (warunki → akcja → zarządzanie pozycją) albo Python w sandboxie. Obie formy kompilują się do tego samego silnika. |
| Backtest | Silnik jak `backtest.py` z bota: wejście na następnym open, fees, slippage, SL-first przy konflikcie, walk-forward, t-stat. |
| Paper trading | Na żywych danych, min. N dni lub M transakcji, zanim live się odblokuje. |
| Live | Klucze z uprawnieniem **trade, bez withdraw**. Twarde limity: max dzienna strata, max pozycja, kill switch, auto-stop przy odchyleniu od backtestu. |
| Monitoring | Porównanie live vs backtest vs paper. Każda transakcja bota trafia do journala jak ręczna. |

**Nie robimy:** marketplace'u strategii ani copy tradingu. To regulowana działalność, a przy tym magnes na scamy.

---

## 3. Import z brokerów i giełd

Import to **najważniejsza funkcja produktu**. Jeśli nie działa perfekcyjnie, nic innego nie ma znaczenia.

### 3.1 Trzy sposoby połączenia

| Typ | Jak | Aktualizacja | Dla kogo |
|-----|-----|--------------|----------|
| **API** | Klucz read-only albo OAuth | Automatycznie, co kilka minut | Giełdy crypto, IBKR, Saxo, cTrader, US brokerzy |
| **Plik** | Upload CSV / XLSX / XML / HTML / PDF | Ręcznie (albo mail-in) | Polscy brokerzy, Degiro, eToro, Revolut, MT4/MT5 statement |
| **Push** | Nasz Expert Advisor wysyła transakcje | Real-time | MetaTrader 4/5 (prop firmy) |

### 3.2 Pokrycie — priorytety

> ⚠️ Stan API brokerów zmienia się często. Każdą pozycję **zweryfikować przed implementacją**
> (dokumentacja, regulamin API, limity historii).

**Giełdy crypto (API, przez `ccxt` + natywne endpointy tam, gdzie ccxt nie wystarcza)**

| Giełda | Priorytet | Uwagi |
|--------|-----------|-------|
| Binance (spot, USDⓈ-M, COIN-M) | P0 | Historia transakcji spot wymaga zapytań per symbol. Starsza historia z eksportu CSV. Sprawdzamy flagi uprawnień klucza. |
| Bybit (Unified) | P0 | Endpoint historii egzekucji ma limit wstecz — starsze dane z CSV. |
| OKX, Bitget | P1 | |
| Hyperliquid | P1 | Publiczne API po adresie portfela — **bez klucza**. |
| Kraken, Coinbase Advanced, KuCoin, Gate, MEXC | P2 | |
| Portfele on-chain (EVM, Solana) | P3 | Po adresie przez indexer (salda, transfery, swapy DEX). |

**Brokerzy — API**

| Broker | Metoda | Priorytet | Uwagi |
|--------|--------|-----------|-------|
| Interactive Brokers (i Lynx) | **Flex Web Service** (token + Flex Query ID, XML) | P0 | Read-only z natury, pełna historia (zapytania w oknach). Najlepsze źródło danych w branży. |
| Trading 212 | Publiczne API (klucz) | P1 | Zweryfikować zakres (Invest / ISA; CFD?) i status beta. |
| Saxo | OpenAPI (OAuth) | P2 | |
| cTrader | Open API (OAuth) | P1 | Wiele prop firm. |
| TradeLocker, Match-Trader, DXtrade | API platform | P2 | Prop firmy; dostęp często przez firmę, a nie tradera. |
| Alpaca, Tastytrade, Schwab, Tradier | API | P3 | Rynek US. |
| **Agregator** (np. SnapTrade) | Jedno API → wiele brokerów | P2 | Szybkie pokrycie US/CA i części EU. Płatne per użytkownik; sprawdzić listę brokerów EU. |

**Brokerzy — pliki**

| Broker | Plik | Priorytet | Uwagi |
|--------|------|-----------|-------|
| XTB | Eksport z xStation (zamknięte pozycje, operacje gotówkowe; XLSX) | P0 | Publiczne API XTB zostało wyłączone — zweryfikować aktualny stan. Największy broker detaliczny w PL. |
| mBank eMakler | Historia transakcji (CSV) | P1 | |
| Bossa (DM BOŚ) | Historia transakcji (CSV) | P1 | |
| Degiro | `Transactions.csv`, `Account.csv` | P1 | |
| eToro | Account statement (XLSX) | P2 | Sprawdzić też oficjalne API. |
| Revolut | Wyciąg trading (CSV) | P2 | |
| MetaTrader 4 / 5 | Detailed Statement (HTML) / Report (XLSX) | P1 | Uzupełnienie EA (sekcja 3.1) dla historii. |
| Potwierdzenia transakcji (PDF z maila) | Mail-in: `import-<id>@tape.app` | P3 | Parsowanie PDF przez Claude + walidacja. |

**Import uniwersalny (każdy CSV/XLSX):**
1. Użytkownik wgrywa plik.
2. AI dostaje nagłówki i ~20 przykładowych wierszy i zwraca **mapowanie kolumn** jako JSON wg schematu:
   kolumna → pole kanoniczne, format daty, strefa czasowa, separator dziesiętny, znak strony transakcji.
3. Podgląd pierwszych 50 wierszy po mapowaniu. Użytkownik poprawia i zatwierdza.
4. Mapowanie zapisuje się jako **szablon** dla tego brokera. Po weryfikacji przez nas trafia do biblioteki dla wszystkich
   — pokrycie brokerów rośnie samo.

### 3.3 Pipeline

```
Connector ──▶ raw_events ──▶ Normalizer ──▶ transactions ──▶ Position engine ──▶ positions/lots
(API/plik/    (niemutowalne,  (per źródło)   (kanoniczne)     (FIFO, multi-FX)     │
 push)         JSONB + hash)                      │                               ▼
                                                  └──▶ Instrument resolver   Reconciliation
                                                      (ISIN/FIGI/symbol)    (salda vs broker)
```

1. **Connector** — pobiera dane i zapisuje je surowo (`raw_events`) razem ze źródłem i hashem treści. Nic nie jest tracone ani nadpisywane.
2. **Normalizer** (osobny dla każdego źródła) — mapuje na **kanoniczne typy**:
   `trade_fill`, `fee`, `funding`, `swap`, `dividend`, `withholding_tax`, `interest`,
   `deposit`, `withdrawal`, `transfer`, `fx_conversion`, `corporate_action` (split, spin-off, zmiana tickera).
3. **Instrument resolver** — ISIN i FIGI (OpenFIGI) dla papierów, mapa symboli dla crypto,
   mnożniki kontraktów dla futures i CFD, waluta notowania.
4. **Position engine** — FIFO na lotach (wymóg PIT dla papierów wartościowych) oraz średni koszt do widoku.
   Obsługuje shorty, częściowe zamknięcia, odwrócenia pozycji i trzy waluty:
   instrumentu, konta i bazową użytkownika.
5. **Reconciliation** — porównuje wyliczone salda z saldami raportowanymi przez brokera.
   Rozbieżność = brakujące dane. Pokazujemy ją użytkownikowi zamiast po cichu liczyć złe PnL.

**Zasady:**
- **Idempotencja:** klucz `(source_account, external_id)`, a gdy plik nie ma ID — hash
  `(czas, symbol, strona, ilość, cena, n-ty duplikat)`. Ponowny import tego samego pliku nie tworzy duplikatów.
- **Czas:** zawsze UTC plus zapisana strefa źródła. Polscy brokerzy eksportują czas lokalny, z przejściami DST.
- **Precyzja:** `NUMERIC` / `Decimal` wszędzie. Nigdy `float` dla pieniędzy.
- **Raport importu:** „312 nowych, 40 duplikatów, 2 błędy (wiersze 118, 204: nieznany format daty)”.
- **Testy:** każdy parser ma testy na **prawdziwych, zanonimizowanych eksportach**.
  Użytkownik może wysłać problematyczny plik jednym kliknięciem (za zgodą) — trafia do zestawu testów.

### 3.4 MetaTrader EA („Tape Sync”)

- EA (MQL5 i MQL4) na wykresie wysyła nowe deale przez `WebRequest` do naszego API z tokenem konta.
- Działa na każdym koncie MT, w tym u prop firm, bez udostępniania hasła inwestora.
- Przy starcie wysyła całą dostępną historię, potem przyrostowo. Idempotencja po ticket ID.
- Alternatywa bez EA: zewnętrzny agregator MT (płatny, wymaga hasła inwestora) — tylko jeśli EA okaże się barierą.

---

## 4. Warstwa AI

### 4.1 Zasada nr 1: AI interpretuje, kod liczy

LLM **nigdy** nie liczy statystyk ani nie pisze dowolnego SQL. Robi to deterministyczny kod,
a model dostaje gotowe liczby i narzędzia o wąskim zakresie. Każda liczba w odpowiedzi AI jest
**walidowana** względem danych wejściowych. Jeśli jej tam nie ma, odpowiedź zostaje odrzucona i wygenerowana ponownie.

### 4.2 Funkcje

| Funkcja | Jak działa |
|---------|------------|
| **Weekly review** | Kod liczy segmenty i wybiera istotne statystycznie odchylenia (min. N, t-stat). AI dostaje tabelę i zwraca max 3 wnioski jako JSON wg schematu: `{finding, evidence[], impact, suggestion}`. |
| **Ask your trades** (czat) | Tool use na wąskich, read-only funkcjach: `get_trades(filtr)`, `get_stats(group_by, filtr)`, `get_market_context(czas, symbol)`, `get_portfolio()`. Model sam składa odpowiedź z wywołań. |
| **Pre-trade check** | „Chcę shortować SOL teraz” → Twoje statystyki dla podobnych warunków (symbol, kierunek, sesja, reżim) + aktualny kontekst. Analiza historii, **nie** rekomendacja. |
| **Mapowanie importu** | Sekcja 3.2. Structured output, człowiek zatwierdza. |
| **Klasyfikacja** | Tagowanie transakcji, kategoryzacja operacji z wyciągów, streszczenia newsów z oceną wpływu na portfel. |
| **Parsowanie PDF** | Potwierdzenia transakcji z maila → kanoniczne transakcje. Walidacja sum kontrolnych. |

### 4.3 Implementacja (Claude API)

- **Model:** `claude-opus-5-5` jako domyślny dla wszystkich funkcji. Tańsze modele
  (`claude-sonnet-5-5`, `claude-haiku-4-5`) do rozważenia dla klasyfikacji i mapowania,
  **wyłącznie po pomiarze jakości** na zestawie testowym (sekcja 4.4) — to decyzja kosztowa, nie domyślna.
- **Structured outputs** (`output_config.format` z JSON Schema / `client.messages.parse()` z Pydantic)
  dla review, mapowań i klasyfikacji — zero parsowania tekstu.
- **Tool use** z `strict: true` na narzędziach czatu. Pętla przez Tool Runner z SDK;
  każde narzędzie z filtrem `user_id` wymuszanym po stronie serwera, a nie przez model.
- **Prompt caching** — stały system prompt i definicje narzędzi na początku, profil statystyk
  użytkownika jako drugi blok cache, pytanie na końcu. Weryfikacja przez `usage.cache_read_input_tokens`.
- **Batch API** dla tygodniowych review: generowane w nocy, asynchronicznie, ~50% taniej.
- **Effort:** `output_config.effort` ustawiany jawnie per funkcja (np. `low` dla klasyfikacji,
  `high` dla review) i dostrojony pomiarem.
- **Streaming** w czacie.
- **Obsługa `stop_reason`:** `refusal`, `max_tokens` → komunikat w UI zamiast pustej odpowiedzi.
- **Prywatność:** do modelu trafiają transakcje i statystyki, **nigdy** klucze API, e-maile ani dane osobowe.

### 4.4 Jakość

- Zestaw ewaluacyjny: 50–100 prawdziwych (zanonimizowanych) historii użytkowników z oczekiwanymi wnioskami.
- Metryki: odsetek odpowiedzi z liczbą spoza danych (cel 0%), trafność wniosków (ocena ręczna), koszt per review.
- Każda zmiana promptu lub modelu → przejście przez ewaluację przed wdrożeniem.

### 4.5 Ton

AI mówi jak rzetelny analityk: liczby, próba, niepewność. Nigdy „kup” ani „sprzedaj”.
Stała stopka przy wnioskach: *analiza Twoich historycznych danych, nie rekomendacja inwestycyjna.*

---

## 5. Design

### 5.1 Zasady

> Makieta (4 ekrany, klikalny prototyp, przełącznik palety dla daltonistów):
> https://claude.ai/artifact/G3qikykCs35xRkiZRBc9ch


1. **Dane najpierw, interfejs na końcu.** Brak dekoracji, gradientów i ilustracji w aplikacji. Każdy piksel pokazuje dane albo prowadzi do działania.
2. **Kolor = znaczenie.** UI jest monochromatyczne. Kolor pojawia się tylko dla: zysku, straty, jednego akcentu (fokus i akcja główna) oraz ostrzeżenia.
3. **Liczby są pierwszoplanowe.** Cyfry tabelaryczne (`tnum`), wyrównanie do prawej, zawsze ten sam format (separator tysięcy, stała liczba miejsc w kolumnie).
4. **Klawiatura przede wszystkim.** `⌘K` wszędzie, skróty `g j` (journal), `g p` (portfolio), `g t` (terminal), `j/k` po listach, `/` szukaj. Każda akcja ma skrót.
5. **Gęstość do wyboru.** Tryb *comfortable* (domyślnie w journalu) i *compact* (domyślnie w terminalu).
6. **Jeden ekran, jedno pytanie.** Dashboard odpowiada na „jak mi idzie”, transakcja na „co się stało”, import na „czy dane są kompletne”.
7. **Puste stany prowadzą.** Zamiast pustej tabeli: jedno zdanie i jeden przycisk („Połącz pierwsze konto”).

### 5.2 Tokeny

```css
/* Dark (domyślny) */
--bg:          #0A0A0B;   --surface:   #111113;   --surface-2: #18181B;
--border:      #26262A;   --text:      #EDEDEF;   --text-muted:#8A8A93;
--accent:      #6E8BFF;   --positive:  #2FBF71;   --negative:  #F2555A;   --warning: #E5A93B;

/* Light */
--bg:          #FFFFFF;   --surface:   #FAFAFA;   --surface-2: #F3F3F4;
--border:      #E6E6E8;   --text:      #0A0A0B;   --text-muted:#696972;
--accent:      #3553E6;   --positive:  #0F8F4E;   --negative:  #D0303A;   --warning: #B7791F;

/* Tryb dla daltonistów (przełącznik): zysk = niebieski, strata = pomarańczowy */
--positive-cb: #3B82F6;   --negative-cb: #F59E0B;
```

Kontrast tekstu ≥ 4.5:1 w obu motywach (sprawdzić narzędziem przed wdrożeniem).

| Element | Wartość |
|---------|---------|
| Font UI | **IBM Plex Sans** (z `font-variant-numeric: tabular-nums`) — neutralny, techniczny, bez generycznego wyglądu Intera |
| Font liczb i linii poleceń | **IBM Plex Mono** — ta sama rodzina, więc liczby i tekst się nie gryzą |
| Skala | 12 · 13 · 14 · 16 · 20 · 28 px (13 = domyślny tekst w tabelach) |
| Siatka | 4 px; padding komórek 8/12 px (compact/comfortable) |
| Promień | 6 px (przyciski, inputy), 0 px (panele terminala) |
| Obramowania | 1 px `--border`; brak cieni poza popoverami i modalami |
| Ruch | 120–160 ms dla UI; zmiana ceny = 400 ms błysk tła w kolorze kierunku; brak animacji wykresów |
| Ikony | Lucide, 16 px, stroke 1.5 |

### 5.3 Layout

```
┌────┬──────────────────────────────────────────────────────────────┐
│ ▣  │  ⌘K  Szukaj albo wpisz polecenie…              PLN ▾   ◐   M │
│ ⌂  ├──────────────────────────────────────────────────────────────┤
│ ≡  │                                                              │
│ ◷  │                    treść modułu                              │
│ ⌁  │                                                              │
│ ✦  │                                                              │
│    │                                                              │
│ ⚙  │                                                              │
└────┴──────────────────────────────────────────────────────────────┘
 sidebar 56 px: Journal · Portfolio · Terminal · Boty · AI · Ustawienia
```

Mobile: dolne zakładki (Journal, Portfolio, AI), terminal i boty tylko w trybie podglądu.

### 5.4 Kluczowe ekrany

**Dashboard journala**
```
 Wrzesień 2026                                    7D  30D  [MTD]  YTD  ALL
 ────────────────────────────────────────────────────────────────────────
 Net PnL          Win rate      Avg R       Profit factor    Max DD
 +4 218,40 USD    47,3%         +0,31R      1,42             −6,8%
 ────────────────────────────────────────────────────────────────────────
 [ equity curve — jedna linia, bez wypełnienia, oś Y po prawej          ]
 ────────────────────────────────────────────────────────────────────────
 ✦ AI · tydzień 39
   Transakcje w 30 min po stracie: 11 szt., −0,52R śr., −640 USD.   →
 ────────────────────────────────────────────────────────────────────────
 Ostatnie transakcje                                            Wszystkie →
 26.09 14:02  SOL-PERP  SHORT  Breakout fail   +1,8R   +312,00
 26.09 09:41  BTC-PERP  LONG   Range low       −1,0R   −150,00
```

**Szczegóły transakcji:** wykres na górze (60% wysokości) z wejściem, wyjściem, SL i TP.
Pod spodem trzy kolumny: liczby (R, PnL, MAE/MFE, czas), kontekst rynkowy (reżim, funding, sesja, eventy)
i journal (setup, checklista, błędy, notatka).

**Import:** lista kont ze statusem (● zsynchronizowane 2 min temu / ● błąd: klucz wygasł).
Kreator pliku: upload → podgląd mapowania (kolumny z pewnością AI) → raport importu.

**Terminal:** linia poleceń na górze, siatka dokowalnych paneli, pasek statusu na dole
(połączenie, opóźnienie danych, czas UTC i lokalny).

### 5.5 Wykresy

- Cena: świece, bez siatki pionowej, crosshair z wartościami w nagłówku panelu zamiast tooltipa.
- Equity: jedna linia, zero na osi zaznaczone, obszary DD delikatnie w `--negative` 8% opacity.
- Rozkłady (R, PnL): histogram, słupki w kolorze znaku.
- Maksymalnie 2 serie na wykres. Więcej = osobne małe wykresy obok siebie (small multiples).

---

## 6. Stack technologiczny

Wybór pod: **jedną osobę na start, dane finansowe, real-time, integracje w Pythonie.**

### 6.1 Frontend aplikacji — React + Vite (SPA), nie Next.js

| Warstwa | Wybór | Dlaczego |
|---------|-------|----------|
| Framework | **React 19 + Vite + TypeScript** | Aplikacja jest za logowaniem (SEO nie ma znaczenia), stanowa i real-time. SPA na CDN jest prostsze, tańsze i szybsze w nawigacji niż SSR. Największy ekosystem komponentów finansowych. |
| Routing / dane | **TanStack Router + TanStack Query** | Type-safe routing z parametrami w URL (filtry journala do udostępnienia), cache i invalidacja danych z API. |
| Tabele | **TanStack Table + TanStack Virtual** | Dziesiątki tysięcy transakcji bez lagów, pełna kontrola nad wyglądem. |
| UI | **Tailwind CSS v4 + Radix UI** (bazowo shadcn/ui) | Dostępne prymitywy bez narzuconego stylu → minimalistyczny design system z tokenów 5.2. |
| Wykresy cenowe | **TradingView Lightweight Charts** | Standard branży, Apache-2.0, szybkie. |
| Wykresy analityczne | **uPlot** | Najszybsza biblioteka do serii czasowych, bardzo mała. |
| Panele terminala | **dockview** | Dokowanie, zakładki, zapisywanie układów. |
| Command palette | **cmdk** | |
| Stan UI | **Zustand** | Tylko stan lokalny; dane serwerowe w TanStack Query. |
| Formularze | **React Hook Form + Zod** | Schematy Zod współdzielone z walidacją typów z API. |
| Klient API | Typy generowane z OpenAPI FastAPI (`openapi-typescript`) | Jeden kontrakt, zero ręcznych typów. |

**Alternatywa:** SvelteKit — mniejszy i szybszy runtime, ale uboższy ekosystem wykresów, tabel i dokowania.

### 6.2 Strona marketingowa — Astro

Statyczna, najszybsza możliwa, blog i SEO. Osobno od aplikacji (`tape.app` vs `app.tape.app`).

### 6.3 Desktop (faza 3) — Tauri 2

Ta sama aplikacja SPA w natywnym oknie: wiele okien i monitorów dla terminala, globalne skróty, powiadomienia systemowe.
Mniejsza i lżejsza niż Electron.

### 6.4 Backend — Python

| Warstwa | Wybór | Dlaczego |
|---------|-------|----------|
| API | **FastAPI + Pydantic v2** | Znasz go z bota. Async, automatyczne OpenAPI. |
| Analityka | **Polars** (+ NumPy) | Szybszy i oszczędniejszy niż pandas dla statystyk journala. |
| ORM / migracje | **SQLAlchemy 2 (async) + Alembic** | |
| Kolejka i cron | **Arq** (Redis) | Async-native, proste retry i zadania cykliczne (sync kont, review). |
| Integracje | **ccxt** + natywne klienty brokerów | ccxt obsługuje też websockety giełd. |
| AI | **`anthropic` SDK** | Sekcja 4.3. |
| Narzędzia | **uv** (pakiety), **Ruff** (lint + format), **pyright**, **pytest** | |

### 6.5 Dane i infrastruktura

| Potrzeba | Wybór |
|----------|-------|
| Baza główna | **PostgreSQL 17** + **TimescaleDB** (świece, snapshoty equity) |
| Cache, pub/sub, kolejka | **Redis / Valkey** |
| Pliki (importy, screenshoty) | **Cloudflare R2** (API S3, brak opłat za transfer) |
| Hosting frontu | **Cloudflare Pages** |
| Hosting API i workerów | **Fly.io** (regiony EU: Warszawa / Frankfurt) na start → **Hetzner** gdy koszty urosną |
| Auth | **Clerk** (passkeys, MFA, social login) za abstrakcją weryfikacji JWT → możliwa migracja do self-hosted (Zitadel / Keycloak) |
| Płatności | **Paddle** (Merchant of Record: VAT UE/OSS, faktury) — mniej formalności niż Stripe + własne rozliczenia VAT |
| E-mail | **Postmark** albo **Resend** |
| Błędy / telemetria | **Sentry** + **OpenTelemetry → Grafana Cloud** |
| Analityka produktu, feature flagi | **PostHog** (hosting w EU) |
| Szyfrowanie kluczy | **Cloud KMS** (AWS / GCP) — envelope encryption |
| CI/CD | **GitHub Actions** (lint, typy, testy, testy parserów na próbkach, deploy) |

### 6.6 Dane rynkowe (terminal)

| Rynek | Źródło | Uwagi |
|-------|--------|-------|
| Crypto | Websockety giełd (darmowe) | Start terminala od crypto — zero kosztów licencji. |
| Akcje US, ETF | Dostawca z licencją na wyświetlanie (np. Databento, Polygon.io, Twelve Data) | Cena rośnie z liczbą użytkowników. **Negocjować licencję „display” przed startem.** |
| GPW | Licencja GPW na dane real-time; na start dane opóźnione lub EOD | Koszt i formalności — faza późniejsza. |
| FX i kursy do przeliczeń | ECB, **NBP** (API publiczne) | NBP wymagany do PIT. |
| News | Finnhub, CryptoPanic, NewsAPI (masz już w bocie) | |
| Makro | Moduł kalendarza z bota | |

---

## 7. Architektura

```
      Cloudflare (CDN, WAF)
     ┌────────┴──────────┐
 Astro (landing)    React SPA (app) ──── WebSocket ────┐
                         │ HTTPS/JSON                  │
                  ┌──────▼───────────────────────────┐ │
                  │  API (FastAPI)                   │◀┘
                  │  auth (JWT Clerk) · CRUD · stats │
                  │  realtime gateway (WS)           │
                  └──┬──────────┬────────────┬───────┘
                     │          │            │
              ┌──────▼───┐ ┌────▼─────┐ ┌────▼──────────────┐
              │PostgreSQL│ │  Redis   │ │ R2 (pliki)        │
              │+Timescale│ │ queue/ps │ └───────────────────┘
              └──────▲───┘ └────┬─────┘
                     │          │ zadania
              ┌──────┴──────────▼────────────────────────────┐
              │ Workers (Arq)                                │
              │ ├ sync      — API brokerów/giełd (co X min)  │
              │ ├ import    — parsowanie plików              │
              │ ├ engine    — pozycje, loty, metryki         │
              │ ├ context   — kontekst rynkowy transakcji    │
              │ ├ ai        — review (Batch API), klasyfikacja│
              │ └ market    — ingest danych rynkowych (WS)   │
              └──────────────────────────────────────────────┘
                     │ klucze odszyfrowywane tylko tutaj (KMS)
              Giełdy · Brokerzy · Claude API · dostawcy danych
```

**Zasady:**
- **Modularny monolit** — jedno repo, jeden backend, moduły z jasnymi granicami
  (`importers/`, `engine/`, `journal/`, `portfolio/`, `market/`, `ai/`, `bots/`). Mikroserwisy dopiero przy realnej potrzebie.
- **Boty (faza 4) w izolowanych procesach** z osobnymi limitami zasobów i własnym kill switchem.
- **Jedno repo (monorepo):** `apps/web`, `apps/landing`, `apps/desktop`, `backend/`, `packages/ui`, `packages/api-types`.

---

## 8. Model danych

```sql
users            (id, email, base_currency, tax_residency, plan, created_at)
accounts         (id, user_id, kind[api|file|push], provider, label, currency,
                  credentials_enc, permissions_verified_at, last_sync_at, status, error)
raw_events       (id, account_id, source_ref, content_hash UNIQUE, payload JSONB, received_at)
instruments      (id, kind[crypto|stock|etf|future|cfd|fx|option], symbol, isin, figi,
                  exchange, currency, multiplier)
transactions     (id, account_id, instrument_id, type, side, qty NUMERIC, price NUMERIC,
                  amount NUMERIC, currency, fee NUMERIC, fee_currency, ts TIMESTAMPTZ,
                  external_id, raw_event_id, UNIQUE(account_id, external_id))
lots             (id, account_id, instrument_id, open_tx_id, qty_open, qty_remaining,
                  cost_basis, cost_basis_pln, opened_at)
positions        (id, account_id, instrument_id, direction, opened_at, closed_at,
                  qty, avg_entry, avg_exit, realized_pnl, fees, funding,
                  initial_sl, r_multiple, mae, mfe, setup_id, notes)
position_tx      (position_id, transaction_id)
market_context   (position_id, regime, funding, fear_greed, session, atr_pct, events JSONB)
setups           (id, user_id, name, rules JSONB)
tags / position_tags
fx_rates         (date, base, quote, rate, source[nbp|ecb])
ai_reviews       (id, user_id, period, findings JSONB, model, input_hash, created_at)
import_templates (id, provider, mapping JSONB, verified, created_by)
bots / bot_runs  (faza 4)
```

- `raw_events` i `transactions` są **niemutowalne**. `lots` i `positions` da się zawsze przeliczyć od zera.
- **Row Level Security** w Postgresie na `user_id` jako druga linia obrony po filtrach w API.

---

## 9. Bezpieczeństwo

To produkt z kluczami do pieniędzy użytkowników — jeden wyciek kończy firmę.

- **Klucze read-only dla journala i portfolio.** Przy dodaniu klucza sprawdzamy jego uprawnienia przez API giełdy
  i **odrzucamy** klucze z uprawnieniem trade lub withdraw (poza modułem botów).
- **Boty:** trade tak, **withdraw nigdy.** Klucz bota osobny od klucza journala.
- **Statyczne IP wyjściowe** workerów, żeby użytkownik mógł przypiąć klucz do naszych adresów (whitelista IP na giełdzie).
- **Envelope encryption:** każdy klucz szyfrowany własnym kluczem danych, który z kolei szyfruje KMS.
  Odszyfrowanie tylko w procesie workera, nigdy w API, logach, frontendzie ani promptach AI.
- **Passkeys i MFA** (Clerk). Wymagane MFA dla modułu botów.
- **Audit log** (logowania, dodanie i usunięcie kluczy, zmiany botów) widoczny dla użytkownika.
- **Pentest przed startem modułu botów.** Program bug bounty po starcie.
- **Kopie zapasowe** z point-in-time recovery; test odtworzenia co kwartał.

---

## 10. Prawo i podatki

> **Przed startem: konsultacja z prawnikiem (usługi finansowe) i doradcą podatkowym.** Poniżej punkty do omówienia, nie porada prawna.

**Regulacje**
- **Journal i portfolio** — narzędzie analityczne danych użytkownika. Najmniejsze ryzyko.
- **Pre-trade check / AI** — analiza historii użytkownika bez rekomendacji. Sformułowania i UI muszą to jasno pokazywać. Granica z doradztwem inwestycyjnym (MiFID II / KNF) do oceny prawnej.
- **Sygnały dla użytkowników** (jak z bota) — mogą być traktowane jako rekomendacje → poza produktem albo po analizie prawnej.
- **Boty na kluczach użytkowników** — software wykonujący strategię użytkownika to co innego niż zarządzanie cudzymi środkami. Do oceny prawnej przed fazą 4, także pod kątem MiCA.
- **Dane rynkowe** — licencje na wyświetlanie (display) i redystrybucję. Bez nich nie wolno pokazywać danych giełd tradycyjnych.
- **RODO** — dane finansowe, DPA z dostawcami (hosting, Clerk, Anthropic, PostHog), eksport i usunięcie konta, hosting w UE.

**PIT-38 (moduł, faza 2) — do weryfikacji z doradcą**
- Papiery wartościowe i derywaty: przychód i koszt przeliczane **średnim kursem NBP z ostatniego dnia roboczego
  poprzedzającego** dzień transakcji; metoda **FIFO**.
- Kryptowaluty: osobne źródło w PIT-38, inne zasady rozliczania kosztów (m.in. przenoszenie nadwyżki kosztów na kolejne lata).
- Dywidendy zagraniczne: podatek u źródła i dopłata do 19%.
- Wynik: zestawienie pól formularza + raport szczegółowy (każda transakcja, kursy, źródło) do wglądu w razie kontroli.

---

## 11. Co przenieść z bota

| Moduł bota | Użycie w Tape |
|------------|---------------|
| `backtest.py` (symulacja, metryki, t-stat, walk-forward) | Statystyki journala + silnik backtestu botów |
| `analysis/market_scanner.py` (reżim, sesje, S/R, korelacje) | `market_context` transakcji + panele terminala |
| `analysis/fear_greed.py`, `funding_rate.py`, `whale_alerts.py` | Kontekst i panele terminala |
| `analysis/economic_calendar.py`, `news_monitor.py` | Panel `ECO`, news w terminalu |
| `fetchers/` | Punkt startowy konektorów danych rynkowych |
| `tracking/position_tracker.py` | Referencja logiki SL/TP/PnL (silnik pozycji piszemy od nowa na `Decimal` i lotach) |
| `strategy/neural_weight_oscillator.py` | **NIE** — CC BY-NC-SA 4.0 |
| `analysis/glm_analyst.py` | Nie — zastępuje go warstwa AI z sekcji 4 |

---

## 12. Monetyzacja

| Plan | Dla kogo | Zawiera |
|------|----------|---------|
| **Free** | wypróbowanie | 1 konto, 90 dni historii, podstawowe statystyki |
| **Pro** (~15–25 USD / mies.) | aktywny trader | Nielimitowane konta i historia, playbooki, AI weekly review, czat, kontekst rynkowy, reguły prop firm |
| **Pro + Tax** (dopłata roczna lub sezonowa) | inwestor w PL | PIT-38, raporty dla doradcy |
| **Terminal** (~40–60 USD / mies.) | faza 3 | Terminal z danymi (cena zależy od kosztu licencji danych) |
| **Bots** (dopłata lub % limitu) | faza 4 | Paper i live, limity liczby botów |

- 14 dni Pro za darmo. Plan roczny −20%.
- **Koszt AI na użytkownika** policzyć na realnych danych (tokeny review × liczba review + czat) przed ustaleniem ceny.
  Batch API i caching mocno go obniżają.
- Kanał startowy: Discord bota, polskie grupy traderskie, prop firm community, treści („co 10 000 transakcji mówi o revenge tradingu”).

---

## 13. Roadmapa

| Faza | Czas | Zakres | Kryterium przejścia dalej |
|------|------|--------|---------------------------|
| **0. Fundament** | 1–2 tyg. | Monorepo, CI, auth, Postgres + migracje, design system (tokeny, komponenty bazowe), szkielet SPA | — |
| **1. Journal MVP** | 6–8 tyg. | Import: Binance, Bybit, IBKR Flex, XTB, uniwersalny CSV/XLSX z mapowaniem AI · silnik pozycji · dashboard, lista, szczegóły transakcji · playbooki i tagi · AI weekly review · Paddle | Beta 20–50 osób: ≥ 30% aktywnych co tydzień, ≥ 5 płacących |
| **2. Portfolio + PL** | 6–8 tyg. | Portfolio analyser · PIT-38 · mBank, Bossa, Degiro, Trading 212, MT4/5 (EA + statement), cTrader · czat „ask your trades” · reguły prop firm | Retencja M1 ≥ 40%, konwersja free→pro ≥ 4% |
| **3. Terminal** | 8–10 tyg. | Linia poleceń, workspace'y, crypto real-time, news, makro, alerty · Tauri desktop · akcje po podpisaniu licencji danych | Użycie terminala ≥ 3 dni w tygodniu u płacących |
| **4. Boty** | 10+ tyg. | Builder, backtest, paper, live z limitami · pentest · opinia prawna | Opinia prawna pozytywna, pentest bez krytycznych |

**Zasada:** kolejna faza startuje dopiero po spełnieniu kryterium poprzedniej. Inaczej budujemy cztery
średnie produkty zamiast jednego dobrego.

---

## 14. Ryzyka i otwarte pytania

| Ryzyko | Mitigacja |
|--------|-----------|
| Parsery brokerów się psują (zmiana formatu eksportu) | Testy na próbkach, monitoring odsetka błędów importu per źródło, szybki kanał zgłoszenia pliku |
| Wyciek kluczy | Sekcja 9; read-only; KMS; brak kluczy withdraw nigdy |
| Koszt danych rynkowych zjada marżę terminala | Start od crypto; licencje negocjowane przed fazą 3; terminal jako wyższy plan |
| AI podaje nieprawdziwe liczby | Walidacja liczb, eval set, AI nie liczy statystyk |
| Za szeroki zakres dla jednej osoby | Twarde kryteria faz (sekcja 13) |
| Regulacje (doradztwo, boty) | Opinia prawna przed fazami 2 (AI pre-trade) i 4 (boty) |

**Otwarte pytania:**
1. Nazwa i domena (Tape to nazwa robocza).
2. Główny segment na start: crypto perps czy prop firmy (MT5)? Wpływa na kolejność importerów.
3. Rozliczenie crypto w PIT-38 — w fazie 2 czy osobny, późniejszy moduł?
4. Język: PL-first czy EN-first z PL jako drugim? (Rynek PL mały, ale PIT i brokerzy PL to wyróżnik.)
