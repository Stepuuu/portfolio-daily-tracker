"""Bounded subprocess for explicit market data downloads."""
import json
from pathlib import Path
import sys

from core.lab_data import COLUMNS, validate_records
from core.lab_store import encode


def provider_symbol(symbol):
    """Translate portfolio exchange prefixes without changing dataset identity."""
    value = symbol.upper()
    if ":" in value:
        exchange, value = value.split(":", 1)
        if exchange in {"SHA", "SH", "SSE", "SHE", "SZ", "SZSE"} and value.isdigit():
            return value.zfill(6)
        if exchange in {"HKG", "HK", "HKEX"} and value.isdigit():
            return value.zfill(4) + ".HK"
        if exchange not in {"NASDAQ", "NYSE", "AMEX"}:
            raise ValueError("Unsupported exchange prefix")
    if value.endswith(".US"):
        return value[:-3]
    if value.endswith(".HK") and value[:-3].isdigit():
        return value[:-3].zfill(4) + ".HK"
    return value


def main():
    from backtesting.data.loader import DataLoader
    request = json.loads(Path(sys.argv[1]).read_text())
    loader = DataLoader()
    records = []
    try:
        for symbol in request["symbols"]:
            normalized = provider_symbol(symbol)
            if request["adjustment"] == "hfq" and (not normalized[0].isdigit() or normalized.endswith(".HK")):
                raise ValueError("The market provider does not supply backward-adjusted prices for this symbol")
            frame = loader.get_daily(normalized, request["start"], request["end"],
                                     adjust="" if request["adjustment"] == "none" else request["adjustment"])
            for day, row in frame.iterrows():
                records.append({"symbol": symbol, "date": day.strftime("%Y-%m-%d"),
                                **{col: float(row[col]) for col in COLUMNS}})
        output = {"records": validate_records(records)}
    except Exception:
        output = {"error": "行情下载或原始数据校验失败。请检查股票代码、数据源与所选日期；也可导入 CSV。"}
    Path(sys.argv[2]).write_text(encode(output), encoding="utf-8")


if __name__ == "__main__":
    main()
