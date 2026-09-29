import io
import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from tape.api import create_app
from tape.db import make_sessionmaker
from tape.news import classify, gdelt, track_record
from tape.news.classify import Article
from tape.news.cluster import cluster
from tape.news.pipeline import run_once
from tape.news.store import BiasSnapshot, EventRow

NOW = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)


def art(title, domain, minutes_ago, now=NOW):
    return Article(title=title, text=title, url=f"https://{domain}/{abs(hash((title, domain))) % 10**8}",
                   published_at=now - timedelta(minutes=minutes_ago))


def test_gdelt_parse():
    payload = json.dumps({"articles": [
        {"url": "https://a.com/1", "title": "Gold jumps as tanker struck in Strait of Hormuz", "seendate": "20260928T193000Z"},
        {"url": "https://b.com/2", "title": "", "seendate": "20260928T193000Z"},
        {"url": "https://c.com/3", "title": "Bad date", "seendate": "yesterday"},
    ]})
    out = gdelt.parse(payload)
    assert len(out) == 1 and out[0].published_at == datetime(2026, 9, 28, 19, 30, tzinfo=timezone.utc)
    assert gdelt.parse("<html>rate limited</html>") == []
    assert "mode=ArtList" in gdelt.build_url() and "timespan=1h" in gdelt.build_url()


def test_cluster_groups_same_story_only():
    items = [
        art("Tanker struck near Strait of Hormuz, oil and gold rise", "reuters.com", 50),
        art("Oil, gold rise after tanker struck near Strait of Hormuz", "bloomberg.com", 40),
        art("Fed official signals rates higher for longer", "cnbc.com", 30),
    ]
    groups = cluster(items)
    assert sorted(len(g.articles) for g in groups) == [1, 2]
    hormuz = next(g for g in groups if len(g.articles) == 2)
    assert hormuz.domains == 2
    assert cluster(items)[0].id == cluster(list(reversed(items)))[0].id   # stabilny identyfikator


class FakeMessages:
    """Klasyfikuje każdy klaster jako byczy dla złota, cytując tytuł pierwszego artykułu."""

    def __init__(self):
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        prompt = kwargs["messages"][0]["content"]
        title = re.search(r"\[0\] (.+)", prompt).group(1)
        impact = classify.AssetImpact(direction=1, magnitude=4, horizon="days", channel="safe_haven", reasoning="…")
        parsed = classify.EventClassification(
            title=title[:60], category="conflict", place="Zatoka Perska", lat=26.6, lon=56.3,
            xau=impact, xag=impact, novelty="new", confidence=0.9, summary="…",
            evidence=[classify.Evidence(quote=title, source_index=0)])
        return SimpleNamespace(parsed_output=parsed, stop_reason="end_turn")


def fake_client():
    msgs = FakeMessages()
    return SimpleNamespace(beta=SimpleNamespace(messages=msgs)), msgs


STORY = [
    ("Tanker struck near Strait of Hormuz, oil and gold rise", "reuters.com", 50),
    ("Oil, gold rise after tanker struck near Strait of Hormuz", "bloomberg.com", 40),
    ("Fed official signals rates higher for longer", "cnbc.com", 30),     # jedno medium — pomijane
]


def test_pipeline_classifies_once_and_logs_snapshots(tmp_path):
    Session = make_sessionmaker(f"sqlite:///{tmp_path / 'n.db'}")
    client, msgs = fake_client()
    fetch = lambda: [art(*x) for x in STORY]  # noqa: E731

    with Session() as s:
        rep = run_once(s, client, fetch, NOW)
    assert (rep.new_articles, rep.classified, rep.snapshots) == (3, 1, 2)
    assert msgs.calls == 1

    with Session() as s:  # ten sam wsad: brak nowych klasyfikacji, ale nowe oceny w logu
        rep = run_once(s, client, fetch, NOW + timedelta(minutes=15))
        assert rep.new_articles == 0 and rep.classified == 0
        assert s.query(BiasSnapshot).count() == 4
        assert s.query(EventRow).count() == 1

    # 30 h później: pierwsze artykuły wypadły z okna 24 h, nowy tekst o tej samej sprawie
    # dołącza do klastra — zdarzenie dziedziczy identyfikator i NIE jest klasyfikowane ponownie.
    later = NOW + timedelta(hours=30)
    follow = [art("Tanker struck near Strait of Hormuz, gold rise continues", "ft.com", 10, later),
              art("Strait of Hormuz tanker struck: oil and gold rise again", "wsj.com", 5, later)]
    with Session() as s:
        first_id = s.query(EventRow).one().id
    with Session() as s:
        from tape.news.store import ArticleRow
        # Drugi artykuł sprawy (Bloomberg) nadal jest w oknie; pierwszy (Reuters), od którego
        # pochodzi identyfikator zdarzenia, już wypadł — nowy klaster ma więc inny „naturalny” id.
        bloomberg = s.query(ArticleRow).filter(ArticleRow.url.like("https://bloomberg.com/%")).one()
        bloomberg.published_at = later - timedelta(hours=6)
        s.commit()
        rep = run_once(s, client, lambda: follow, later)
        assert rep.classified == 0
    assert msgs.calls == 1
    with Session() as s:
        assert s.query(EventRow).count() == 1 and s.query(EventRow).one().id == first_id


def test_track_record_hits_and_no_overlap():
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    prices = [(t0 + timedelta(hours=h), 2000 + h * 0.5) for h in range(24 * 40)]   # stały wzrost
    snaps = [(t0 + timedelta(hours=h), 0.5, "long") for h in range(0, 24 * 30)]      # ocena co godzinę
    tr = track_record.evaluate(snaps, prices)
    assert tr.observations == 30                 # jedna ocena na 24-godzinne okno
    assert tr.hit_rate == 1.0 and tr.mean_signed_return > 0
    assert not tr.labels_allowed                 # < 200 obserwacji i < 12 miesięcy
    shorts = [(ts, -0.5, "short") for ts, _, _ in snaps]
    assert track_record.evaluate(shorts, prices).hit_rate == 0.0
    assert track_record.evaluate([(t0, 0.0, "neutral")], prices).observations == 0


def test_api_uses_stored_events_and_prices(tmp_path):
    url = f"sqlite:///{tmp_path / 'api.db'}"
    Session = make_sessionmaker(url)
    client_ai, _ = fake_client()
    now = datetime.now(timezone.utc)
    with Session() as s:
        run_once(s, client_ai, lambda: [art(t, d, m, now) for t, d, m in STORY], now)
    api = TestClient(create_app(url))
    events = api.get("/api/events").json()
    assert len(events) == 1 and events[0]["sample"] is False
    bias = api.get("/api/bias").json()
    assert bias["sample"] is False and bias["XAU"]["label"] in ("long", "neutral")
    assert bias["track_record"]["available"] is False and "Zapisanych ocen: 1" in bias["track_record"]["note"]
    csv = "timestamp,close\n2026-01-01 00:00:00,2600\n2026-01-01 01:00:00,2601.5\nbad,row\n"
    r = api.post("/api/prices", files={"file": ("p.csv", io.BytesIO(csv.encode()), "text/csv")}, data={"asset": "XAU"})
    assert r.json() == {"added": 2, "rows": 3}


def _reference_cluster(articles):
    """Pierwotny algorytm (porównanie z każdym klastrem) — wzorzec dla zoptymalizowanej wersji."""
    from tape.news.cluster import SIMILARITY, WINDOW, Cluster, jaccard, tokens

    clusters = []
    for art in sorted(articles, key=lambda a: a.published_at):
        tok = tokens(art.title)
        best, best_sim = None, 0.0
        for c in clusters:
            if art.published_at - max(a.published_at for a in c.articles) > WINDOW:
                continue
            sim = max(jaccard(tok, t) for t in c.token_sets)
            if sim > best_sim:
                best, best_sim = c, sim
        if best is not None and best_sim >= SIMILARITY:
            best.articles.append(art)
            best.token_sets.append(tok)
        else:
            clusters.append(Cluster([art], [tok]))
    return clusters


def test_cluster_matches_reference_algorithm():
    import random
    from datetime import datetime, timedelta, timezone

    from tape.news.classify import Article
    from tape.news.cluster import cluster

    r = random.Random(11)
    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    vocab = [f"w{i}" for i in range(400)] + ["gold", "fed", "the", "a"]
    stories = [(r.sample(vocab, 8), now - timedelta(hours=r.randint(0, 60))) for _ in range(60)]
    arts = []
    for i in range(600):
        words, t0 = r.choice(stories)
        words = words[:]
        for _ in range(r.randint(0, 5)):
            words[r.randrange(len(words))] = r.choice(vocab)
        title = " ".join(words) if i % 50 else "the a of"           # także tytuły bez słów kluczowych
        arts.append(Article(title=title, text="", url=f"https://s{r.randint(0, 30)}.example/{i}",
                            published_at=t0 + timedelta(minutes=r.choice([0, 1, 720, 721, r.randint(0, 1500)]))))
    got, want = cluster(arts), _reference_cluster(arts)
    assert [[a.url for a in c.articles] for c in got] == [[a.url for a in c.articles] for c in want]
    assert [c.id for c in got] == [c.id for c in want] and 20 < len(got) < 600
