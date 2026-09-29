from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tape.engine.positions import build_positions
from tape.importers import detect_broker, ibkr, parse_file

FLEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="tape" type="AF">
 <FlexStatements count="1"><FlexStatement accountId="U1234567" fromDate="20260901" toDate="20260930">
  <Trades>
   <Trade tradeID="501" assetCategory="FUT" symbol="GCZ6" underlyingSymbol="GC" buySell="BUY" quantity="1"
          tradePrice="2690.5" multiplier="100" ibCommission="-2.25" currency="USD"
          dateTime="20260910;093000" openCloseIndicator="O" fifoPnlRealized="0" levelOfDetail="EXECUTION"/>
   <Trade tradeID="502" assetCategory="FUT" symbol="GCZ6" underlyingSymbol="GC" buySell="SELL" quantity="-1"
          tradePrice="2702.3" multiplier="100" ibCommission="-2.25" currency="USD"
          dateTime="20260910;143000" openCloseIndicator="C" fifoPnlRealized="1177.75" levelOfDetail="EXECUTION"/>
   <Trade tradeID="503" assetCategory="FUT" symbol="SIZ6" buySell="SELL" quantity="-2" tradePrice="31.40"
          multiplier="5000" ibCommission="-4.5" currency="USD" dateTime="2026-09-11;10:00:00"
          openCloseIndicator="O" levelOfDetail="EXECUTION"/>
   <Trade tradeID="504" assetCategory="FUT" symbol="SIZ6" buySell="HOLD" quantity="1" tradePrice="31.40"
          multiplier="5000" dateTime="20260911;110000"/>
  </Trades>
 </FlexStatement></FlexStatements>
</FlexQueryResponse>"""


def test_detect_and_parse_futures():
    assert detect_broker(FLEX, "flex.xml") == "ibkr"
    res = parse_file(FLEX, "flex.xml")
    assert len(res.fills) == 3 and len(res.errors) == 1 and "HOLD" in res.errors[0]
    buy, sell, silver = res.fills
    assert (buy.symbol, buy.side, buy.qty, buy.contract_size, buy.fee) == ("GCZ6", "buy", 1, 100, Decimal("-2.25"))
    assert buy.ts == datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)      # 09:30 EDT = 13:30 UTC
    assert buy.broker_pnl is None and sell.broker_pnl == Decimal("1177.75")
    assert (silver.qty, silver.contract_size, silver.ts.hour) == (2, 5000, 14)

    gold = [p for p in build_positions(res.fills) if p.symbol == "GCZ6"][0]
    assert gold.net_pnl == Decimal("1177.75") - Decimal("4.50")               # PnL brokera + prowizje


def test_bad_xml_and_missing_trades():
    assert parse_file(b"<FlexQueryResponse><broken", "x.xml").errors
    res = ibkr.parse(b"<FlexQueryResponse><FlexStatements/></FlexQueryResponse>")
    assert res.fills == [] and "Trades" in res.errors[0]
    assert detect_broker(b"<html><body>nie ten plik</body></html>", "x.html") is None
    bomb = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
            b"<FlexQueryResponse><Trades><Trade tradeID='&lol2;'/></Trades></FlexQueryResponse>")
    assert "niebezpieczny" in ibkr.parse(bomb).errors[0]


def test_flex_web_service_flow():
    calls = []

    def opener(url):
        calls.append(url)
        if "SendRequest" in url:
            return b"<FlexStatementResponse><Status>Success</Status><ReferenceCode>999</ReferenceCode></FlexStatementResponse>"
        if len(calls) == 2:
            return b"<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode><ErrorMessage>generating</ErrorMessage></FlexStatementResponse>"
        return FLEX

    body = ibkr.fetch_statement("tok", "123", opener=opener, wait_seconds=0)
    assert body == FLEX and "q=999" in calls[-1] and "t=tok" in calls[0]

    def fail(url):
        return b"<FlexStatementResponse><Status>Fail</Status><ErrorMessage>Invalid token</ErrorMessage></FlexStatementResponse>"

    with pytest.raises(RuntimeError, match="Invalid token"):
        ibkr.fetch_statement("bad", "123", opener=fail)
