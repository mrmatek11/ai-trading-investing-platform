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
| Import dowolnego CSV z mapowaniem kolumn | ✅ backend (`importers/generic.py`); kreator z AI — następny krok |
| Silnik pozycji | ✅ FIFO na `Decimal`, dokładki, częściowe zamknięcia, odwrócenia, R z initial SL |
| Statystyki | ✅ PnL, win rate, PF, drawdown, t-stat, segmenty z testem istotności (w tym „po stracie”) |
| Globus zdarzeń 3D | ✅ globe.gl: warstwy, XAU/XAG, obrót do wybranego zdarzenia, pierścienie dla nowych |
| Nastawienie newsów XAU/XAG | ✅ agregacja w kodzie; klasyfikator Claude gotowy, **zdarzenia na razie przykładowe** |
| Logowanie, płatności, pipeline newsów na żywo | ⏳ następne kroki |

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
```

Zmienne środowiskowe:

| Zmienna | Znaczenie |
|---------|-----------|
| `DATABASE_URL` | domyślnie `sqlite:///tape.db`; produkcyjnie PostgreSQL |
| `TAPE_API_TOKEN` | opcjonalny token (nagłówek `Authorization: Bearer …`) do czasu wdrożenia logowania |
| `ANTHROPIC_API_KEY` | dla klasyfikatora newsów (`tape/news/classify.py`) |

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
