# Tape

Trading journal, analiza portfela i terminal rynkowy dla traderów **złota i srebra** —
z globusem zdarzeń i analizą newsów AI. Specyfikacja: [`../docs/PRODUCT_SPEC.md`](../docs/PRODUCT_SPEC.md),
research: [`../docs/RESEARCH.md`](../docs/RESEARCH.md).

> Katalog tymczasowo mieszka w repo bota. Wydzielenie do osobnego repo z pełną historią:
> `git subtree split --prefix tape -b tape-only`, potem push gałęzi `tape-only` do nowego repozytorium.

## Co działa (faza 1 — pierwszy wycinek)

| Obszar | Stan |
|--------|------|
| Import XTB (zamknięte pozycje, XLSX/CSV) | ✅ czas Europe/Warsaw → UTC, SL, wielkość kontraktu z P/L brokera |
| Import cTrader (historia pozycji, CSV/XLSX) | ✅ strefa czasowa z nagłówka (UTC±h), ilość w lotach albo uncjach |
| Import MetaTrader 5 (Deals, CSV/XLSX) | ✅ czas serwera → UTC (domyślnie UTC+2, do zmiany) |
| Import IBKR Flex (XML) | ✅ futures GC/SI/MGC z mnożnikiem, prowizje, FIFO P/L brokera; bezpieczny parser XML; klient Flex Web Service |
| Automatyczna synchronizacja | ✅ MT5 przez EA „Tape Sync” (token połączenia, w bazie tylko hash); IBKR Flex Web Service co godzinę (token szyfrowany AES-256-GCM, envelope, rotacja kluczy) |
| Import dowolnego CSV z mapowaniem kolumn | ✅ kreator: heurystyka (bez AI) albo propozycja Claude, podgląd, zatwierdzenie |
| Silnik pozycji | ✅ FIFO na `Decimal`, dokładki, częściowe zamknięcia, odwrócenia, R z initial SL |
| Statystyki | ✅ PnL, win rate, PF, drawdown, t-stat, segmenty z testem istotności (w tym „po stracie”) |
| Portfel | ✅ ekspozycja XAU/XAG w uncjach i USD, otwarte pozycje (FIFO) z wyceną spot, wpłaty/wypłaty (MT5, IBKR, ręcznie), stopa zwrotu miesięczna (Modified Dietz) i TWR |
| Przegląd AI journala | ✅ kod liczy fakty (F1…), Claude je interpretuje; wnioski bez faktów lub z liczbami spoza faktów są odrzucane; przegląd zapisany do zmiany danych |
| Ceny XAU/XAG | ✅ Twelve Data, OANDA (konto demo) albo gold-api (bez klucza); świece H1 do wykresów i trafności, bieżąca cena do portfela i paska cen z wiekiem notowania |
| Kalendarz makro USD | ✅ daty FOMC w kodzie, opcjonalnie tygodniowy feed albo CSV od admina; panel „7 dni” przy globusie, dane w pobliżu transakcji, segment „wejście ±30 min od ważnych danych” w statystykach i przeglądzie AI |
| Wiele kont (rachunków) | ✅ każde połączenie MT5/IBKR i każda nazwa przy imporcie to osobny rachunek: osobna deduplikacja, osobne pozycje; przełącznik „Konto” w nagłówku filtruje journal, transakcje, portfel i ryzyko |
| Limity prop na bieżąco | ✅ reguły zapisane dla konta; z EA Tape Sync 1.10 także wynik otwartych pozycji (equity, odczyt ≤ 15 min); zapas do dziennego limitu i max drawdownu NA DZIŚ (dzień w strefie firmy), karty na dashboardzie, czerwony pasek przy ≤25% zapasu lub złamaniu |
| Raport tygodniowy i alerty e-mail | ✅ poniedziałek rano: wynik, najlepsza/najgorsza, koszt błędów, limity prop, dane USD (bez kosztu AI); alert limitu prop maks. raz dziennie na konto; podgląd w Ustawieniach; SMTP dowolnego dostawcy |
| Szczegóły transakcji | ✅ wykres (Lightweight Charts) z IN/OUT/SL na cenach z `/api/prices`, wykonania, journal |
| Playbooki i błędy | ✅ setupy z checklistą reguł, taksonomia błędów, wynik per setup i per błąd; ręczny SL → R dla MT5 |
| Globus zdarzeń 3D | ✅ globe.gl: warstwy, XAU/XAG, obrót do wybranego zdarzenia, pierścienie dla nowych |
| Nastawienie newsów XAU/XAG | ✅ agregacja w kodzie; bez uruchomionego pipeline'u UI pokazuje zdarzenia przykładowe (oznaczone) |
| Pipeline newsów (tryb shadow) | ✅ GDELT → grupowanie → Claude → log ocen (append-only) → trafność vs ceny (`/api/prices`) |
| Kalkulator pozycji (złoto / srebro) | ✅ zaokrąglanie w dół, ostrzeżenia względem dziennego zasięgu i limitu prop firmy |
| Reguły prop firm | ✅ dzienny limit, drawdown statyczny / trailing, symulacja tej samej historii na 3 typach kont |
| Logowanie (Clerk / OIDC) | ✅ JWT weryfikowany po JWKS, konto tylko z tokenu, izolacja danych; włączane zmiennymi środowiskowymi |
| Płatności | ⏳ wymagają konta Paddle |

## Uruchomienie

```bash
# backend (Python 3.11+)
cd tape/backend
pip install -e ".[dev]"
uvicorn tape.api:create_app --factory --reload          # http://127.0.0.1:8000
pytest                                                   # testy

# frontend (Node 20+)
cd tape/web
npm install
npm run dev                                              # http://127.0.0.1:5173 (proxy /api → :8000)
npm run build                                            # typecheck + build produkcyjny

# pipeline newsów w trybie shadow (np. co 15 min); wymaga ANTHROPIC_API_KEY
cd tape/backend
python -m tape.news.pipeline --every 900
# synchronizacja kont IBKR (np. co godzinę); wymaga TAPE_SECRET_KEYS
python -m tape.sync --every 3600
# ceny do liczenia trafności (CSV: timestamp, close)
curl -F file=@xauusd_h1.csv -F asset=XAU http://127.0.0.1:8000/api/prices
```

### Docker (produkcja na jednym serwerze)

```bash
cd tape
cp .env.example .env        # uzupełnij hasło bazy, klucze Clerk / Anthropic
docker compose up -d --build                  # PostgreSQL + API + frontend (nginx) → http://localhost:8080
docker compose --profile news up -d           # + worker newsów co 15 min (wymaga ANTHROPIC_API_KEY)
```

nginx serwuje frontend, przekazuje `/api` do API i dodaje nagłówki bezpieczeństwa. HTTPS zapewnij
przed nim (Caddy, Cloudflare, load balancer). CI (`.github/workflows/ci.yml`) uruchamia testy bota,
testy backendu na SQLite i PostgreSQL, build frontendu i build obrazów.

Zmienne środowiskowe:

| Zmienna | Znaczenie |
|---------|-----------|
| `DATABASE_URL` | domyślnie `sqlite:///tape.db`; produkcyjnie PostgreSQL |
| `TAPE_API_TOKEN` | tryb jednego użytkownika: opcjonalny stały token (`Authorization: Bearer …`) |
| `TAPE_AUTH_JWKS_URL`, `TAPE_AUTH_ISSUER` | włączają logowanie: JWKS i wystawca tokenów (Clerk: `https://<instancja>/.well-known/jwks.json`, `https://<instancja>`) |
| `TAPE_AUTH_AUTHORIZED_PARTIES` | dozwolone adresy frontendu (claim `azp`), po przecinku — zalecane |
| `TAPE_AUTH_AUDIENCE` | opcjonalnie, gdy dostawca ustawia `aud` |
| `TAPE_ADMIN_SUBS` | identyfikatory użytkowników-administratorów (wgrywanie cen), po przecinku |
| `VITE_CLERK_PUBLISHABLE_KEY` | frontend: klucz publiczny Clerk; bez niego aplikacja działa bez logowania |
| `TAPE_SECRET_KEYS` | klucze szyfrowania tokenów brokerów, `id:base64(32 B)`, pierwszy aktywny; bez nich połączenie IBKR jest wyłączone |
| `TAPE_PRICE_PROVIDER` | `twelvedata` (+ `TWELVEDATA_API_KEY`), `oanda` (+ `OANDA_TOKEN`, `OANDA_ENV=practice`) albo `goldapi`; worker: `python -m tape.market --every 300 --backfill 2000` |
| `TAPE_SMTP_HOST`, `TAPE_SMTP_PORT`, `TAPE_SMTP_USER`, `TAPE_SMTP_PASSWORD`, `TAPE_MAIL_FROM` | wysyłka e-maili (STARTTLS); bez nich worker `tape.reports` tylko loguje |
| `ANTHROPIC_API_KEY` | klasyfikator newsów i propozycje mapowania CSV; bez klucza działają fallbacki |

## Stack

React 19 + Vite + TypeScript · TanStack Router / Query · Tailwind v4 · globe.gl (three.js) · uPlot ·
FastAPI · SQLAlchemy 2 · Claude API (structured outputs, prompt caching, server-side fallback).
Uzasadnienie wyborów: `PRODUCT_SPEC.md` sekcja 6.

> ⚠️ Schemat bazy zmienia się jeszcze bez migracji (tabele tworzy `create_all`). Przed pierwszym
> wdrożeniem z prawdziwymi danymi dodajemy Alembic; do tego czasu po zmianie schematu usuń bazę dev.

## Zasady, których pilnuje kod

- **Fill-e są źródłem prawdy** i się nie zmieniają; pozycje zawsze da się przeliczyć od zera.
- **Import jest idempotentny** — ten sam plik drugi raz = same duplikaty, zero nowych.
- **Pieniądze na `Decimal`**, czas w UTC.
- **AI nie liczy statystyk** — kod liczy, AI interpretuje; cytaty AI bez pokrycia w artykule są odrzucane.
- **Wnioski tylko z istotnych różnic** (≥ 10 transakcji w segmencie, |t| ≥ 2); nastawienie newsów pokazuje,
  że trafność nie jest jeszcze policzona, zamiast udawać sygnał.
