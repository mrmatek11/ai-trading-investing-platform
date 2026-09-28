# Wyniki backtestu — strategia NWO + Stoch + CVD

Stan: 2026-09-28, po poprawkach z commita „Fix startup crash, NWO state drift and signal tier bugs”.
Metoda: `backtest.py` — ta sama funkcja strategii co live, okno 499 barów, wejście na open następnej
świecy, fees + slippage, SL przed TP w tej samej świecy, walk-forward 5 okresów (anchored).

Złota i srebra **nie udało się przetestować** w środowisku, w którym powstał ten raport (brak dostępu
do źródeł danych). Użyto publicznie dostępnych, prawdziwych danych spoza crypto.
Uruchom lokalnie dla metali:

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

**Rekomendacja:** nie używać tej strategii do automatycznego handlu. Jeśli metale wyjdą podobnie,
traktować bota jako narzędzie do alertów i kontekstu, a nie źródło przewagi.
