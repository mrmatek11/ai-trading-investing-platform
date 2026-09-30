from datetime import datetime, timezone

import pytest

from tape.news import rss
from tape.news.rss import Feed

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

RSS2 = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>FX news</title>
<item><title>Gold climbs as Fed rate cut bets rise</title><link>https://fx.example/a1</link>
<pubDate>Tue, 29 Sep 2026 09:15:00 +0200</pubDate><description>&lt;p&gt;Spot gold &amp;amp; silver &lt;b&gt;up&lt;/b&gt;&lt;/p&gt;</description></item>
<item><title>Football transfer news</title><link>https://fx.example/a2</link><pubDate>Tue, 29 Sep 2026 09:00:00 GMT</pubDate></item>
<item><title>No date: silver</title><link>https://fx.example/a3</link></item>
<item><title>Old gold story</title><link>https://fx.example/a4</link><pubDate>Mon, 01 Sep 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Fed</title>
<entry><title type="html">FOMC statement</title>
<link rel="self" href="https://fed.example/self.xml"/><link rel="alternate" href="https://fed.example/fomc"/>
<updated>2026-09-29T11:00:00Z</updated><summary>The Committee decided to maintain the target range.</summary></entry>
</feed>"""

RDF = b"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<item rdf:about="https://bls.example/cpi"><title>Consumer Price Index Summary</title><link>https://bls.example/cpi</link>
<dc:date>2026-09-29T08:30:00-04:00</dc:date></item></rdf:RDF>"""

BOMB = b"""<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>
<rss><channel><item><title>&lol2;</title></item></channel></rss>"""


def test_rss2_filter_clean_and_dates():
    items = rss.parse(RSS2, Feed("FX", "https://fx.example/rss"))
    assert [a.url for a in items] == ["https://fx.example/a1", "https://fx.example/a4"]   # bez piłki i bez daty
    a = items[0]
    assert a.published_at == datetime(2026, 9, 29, 7, 15, tzinfo=timezone.utc)            # +0200 → UTC
    assert a.text == "Gold climbs as Fed rate cut bets rise. Spot gold & silver up"        # HTML i encje usunięte
    unfiltered = rss.parse(RSS2, Feed("FX", "https://fx.example/rss", filter=False))
    assert "https://fx.example/a2" in [x.url for x in unfiltered]


def test_atom_prefers_alternate_link_and_rdf_dc_date():
    [fomc] = rss.parse(ATOM, Feed("Fed", "https://fed.example", filter=False))
    assert fomc.url == "https://fed.example/fomc" and fomc.title == "FOMC statement"
    [cpi] = rss.parse(RDF, Feed("BLS", "https://bls.example", filter=False))
    assert cpi.published_at == datetime(2026, 9, 29, 12, 30, tzinfo=timezone.utc)


def test_entities_are_rejected():
    with pytest.raises(ValueError):
        rss.parse(BOMB, Feed("x", "https://x.example"))


def test_fetch_all_isolates_failures_and_drops_old_and_duplicates():
    feeds = [Feed("FX", "https://fx.example/rss"), Feed("dup", "https://fx2.example/rss"),
             Feed("down", "https://down.example/rss"), Feed("bomb", "https://bomb.example/rss")]
    pages = {"https://fx.example/rss": RSS2, "https://fx2.example/rss": RSS2, "https://bomb.example/rss": BOMB}

    def opener(url):
        if url not in pages:
            raise OSError("connection refused")
        return pages[url]

    rep = rss.fetch_all(feeds, opener, now=NOW)
    assert [a.url for a in rep.articles] == ["https://fx.example/a1"]          # stary wpis i duplikat odpadają
    assert rep.feeds["FX"] == "ok (1)" and rep.feeds["dup"] == "ok (0)"
    assert rep.feeds["down"].startswith("błąd") and rep.feeds["bomb"].startswith("błąd")


def test_feeds_from_env():
    assert rss.feeds_from_env("") == list(rss.DEFAULT_FEEDS)
    fs = rss.feeds_from_env("Kitco|https://kitco.example/rss|0; bad|http://insecure.example ;X|https://x.example/feed")
    assert fs == [Feed("Kitco", "https://kitco.example/rss", False), Feed("X", "https://x.example/feed", True)]
