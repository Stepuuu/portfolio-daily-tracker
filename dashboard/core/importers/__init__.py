"""Broker file importer: turn a 交割单 / 对账单 / 持仓 export into ledger proposals.

Nothing here writes to the ledger. The importer produces rows with a status and,
for bookable rows, standard ledger events; the person reviews them and the
existing propose → confirm step books them.

Modules:

* ``sniff``: file type, encoding, delimiter, header row, size and row limits.
* ``values``: decimals, dates and times from cells (never guessed).
* ``codes``: bare security code → ``EXCH:CODE``, currency and instrument kind.
* ``mapping``: column synonyms, vendor presets, saved mappings.
* ``transactions``: business type → ledger events, fees, 逆回购, 港股通.
* ``holdings``: a holdings statement → opening event or reconciliation table.
* ``accounts``: account identity columns (资金账号, 股东代码) and which rows belong.
* ``dedupe``: stable external ids, already-imported and changed rows, fuzzy duplicates.
* ``reconcile``: what the ledger would say about each row, and the file's balances.
* ``aliases``: the ``instrument_aliases`` table in ``desk.sqlite3``.
* ``session``: import sessions in ``desk.sqlite3`` and the batches they propose.

The rule everywhere: a money value that is not in the file and cannot be derived
exactly from values that are puts the row in "needs review". It is never 0. The
same holds for anything else that is uncertain (a unit, a currency, an exchange,
the account a row belongs to, which leg pairs with which): review, never a guess.
"""


# The version of the rules that turn a file into events. Every ledger proposal the importer makes is bound to a
# fingerprint that includes it (``session.fingerprint``), so a proposal prepared under other rules can never be
# confirmed. Bump it whenever a change to parsing, mapping, classification, dedupe or account rules could give
# the same stored session different events or statuses.
#   1  (implicit) every release before this constant existed
#   2  review round 3: duplicates of splits, account identity memory, units, signs, currencies, workbook cells
#   3  cash to the cent: a derived price whose product with the quantity is exactly 成交金额, a fee within a cent
#      of the cash amount taken from the cash amount
#   4  release gate: a day without times ordered so it can be afforded, a listing's own dollar, a dividend's posted
#      amount when gross − withheld = posted, native workbook numbers read exactly, a 港股通 fee stated in HKD
INTERPRETATION_VERSION = 4


class ImportFailure(ValueError):
    """A user-facing problem with an import. ``status`` is the HTTP status (4xx)."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status
