# Wyniki backtestu — strategia NWO + Stoch + CVD

Stan: 2026-09-28, po poprawkach z commita „Fix startup crash, NWO state drift and signal tier bugs”.
Metoda: `backtest.py` — ta sama funkcja strategii co live, okno 499 barów, wejście na open następnej
świecy, fees + slippage, SL przed TP w tej samej świecy, walk-forward 5 okresów (anchored).

## Złoto (XAUUSD) — główny rynek

Dane: publiczne repozytorium `ejtraderLabs/historical-data` (eksport MT5, 2012-05 → 2022-03; czas serwera).
Koszty: fee 0,01% + slippage 0,005% na stronę (≈ spread detaliczny na złocie). Srebra w tym zbiorze nie ma.

### Strategia bota (NWO + Stoch + CVD)

| Interwał | Konfiguracja live: trades · avg R · t | Walk-forward OOS: trades · avg R · t | Werdykt |
|----------|---------------------------------------|--------------------------------------|---------|
| D1 (2 400 świec) | 32 · **−0,39R** · −2,0 | 53 · −0,11R · −0,6 | brak przewagi |
| H1 (57 600 świec) | 945 · **−0,08R** · −2,0 | 826 · **−0,12R** · −2,4 | **istotnie stratna** |

Na H1 najgorszy jest tier **STOCH STRICT+NWO**: 779 transakcji, −0,17R, t = −4,1. Ten tier wcześniej
nigdy się nie uruchamiał (błąd kolejności warunków); po naprawie widać, że jest szkodliwy.

### Laboratorium strategii (`research/strategy_lab.py`)

Walk-forward 5 okresów; parametry wyjścia (SL/TP w ATR, filtr trendu, timeout) wybierane tylko na przeszłości.
Rodzina 18 testów → próg istotności (Bonferroni, α = 5%): **t > 2,77**. Wynik = OOS avg R (t).

| Strategia | D1 | H4 | H1 |
|-----------|----|----|----|
| Donchian breakout (20) | −0,04R (−0,3) | +0,02R (+0,2) | −0,01R (−0,2) |
| RSI(2) pullback w trendzie | −0,24R (−1,1) | −0,18R (−2,0) | −0,08R (−1,2) |
| Bollinger reversion | +0,06R (+0,2) | −0,04R (−0,3) | −0,14R (−1,6) |
| London breakout (zakres azjatycki) | — | — | +0,08R (+1,0) |
| Momentum 60 świec | −0,23R (−1,7) | −0,01R (−0,1) | −0,03R (−0,7) |
| **Losowe wejścia (punkt odniesienia)** | +0,03R (+0,2) | +0,16R (+1,0) | −0,04R (−0,3) |

London breakout przy innych założeniach strefy czasowej danych: UTC+2 → +0,13R (t = 1,65), UTC+3 → +0,05R (t = 0,71).

**Wnioski dla złota:**
- Żadna strategia nie przekracza progu istotności; większość jest w zakresie, w którym mieszczą się losowe wejścia.
- **London breakout** jest jedynym kandydatem konsekwentnie dodatnim poza próbą — do dalszych badań
  (dokładne godziny sesji w UTC, realny spread w oknie otwarcia Londynu, dłuższa historia), **nie do handlu**.
- Strategia bota nie powinna być używana do automatycznego handlu złotem.

## Inne rynki

Uruchom lokalnie dla aktualnych danych metali (Yahoo Finance):

```bash
python backtest.py --yf GC=F --symbol XAU/USD --timeframe 1h   # złoto, ~2 lata 1h
python backtest.py --yf GC=F --symbol XAU/USD --timeframe 4h
python backtest.py --yf SI=F --symbol XAG/USD --timeframe 1h   # srebro
python backtest.py --yf GC=F --symbol XAU/USD --timeframe 1d --since 2005-01-01
```

## Wyniki

| Rynek | Dane | Koszty (na stronę) | Live: trades · avg R · t | Walk-forward OOS: trades · avg R · t | Werdykt |
|-------|------|--------------------|--------------------------|--------------------------------------|---------|
| EUR/USD | 1h, 2017-04 → 2018-02 (5 000 świec) | fee 0,005% + slip 0,005% | 82 · **−0,23R** · −1,7 | 85 · −0,04R · −0,4 | brak przewagi |
| NVDA | 1d, 1999 → 2014 | fee 0,05% + slip 0,05% | 66 · −0,02R · −0,1 | 73 · −0,02R · −0,1 | brak przewagi |
| ORCL | 1d, 1995 → 2014 | fee 0,05% + slip 0,05% | 77 · −0,04R · −0,3 | 60 · −0,18R · −0,9 | brak przewagi |
| YHOO | 1d, 1996 → 2014 | fee 0,05% + slip 0,05% | 75 · +0,17R · +1,1 | 78 · +0,49R · +2,2 | „obiecujące” — patrz niżej |

Źródła danych: `kernc/backtesting.py` (EURUSD), `mementum/backtrader` (NVDA, ORCL, YHOO).

## Wnioski

1. **Brak dowodu przewagi.** 1 z 4 rynków przekracza t = +2. Przy czterech niezależnych testach
   strategii bez przewagi szansa, że przynajmniej jeden przekroczy ten próg przypadkiem, to ok. 9%
   (1 − 0,977⁴). Do tego średnia z czterech rynków jest bliska zera — pojedynczy wynik YHOO nie jest dowodem.
2. **Ranking tierów jest losowy.** Najlepszy tier to kolejno: CONFLUENCE (EURUSD), STOCH-ONLY (NVDA),
   CONFLUENCE (YHOO), STOCH+NWO (ORCL). Gdyby któryś filtr niósł informację, wygrywałby konsekwentnie.
3. **Handel pod trend jest gorszy** w 3 z 4 rynków (wyjątek: EURUSD) — to wspiera domyślne
   `--trend-filter block`.
4. **STOCH-ONLY jest najgorszy** w 3 z 4 rynków — to wspiera wyłączenie go domyślnie.
5. Walk-forward wybiera różne parametry w każdym okresie — kolejny znak, że optymalizacja dopasowuje się do szumu.

**Rekomendacja:** nie używać tej strategii do automatycznego handlu — na złocie wynik jest istotnie ujemny.
Bot ma wartość jako narzędzie do alertów i kontekstu, a nie źródło przewagi.
