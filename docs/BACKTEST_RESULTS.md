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

## Złoto — daytrading (M15, `research/intraday_lab.py`)

Stan: 2026-09-29. Dane: `ejtraderLabs/historical-data` XAUUSD M15, 2012-05 → 2022-03 (230 400 świec).

**Metodyka**
- Czas: dane MT5 są w czasie serwera EET/EEST — przerwa dzienna złota wypada zawsze o 00:00 serwera,
  a w tygodniach między zmianą czasu w USA i UE przesuwa się o godzinę. Sesje liczone w czasie lokalnym
  Londynu / Nowego Jorku. (Wcześniejszy test London breakout zakładał stałe przesunięcie — był o godzinę
  przesunięty przez część roku.)
- Wejście zleceniem stop (luka przez poziom = fill po open), SL przed TP w tej samej świecy, bez pozycji na noc.
- Koszt 0,40 USD/oz na transakcję (spread + poślizg ECN), wrażliwość 0,2 / 0,6 / 1,0 USD.
- Parametry wybierane tylko na 2012–2016; ocena na 2017–2022 (out-of-sample). Punkt odniesienia:
  losowy kierunek w tych samych godzinach z tym samym zarządzaniem — traci ≈ koszt, zgodnie z oczekiwaniem.
- Rodzina 8 strategii → próg Bonferroni (α = 5%): **t > 2,73**.

| Strategia | IS 2012–16: śr. bps (t) | OOS 2017–22: n · śr. bps (t) | PF OOS | lata OOS na plus |
|-----------|-------------------------|------------------------------|--------|------------------|
| London ORB (08:00 LDN) | +0,2 (0,1) | 1 299 · +0,5 (0,4) | 1,04 | 4/6 |
| London ORB + trend SMA20 | +0,6 (0,4) | 899 · −1,3 (−1,1) | 0,89 | 2/6 |
| **London ORB po dniu NR7** | **+3,3 (0,8)** | **184 · +4,0 (1,2)** | **1,28** | **5/6** |
| Zakres azjatycki — wybicie | +3,0 (1,5) | 1 148 · −1,5 (−0,9) | 0,94 | 3/6 |
| NY ORB (COMEX 08:20) | +0,4 (0,3) | 1 199 · −1,3 (−1,1) | 0,91 | 2/6 |
| Kontynuacja Londyn → NY | +1,5 (0,4) | 235 · −8,3 (**−2,7**) | 0,60 | 2/6 |
| Dryf sesji (long Azja) | +0,8 (0,7) | 1 333 · −1,3 (−1,4) | 0,90 | 1/6 |
| Mean reversion od otwarcia Londynu | −7,5 (−2,5) | 257 · −4,4 (−1,6) | 0,78 | 1/6 |

**Wnioski analityka**
1. **Żadna strategia nie ma istotnej przewagi po kosztach.** Najlepszy kandydat — London ORB po dniu NR7 —
   jest dodatni i w próbie, i poza nią, w 5 z 6 lat, ale t = 1,2 przy 184 transakcjach to wciąż zgodne z szumem.
   Przewaga (+4 bps ≈ 0,7 USD/oz) znika przy koszcie ~1 USD/oz — u brokera ze spreadem 0,5+ USD jej nie ma.
2. **Kontynuacja Londyn → NY jest istotnie ujemna poza próbą** (t = −2,7). To sugeruje *odwrócenie* ruchu
   Londynu na otwarciu NY — ale ta hipoteza powstała po obejrzeniu wyników, więc wolno ją sprawdzić tylko
   na nowych danych (2022-03 →). Zapisuję ją tu, zanim zobaczę te dane.
3. Filtry „oczywiste” (trend SMA20) pogarszają wynik — typowy objaw dopasowania do szumu.

**Decyzja dla bota:** `strategy/gold_orb.py` realizuje London ORB po NR7 **wyłącznie jako paper trading**
(plan dnia + alert Discord + dziennik), na tym samym kodzie co backtest. Warunek przejścia na realne pieniądze:
≥ 100 transakcji paper/live z wynikiem po kosztach w przedziale ufności badania i spread ≤ 0,40 USD/oz.

```bash
python -m research.intraday_lab --csv XAUUSD-m15.csv                  # pełne badanie (~10 min)
python -m strategy.gold_orb --csv XAUUSD-m15.csv --paper 250           # replay
python -m strategy.gold_orb --live --balance 100000 --risk-pct 0.5     # plan na dziś (GC=F z Yahoo)
```

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
