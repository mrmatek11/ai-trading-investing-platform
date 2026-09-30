"""Przykładowe zdarzenia dla globusa, dopóki nie działa pipeline newsów. Oznaczone `sample=True`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .bias import Event, Impact


def sample_events(now: Optional[datetime] = None) -> List[Event]:
    now = now or datetime.now(timezone.utc)
    ago = lambda minutes: now - timedelta(minutes=minutes)  # noqa: E731
    both = lambda xau, xag, mag, hz="days": {"XAU": Impact(xau, mag, hz), "XAG": Impact(xag, mag, hz)}  # noqa: E731
    return [
        Event("hormuz", "Cieśnina Ormuz: incydent z tankowcem", "conflict", 26.6, 56.3, ago(38), both(1, 1, 4),
              place="Zatoka Perska", sources=27, sample=True,
              summary="Ubezpieczyciele podnoszą stawki za przejście, ropa rośnie. Kanał: bezpieczna przystań i oczekiwania inflacyjne.",
              analog="Podobne zdarzenia od 2019: 9 · XAU po 1 dniu: mediana +0,4%, wzrost w 5 z 9."),
        Event("fed", "Fed: jastrzębie przemówienie członka FOMC", "cb", 38.9, -77.0, ago(95), both(-1, -1, 4),
              place="Waszyngton", sources=41, sample=True,
              summary="Sygnał „dłużej wyżej” podbił realne rentowności 10Y. Kanał: koszt alternatywny trzymania złota.",
              analog="Podobne wypowiedzi: 31 · XAU po 1 dniu: mediana −0,3%."),
        Event("mideast", "Bliski Wschód: postęp rozmów o zawieszeniu broni", "conflict", 30.0, 31.2, ago(70),
              {"XAU": Impact(-1, 3), "XAG": Impact(0, 1)}, place="Kair", sources=33, sample=True,
              summary="Deeskalacja zmniejsza premię za ryzyko — działa odwrotnie niż eskalacja.",
              analog="Podobne zdarzenia: 14 · XAU po 1 dniu: mediana −0,2%, słaba zależność."),
        Event("peru", "Peru: strajk w kopalni srebra i miedzi", "supply", -12.0, -76.0, ago(150),
              {"XAU": Impact(0, 1), "XAG": Impact(1, 3, "weeks")}, place="Andy, Peru", sources=6, sample=True,
              summary="Wstrzymanie wydobycia w jednej z większych kopalń regionu. Kanał: podaż srebra.",
              analog="Strajki w kopalniach od 2015: 11 · XAG po 5 dniach: mediana +0,6%."),
        Event("cpi", "USA: publikacja CPI jutro 14:30", "macro", 40.7, -74.0, now + timedelta(hours=16),
              both(0, 0, 5, "intraday"), place="Nowy Jork", sample=True,
              summary="Wynik powyżej konsensusu historycznie osłabia złoto w dniu publikacji; poniżej — wzmacnia.",
              analog="Publikacje z niespodzianką > 0,1 pp: 24 · XAU w dniu publikacji: mediana −0,7% / +0,6%."),
        Event("ecb", "EBC: decyzja w sprawie stóp w czwartek", "cb", 50.1, 8.7, now + timedelta(days=2),
              both(0, 0, 3), place="Frankfurt", sample=True,
              summary="Rynek wycenia brak zmian. Wpływ na złoto głównie przez EUR/USD.",
              analog="Decyzje zgodne z oczekiwaniami: XAU zwykle w zakresie ±0,3%."),
        Event("nbp", "NBP zwiększa rezerwy złota", "cb", 52.2, 21.0, ago(300),
              {"XAU": Impact(1, 2, "weeks"), "XAG": Impact(0, 1)}, place="Warszawa", sources=9, sample=True,
              summary="Kolejny miesiąc zakupów banków centralnych — wsparcie strukturalne.",
              analog="Wpływ na cenę w horyzoncie dni: nieistotny statystycznie."),
        Event("ukraine", "Ukraina: eskalacja na froncie wschodnim", "conflict", 48.3, 37.8, ago(240),
              {"XAU": Impact(1, 2), "XAG": Impact(0, 1)}, place="Donbas", sources=18, novelty="developing", sample=True,
              summary="Bez nowych sankcji ani zakłóceń dostaw — ograniczony wpływ na metale.",
              analog="W 70% podobnych przypadków wpływ zanika po 1–2 dniach."),
        Event("lbma", "Londyn: napływy do skarbców LBMA", "market", 51.5, -0.1, ago(420),
              both(1, 1, 2, "weeks"), place="Londyn", sources=5, sample=True,
              summary="Rosnące zapasy w skarbcach wskazują na popyt instytucjonalny.",
              analog="Dane miesięczne — sygnał wolny, horyzont tygodni."),
        Event("india", "Indie: popyt na biżuterię przed sezonem świątecznym", "market", 19.1, 72.9, ago(600),
              {"XAU": Impact(1, 2, "weeks"), "XAG": Impact(0, 1)}, place="Mumbaj", sources=12, sample=True,
              summary="Sezonowy wzrost popytu fizycznego; premie lokalne rosną.",
              analog="Efekt sezonowy widoczny w danych od 2010, słaby w horyzoncie dni."),
        Event("shanghai", "Chiny: rekordowa premia na złoto w Szanghaju", "market", 31.2, 121.5, ago(180),
              {"XAU": Impact(1, 2), "XAG": Impact(0, 1)}, place="Szanghaj", sources=8, sample=True,
              summary="Wysoka premia lokalna wskazuje na silny popyt fizyczny w Chinach.",
              analog="Premia > 20 USD/oz: historycznie wsparcie w horyzoncie tygodni."),
        Event("mexico", "Meksyk: nowe regulacje górnicze", "supply", 22.8, -102.6, ago(900),
              {"XAU": Impact(0, 1), "XAG": Impact(1, 2, "weeks")}, place="Zacatecas", sources=4, sample=True,
              summary="Zaostrzenie koncesji w największym na świecie kraju wydobycia srebra.",
              analog="Efekt zależy od wdrożenia — obserwować."),
    ]
