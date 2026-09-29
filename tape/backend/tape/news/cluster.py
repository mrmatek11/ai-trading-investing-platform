"""Grupowanie artykułów o tej samej sprawie w zdarzenia.

Deterministycznie: podobieństwo Jaccarda znormalizowanych słów tytułu + okno czasowe.
Wystarcza na start i nie wymaga modelu embeddingów; identyfikator klastra jest stabilny
(hash najwcześniejszego URL-a), więc ponowne uruchomienie nie tworzy duplikatów zdarzeń.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import FrozenSet, List, Sequence

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
    clusters: List[Cluster] = []
    for art in sorted(articles, key=lambda a: a.published_at):
        tok = tokens(art.title)
        best, best_sim = None, 0.0
        for c in clusters:
            if art.published_at - max(a.published_at for a in c.articles) > WINDOW:
                continue
            # podobieństwo do najbliższego artykułu w klastrze (słownik całego klastra rośnie i rozmywa wynik)
            sim = max(jaccard(tok, t) for t in c.token_sets)
            if sim > best_sim:
                best, best_sim = c, sim
        if best is not None and best_sim >= SIMILARITY:
            best.articles.append(art)
            best.token_sets.append(tok)
        else:
            clusters.append(Cluster([art], [tok]))
    return clusters
