# Plan SaaS: AI Trading Journal + Portfolio

> Dokument startowy dla **nowego repo**. Ten bot zostaje osobnym projektem — SaaS
> reużywa z niego wybrane moduły (niżej), ale **nie** `neural_weight_oscillator.py`
> (licencja CC BY-NC-SA 4.0 zabrania użycia komercyjnego).

## 1. Pozycjonowanie

**Nie** budujemy "Bloomberga + journala + portfolio + bot platformy" naraz — to cztery
produkty z silną konkurencją każdy. Budujemy jeden, z wyróżnikiem:

> **Journal, który sam importuje Twoje transakcje i mówi Ci, gdzie tracisz pieniądze.**

Większość journali to ręczne tabelki + wykresy. Wyróżnik:

1. **Zero ręcznej pracy** — auto-import z giełd (klucze read-only) i CSV z brokerów.
2. **AI coach behawioralny** — wykrywa wzorce: overtrading po stracie (revenge),
   za duży size po serii wygranych, godziny/dni/setupy z ujemnym expectancy,
   przesuwanie SL, zamykanie zysków za wcześnie.
3. **Kontekst rynkowy przy każdej transakcji** — reżim rynku, funding, F&G, sesja
   w momencie wejścia (z modułów tego bota), żeby odpowiedzieć na pytanie
   "w jakich warunkach mój edge działa".

Terminal i bot platform to ewentualnie **późniejsze** moduły premium, nie MVP.

## 2. MVP (6–8 tygodni, 1 osoba)

| # | Funkcja | Po co |
|---|---------|-------|
| 1 | Rejestracja, logowanie, 1 plan płatny (Stripe) | walidacja, że ktoś płaci |
| 2 | Import: Binance + Bybit (API read-only) + CSV | główna wartość: brak ręcznej pracy |
| 3 | Rekonstrukcja pozycji z fillów (FIFO, częściowe zamknięcia, fees, funding) | poprawne PnL — fundament wszystkiego |
| 4 | Dashboard: equity, PnL, win rate, avg R, PF, drawdown, rozkład po symbolu/godzinie/dniu | standard, bez którego nikt nie zapłaci |
| 5 | Tagi setupów + notatki + screenshot do transakcji | journal w ścisłym sensie |
| 6 | **AI weekly review** — raport tygodniowy z 3 konkretnymi wnioskami i liczbami | wyróżnik |
| 7 | Portfolio: aktualne salda, ekspozycja, alokacja | tani dodatek, bo dane już są |

**Poza MVP:** mobile app, social/sharing, copy trading, własny terminal, auto-trading.

## 3. Architektura

```
┌──────────────┐   HTTPS    ┌──────────────────┐     ┌─────────────────┐
│  Next.js     │ ─────────▶ │  FastAPI (API)   │ ──▶ │  PostgreSQL     │
│  (Vercel)    │            │  auth, CRUD,     │     │  (+ Timescale   │
└──────────────┘            │  stats           │     │   dla OHLCV)    │
                            └────────┬─────────┘     └─────────────────┘
                                     │ kolejka (Redis)
                            ┌────────▼─────────┐     ┌─────────────────┐
                            │  Workers         │ ──▶ │  Giełdy (ccxt)  │
                            │  - sync giełd    │     └─────────────────┘
                            │  - rekonstrukcja │     ┌─────────────────┐
                            │  - AI review     │ ──▶ │  Claude API     │
                            └──────────────────┘     └─────────────────┘
```

- **Frontend:** Next.js + TypeScript + Tailwind + shadcn/ui; wykresy: lightweight-charts (TradingView, Apache-2.0) + Recharts.
- **Backend:** FastAPI (znasz już z bota), SQLAlchemy + Alembic, Pydantic.
- **Kolejka:** Redis + RQ/Arq (sync co X min, AI review raz w tygodniu).
- **Auth:** Clerk / Supabase Auth albo własne (FastAPI + JWT) — zewnętrzny szybszy na start.
- **Płatności:** Stripe Checkout + Customer Portal + webhooki.
- **AI:** Claude API — mocniejszy model do tygodniowego review (mało wywołań, długi kontekst),
  mały i tani model do tagowania/klasyfikacji pojedynczych transakcji.
- **Hosting:** Vercel (front) + Fly.io/Railway (API, workery) + Neon/Supabase (Postgres).

## 4. Model danych (rdzeń)

```
users(id, email, plan, stripe_customer_id, created_at)
exchange_accounts(id, user_id, exchange, label, api_key_enc, api_secret_enc,
                  permissions_checked_at, last_sync_at, status)
fills(id, account_id, exchange_fill_id UNIQUE, symbol, side, price, qty,
      fee, fee_asset, ts, raw JSONB)
positions(id, account_id, symbol, direction, opened_at, closed_at,
          avg_entry, avg_exit, qty, realized_pnl, fees, funding,
          initial_sl, r_multiple, setup_tag, notes)
position_fills(position_id, fill_id)
market_context(position_id, regime, funding, fear_greed, session, atr_pct)
ai_reviews(id, user_id, period_start, period_end, findings JSONB, model, created_at)
```

Zasady:
- `fills` są **źródłem prawdy** i niemutowalne; `positions` zawsze da się przeliczyć od zera.
- Idempotentny sync: `UNIQUE(account_id, exchange_fill_id)` + upsert.
- R-multiple wymaga SL — gdy brak, pytamy użytkownika albo liczymy od ATR (oznaczone jako szacunek).

## 5. Bezpieczeństwo (to, co zabija takie produkty)

- **Tylko klucze read-only.** Przy dodaniu klucza sprawdzamy uprawnienia przez API giełdy
  i **odrzucamy** klucze z uprawnieniem trade/withdraw.
- Klucze szyfrowane (envelope encryption: KMS + klucz per rekord), nigdy w logach,
  nigdy zwracane do frontendu.
- Izolacja tenantów: każde zapytanie filtrowane po `user_id` (Postgres RLS jako druga warstwa).
- AI dostaje zagregowane statystyki i transakcje użytkownika — bez kluczy i e-maili.

## 6. AI — jak zrobić, żeby nie było "ChatGPT wrapperem"

AI **nie liczy** statystyk — liczy je kod. AI dostaje gotowe liczby i ma je zinterpretować:

1. Kod liczy segmenty: expectancy per setup / godzina / dzień / symbol / kierunek /
   "po stracie" vs "po wygranej" / size vs mediana / czas trzymania.
2. Kod wybiera segmenty istotnie różne od średniej (min. N transakcji, test statystyczny).
3. Claude dostaje tabelę segmentów + kontekst i zwraca JSON:
   `{finding, evidence (liczby), impact_usd, suggestion}` — max 3 wnioski.
4. Walidacja: każda liczba w odpowiedzi musi istnieć w danych wejściowych (odrzucamy halucynacje).

Przykładowy wniosek, który sprzedaje produkt:
> "Transakcje otwarte w ciągu 30 min po stracie: 41 szt., win rate 29%, −0.45R średnio,
> łącznie −1 870 USD. Bez nich Twój miesiąc byłby na plusie."

## 7. Co przenieść z tego bota

| Moduł | Użycie w SaaS |
|-------|---------------|
| `analysis/fear_greed.py`, `funding_rate.py`, `market_scanner.py` (reżim, sesje) | `market_context` przy każdej transakcji |
| `analysis/economic_calendar.py` | oznaczanie transakcji otwartych przed eventami makro |
| `tracking/position_tracker.py` (logika SL/TP/PnL) | referencja przy rekonstrukcji pozycji |
| `backtest.py` (metryki, t-stat) | te same metryki w dashboardzie |
| `strategy/neural_weight_oscillator.py` | **NIE** — licencja NC |

## 8. Monetyzacja

- **Free:** 1 konto giełdowe, 90 dni historii, podstawowe statystyki.
- **Pro (~15–25 USD/mies.):** wiele kont, pełna historia, AI weekly review, kontekst rynkowy.
- **Później:** plan "Trader+" z alertami behawioralnymi w czasie rzeczywistym
  (np. "3. strata z rzędu — Twoje dane mówią, że teraz tracisz najwięcej").

Koszt AI: 1 review/tydzień/użytkownika na modelu z długim kontekstem to niewielki ułamek ceny planu —
policz dokładnie na realnych danych przed ustaleniem ceny.

## 9. Prawo (skonsultuj z prawnikiem przed startem)

- Journal/analiza **własnych** transakcji użytkownika = narzędzie analityczne — najmniejsze ryzyko.
- Sygnały / rekomendacje "kup X" dla użytkowników mogą być traktowane jako doradztwo inwestycyjne
  (MiFID II / KNF) — dlatego sygnały bota **nie** wchodzą do MVP.
- Auto-trading na kluczach użytkowników = zarządzanie cudzymi środkami + ogromne ryzyko bezpieczeństwa.
- RODO: dane finansowe użytkowników, DPA z dostawcami (hosting, AI), eksport i usuwanie konta.

## 10. Roadmapa

| Tydzień | Cel |
|---------|-----|
| 1 | Repo, auth, Postgres + migracje, szkielet Next.js, CI |
| 2–3 | Sync Binance/Bybit (fills), rekonstrukcja pozycji + testy na prawdziwych eksportach |
| 4 | Dashboard statystyk, tagi, notatki |
| 5 | Segmentacja + AI weekly review |
| 6 | Stripe, landing page, onboarding |
| 7–8 | Beta z 10–20 traderami (Discord bota to gotowa grupa testowa), poprawki |

**Kryterium sukcesu bety:** ≥ 30% testerów wraca co tydzień i ≥ 3 osoby deklarują płatność.
Jeśli nie — zmieniamy wyróżnik, zanim dobudujemy terminal czy boty.
