# Synthetic broker exports

Every file here is **synthetic**. Account numbers, shareholder codes, trade numbers,
prices, amounts and balances are invented. Security codes and names are public
listings used only so the code tables can be exercised; the trades never happened.

The layouts were written from commonly documented column names of 同花顺, 东方财富
and 通达信 exports and of broker web and app downloads. **They are not verified
against real exports.** Broker formats vary between versions and brokers. When the
owner supplies a redacted real sample, add it here as a new fixture and keep it.

| File | Imitates | Encoding / format |
|---|---|---|
| `ths_trades_gbk.xls` | 同花顺交割单 | GBK tab-separated text with an `.xls` name, title line, 合计 footer |
| `eastmoney_statement_bom.csv` | 东方财富-style 资金对账单 | UTF-8 with BOM, comma |
| `tdx_trades_gbk.txt` | 通达信 历史成交 | GBK tab-separated, `="..."` cells, newest first |
| `eastmoney_trades_html.xls` | web page saved as `.xls` | HTML table, GB2312 `meta` charset |
| `ths_holdings_gbk.xls` | 同花顺持仓 | GBK tab-separated text, summary lines above the header |
| `hk_connect_bom.csv` | 港股通 rows settled in CNY | UTF-8 with BOM, comma |

`make_fixtures.py` regenerates them; it computes the 资金余额 / 股份余额 columns
with `Decimal` so each file reconciles with the opening balance used by the tests
(`tests/importer_support.py`: 500,000.00 CNY, 1,000 SHA:600036, 2,000 SHE:000001).

Real `.xls` (BIFF8) and `.xlsx` workbooks are written at test time by
`tests/importer_support.py` (`xls_bytes`, `xlsx_bytes`) instead of being committed:
the public artifact check treats tracked `.xlsx` files as private data.
