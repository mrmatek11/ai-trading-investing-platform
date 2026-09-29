"""Grupowanie artykułów o tej samej sprawie w zdarzenia.

Deterministycznie: podobieństwo Jaccarda znormalizowanych słów tytułu + okno czasowe.
Wystarcza na start i nie wymaga modelu embeddingów; identyfikator klastra jest stabilny
(hash najwcześniejszego URL-a), więc ponowne uruchomienie nie tworzy duplikatów zdarzeń.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, FrozenSet, List, Sequence, Set

from .classify import Article

STOPWORDS = frozenset("""
a an the of to in on for and or as at by with from is are was were be been it its this that
after amid over under says said new up down more less than into about as will would could
""".split())
SIMILARITY = 0.35
WINDOW = timedelta(hours=12)


def tokens(title: str) -> FrozenSet[str]:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return frozenset(w for w in words if len(w) > 2 and w not in STOPWORDS)


def jaccard(a: FrozenSet[str], b: FrozenSet[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


@dataclass
class Cluster:
    articles: List[Article] = field(default_factory=list)
    token_sets: List[FrozenSet[str]] = field(default_factory=list)

    @property
    def id(self) -> str:
        first = min(self.articles, key=lambda a: (a.published_at, a.url))
        return "c_" + hashlib.sha1(first.url.encode()).hexdigest()[:16]

    @property
    def domains(self) -> int:
        return len({re.sub(r"^https?://(www\.)?", "", a.url).split("/")[0] for a in self.articles})


def cluster(articles: Sequence[Article]) -> List[Cluster]:
    """Każdy artykuł (od najstarszego) trafia do klastra o najwyższym podobieństwie ≥ SIMILARITY albo tworzy nowy.

    Wynik jest taki sam jak przy porównaniu z każdym klastrem, ale liczymy tylko to, co może wygrać:
    - klaster, którego ostatni artykuł jest starszy niż WINDOW, już nigdy nie wróci do gry (artykuły idą po czasie);
    - klaster bez wspólnego słowa ma podobieństwo 0, a wybór wymaga sim > 0 — pomijamy go (indeks słowo → klastry);
    - identyczne zestawy słów w klastrze liczymy raz.
    """
    clusters: List[Cluster] = []
    last: List = []                                  # czas ostatniego artykułu w klastrze
    uniq: List[set] = []                             # różne zestawy słów w klastrze
    index: Dict[str, Set[int]] = defaultdict(set)    # słowo → aktywne klastry, które je zawierają
    active: Set[int] = set()
    for art in sorted(articles, key=lambda a: a.published_at):
        tok = tokens(art.title)
        expired = [i for i in active if art.published_at - last[i] > WINDOW]
        for i in expired:
            active.discard(i)
            for sets in uniq[i]:
                for w in sets:
                    index[w].discard(i)
        candidates = sorted(set().union(*(index[w] for w in tok if w in index))) if tok else []
        best, best_sim = None, 0.0
        for i in candidates:                         # kolejność tworzenia — przy remisie wygrywa starszy klaster
            sim = max(jaccard(tok, t) for t in uniq[i])
            if sim > best_sim:
                best, best_sim = i, sim
        if best is not None and best_sim >= SIMILARITY:
            c = clusters[best]
            c.articles.append(art)
            c.token_sets.append(tok)
            last[best] = art.published_at
            if tok not in uniq[best]:
                uniq[best].add(tok)
                for w in tok:
                    index[w].add(best)
        else:
            i = len(clusters)
            clusters.append(Cluster([art], [tok]))
            last.append(art.published_at)
            uniq.append({tok})
            active.add(i)
            for w in tok:
                index[w].add(i)
    return clusters
