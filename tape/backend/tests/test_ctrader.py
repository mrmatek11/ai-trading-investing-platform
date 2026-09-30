import io
from datetime import datetime, timezone
from decimal import Decimal

from openpyxl import Workbook

from tape.engine.positions import build_positions
from tape.importers import detect_broker, parse_file

CSV = """ID,Symbol,Opening Direction,Opening Time (UTC+2),Closing Time (UTC+2),Entry Price,Closing Price,Closing Quantity,Commission,Swap,Gross USD,Net USD,Balance USD
PID101,XAUUSD,Buy,10/09/2026 10:00:00,10/09/2026 14:30:00,2650.10,2670.10,0.50 Lots,-3.50,0.00,1000.00,996.50,100996.50
PID102,XAGUSD,Sell,11/09/2026 09:15:00,12/09/2026 16:00:00,31.500,31.200,1500 Oz,-2.00,-1.25,450.00,446.75,101443.25
PID103,XAUUSD,Hold,11/09/2026 09:15:00,12/09/2026 16:00:00,1,1,1 Lots,0,0,0,0,0
"""


def test_detect_and_parse_csv():
    data = CSV.encode()
    assert detect_broker(data, "history.csv") == "ctrader"
    res = parse_file(data, "history.csv")
    assert res.detected == "ctrader" and len(res.fills) == 4
    assert len(res.errors) == 1 and "hold" in res.errors[0]
    o, c, so, sc = res.fills
    assert o.ts == datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)                # UTC+2 z nagłówka
    assert (o.side, c.side, o.qty, o.contract_size) == ("buy", "sell", Decimal("0.50"), Decimal(100))
    assert c.broker_pnl == Decimal("1000.00") and c.fee == Decimal("-3.50")
    assert (so.side, so.qty, so.contract_size) == ("sell", Decimal(1500), Decimal(1))   # ilość w uncjach
    pos = {p.symbol: p for p in build_positions(res.fills)}
    assert pos["XAUUSD"].net_pnl == Decimal("996.50")
    assert pos["XAGUSD"].net_pnl == Decimal("446.75")


def test_negative_utc_offset_and_net_only():
    csv = CSV.replace("(UTC+2)", "(UTC-4)").replace(",Gross USD", ",Gross EUR_x").replace("1000.00,996.50", ",996.50")
    res = parse_file(csv.encode(), "h.csv")
    o, c = res.fills[:2]
    assert o.ts == datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
    assert c.broker_pnl == Decimal("1000.00")                                         # z Net − koszty


def test_xlsx_with_report_header():
    wb = Workbook()
    ws = wb.active
    ws.append(["cTrader history report"])
    for line in CSV.splitlines()[:2]:
        ws.append(line.split(","))
    buf = io.BytesIO()
    wb.save(buf)
    res = parse_file(buf.getvalue(), "history.xlsx")
    assert res.detected == "ctrader" and len(res.fills) == 2 and not res.errors
    assert res.fills[0].ts.hour == 8
