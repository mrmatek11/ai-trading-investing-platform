import io
from datetime import datetime, timezone
from decimal import Decimal

from openpyxl import Workbook

from tape.importers import detect_broker, generic, parse_file
from tape.importers.base import to_decimal, to_utc
from tape.importers.instruments import infer_contract_size, normalize_symbol


def xtb_xlsx(rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "Closed positions"
    ws.append(["Raport zamkniętych pozycji", None, None])      # nagłówek raportu nad tabelą
    ws.append([])
    ws.append(["Position", "Symbol", "Type", "Volume", "Open time", "Open price", "Close time",
               "Close price", "SL", "TP", "Commission", "Swap", "Rollover", "Gross P/L", "Comment"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_value_parsing():
    assert to_decimal("1 234,56") == Decimal("1234.56")
    assert to_decimal("1,234.56") == Decimal("1234.56")
    assert to_decimal("-0.00") == Decimal("0.00")
    assert to_decimal("", allow_empty=True) is None
    # 12.09 09:31 w Warszawie (CEST, UTC+2) → 07:31 UTC
    assert to_utc("12.09.2026 09:31:05", "Europe/Warsaw") == datetime(2026, 9, 12, 7, 31, 5, tzinfo=timezone.utc)


def test_symbols_and_contract_size():
    assert normalize_symbol("GOLD") == "XAUUSD"
    assert normalize_symbol("XAUUSD.pro") == "XAUUSD"
    assert normalize_symbol("XAGUSDm") == "XAGUSD"
    assert normalize_symbol("EURUSD") == "EURUSD"
    # 0,1 lota złota, +12,5 USD ruchu → 125 USD: 100 oz na lot
    assert infer_contract_size(Decimal("2672.40"), Decimal("2684.90"), Decimal("0.10"), 1, Decimal("125.00")) == 100


def test_xtb_closed_positions_xlsx():
    data = xtb_xlsx([
        [1482203911, "GOLD", "BUY", 0.10, datetime(2026, 9, 12, 9, 31, 5), 2672.40, datetime(2026, 9, 12, 15, 2, 44),
         2684.90, 2660.40, 0, 0, -1.24, 0, 125.00, ""],
        [1482203912, "SILVER", "SELL", 0.05, datetime(2026, 9, 13, 10, 0), 31.60, datetime(2026, 9, 13, 12, 0),
         31.40, 31.90, 0, 0, 0, 0, 50.00, ""],
    ])
    assert detect_broker(data, "closed.xlsx") == "xtb"
    res = parse_file(data, "closed.xlsx")
    assert res.errors == []
    assert len(res.fills) == 4
    o, c = res.fills[0], res.fills[1]
    assert (o.symbol, o.side, o.contract_size, o.stop_loss) == ("XAUUSD", "buy", 100, Decimal("2660.4"))
    assert o.ts == datetime(2026, 9, 12, 7, 31, 5, tzinfo=timezone.utc)
    assert (c.side, c.fee, c.broker_pnl) == ("sell", Decimal("-1.24"), Decimal("125.0"))
    s_open = res.fills[2]
    assert (s_open.symbol, s_open.side, s_open.contract_size) == ("XAGUSD", "sell", 5000)


def test_xtb_bad_row_is_reported_not_fatal():
    data = xtb_xlsx([
        [1, "GOLD", "BUY", 0.1, datetime(2026, 9, 12, 9, 0), 2600, datetime(2026, 9, 12, 10, 0), 2610, None, 0, 0, 0, 0, 100, ""],
        [2, "GOLD", "HOLD", 0.1, datetime(2026, 9, 12, 9, 0), 2600, datetime(2026, 9, 12, 10, 0), 2610, None, 0, 0, 0, 0, 100, ""],
    ])
    res = parse_file(data, "x.xlsx")
    assert len(res.fills) == 2
    assert len(res.errors) == 1 and "HOLD" in res.errors[0]


MT5_CSV = """Time;Deal;Symbol;Type;Direction;Volume;Price;Order;Commission;Fee;Swap;Profit;Balance;Comment
2026.09.10 09:00:00;100;;balance;;;;;0;0;0;10000;10000;deposit
2026.09.10 10:00:00;101;XAUUSD.pro;buy;in;0.20;2650.00;201;-1.40;0;0;0;10000;
2026.09.10 12:00:00;102;XAUUSD.pro;sell;out;0.10;2660.00;202;-0.70;0;0;100.00;10100;
2026.09.10 13:00:00;103;XAUUSD.pro;sell;out;0.10;2640.00;203;-0.70;0;-0.50;-100.00;10000;
"""


def test_mt5_deals_csv():
    data = MT5_CSV.encode()
    assert detect_broker(data, "deals.csv") == "mt5"
    res = parse_file(data, "deals.csv")
    assert res.errors == []
    assert [f.external_id for f in res.fills] == ["101", "102", "103"]
    assert res.fills[0].ts == datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)   # serwer UTC+2
    assert res.fills[0].broker_pnl is None and res.fills[1].broker_pnl == Decimal("100.00")
    assert res.fills[2].fee == Decimal("-1.20")


def test_generic_mapping():
    csv_data = "Trade ID,When,Instrument,Action,Qty,Fill Price,Costs\nA1,2026-09-01 10:00:00,XAUUSD,Kupno,1,2600,-2\n".encode()
    m = generic.Mapping(columns={"id": "Trade ID", "time": "When", "symbol": "Instrument", "side": "Action",
                                 "qty": "Qty", "price": "Fill Price", "fee": "Costs"}, tz="Europe/Warsaw")
    res = parse_file(csv_data, "any.csv", mapping=m)
    assert res.errors == []
    f = res.fills[0]
    assert (f.side, f.fee, f.contract_size) == ("buy", Decimal("-2"), 100)
    assert f.ts.hour == 8


def test_generic_mapping_requires_fields():
    res = parse_file(b"a,b\n1,2\n", "x.csv", mapping=generic.Mapping(columns={"id": "a"}))
    assert res.fills == [] and "time" in res.errors[0]
