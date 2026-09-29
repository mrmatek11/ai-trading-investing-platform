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
| Import MetaTrader 5 (Deals, CSV/XLSX) | ✅ czas serwera → UTC (domyślnie UTC+2, do zmiany) |
| Import dowolnego CSV z mapowaniem kolumn | ✅ kreator: heurystyka (bez AI) albo propozycja Claude, podgląd, zatwierdzenie |
| Silnik pozycji | ✅ FIFO na `Decimal`, dokładki, częściowe zamknięcia, odwrócenia, R z initial SL |
| Statystyki | ✅ PnL, win rate, PF, drawdown, t-stat, segmenty z testem istotności (w tym „po stracie”) |
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
# ceny do liczenia trafności (CSV: timestamp, close)
curl -F file=@xauusd_h1.csv -F asset=XAU http://127.0.0.1:8000/api/prices
```

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
| `ANTHROPIC_API_KEY` | klasyfikator newsów i propozycje mapowania CSV; bez klucza działają fallbacki |

## Stack

React 19 + Vite + TypeScript · TanStack Router / Query · Tailwind v4 · globe.gl (three.js) · uPlot ·
FastAPI · SQLAlchemy 2 · Claude API (structured outputs, prompt caching, server-side fallback).
Uzasadnienie wyborów: `PRODUCT_SPEC.md` sekcja 6.

## Zasady, których pilnuje kod

- **Fill-e są źródłem prawdy** i się nie zmieniają; pozycje zawsze da się przeliczyć od zera.
- **Import jest idempotentny** — ten sam plik drugi raz = same duplikaty, zero nowych.
- **Pieniądze na `Decimal`**, czas w UTC.
- **AI nie liczy statystyk** — kod liczy, AI interpretuje; cytaty AI bez pokrycia w artykule są odrzucane.
- **Wnioski tylko z istotnych różnic** (≥ 10 transakcji w segmencie, |t| ≥ 2); nastawienie newsów pokazuje,
  że trafność nie jest jeszcze policzona, zamiast udawać sygnał.
