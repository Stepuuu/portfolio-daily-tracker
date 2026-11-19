"""Per-account fee settings, stored in ``desk.sqlite3`` (table ``account_profiles``).

Rates are fractions of turnover as Decimal strings (``"0.00025"`` = 万2.5).
A profile::

    {"broker": "", "a_share": {"commission_rate": "0.00025", "commission_min": "5",
                               "waive_minimum": false},
     "hk": null | {"buy": null | {"rate", "min", "fixed"}, "sell": null | {...}}, "us": same}

``waive_minimum`` is the 免五 switch. HK/US models are in the trade currency.
Every side that is given states ``rate``, ``min`` and ``fixed`` explicitly; a
missing value is never read as zero. A side that is omitted or ``null`` has
no model, so that side gets no estimate. For convenience ``hk``/``us`` may be
given as one complete ``{"rate","min","fixed"}``; it is stored for both sides.
A model without any side is refused (send ``null`` for the market instead).
An account without a saved profile gets the preset with ``is_default: true``.
"""
from __future__ import annotations

import copy
import json
from decimal import Decimal
from typing import Any, Dict, List, Optional

from core import desk_store, fees

SCHEMA = "account_profiles"
MIGRATIONS = [[
    "CREATE TABLE account_profiles (account TEXT PRIMARY KEY, profile TEXT NOT NULL, updated_at TEXT NOT NULL)",
]]
desk_store.register_schema(SCHEMA, MIGRATIONS)

# The estimator's bounds, so every stored value can be used in an estimate.
MAX_RATE = fees.MAX_RATE            # 1% of turnover
MAX_AMOUNT = fees.MAX_AMOUNT        # minimum / fixed fee per trade
RATE_SCALE = fees.RATE_SCALE        # decimal places kept for a rate
AMOUNT_SCALE = fees.AMOUNT_SCALE    # decimal places kept for an amount
PRESET = {
    "broker": "",
    "a_share": {"commission_rate": "0.00025", "commission_min": "5", "waive_minimum": False},
    "hk": None,
    "us": None,
}


class ProfileError(ValueError):
    """Invalid account name or profile; the message is Chinese and safe to show."""


def preset() -> dict:
    out = copy.deepcopy(PRESET)
    out["is_default"] = True
    return out


def check_account(account) -> str:
    if not isinstance(account, str) or not account.strip() or len(account.strip()) > 240 \
            or any(ord(c) < 32 for c in account):
        raise ProfileError("账户名不能为空，且不超过 240 个字符。")
    return account.strip()


def _dec(value, name: str, maximum: Decimal, scale: int) -> str:
    """The value as a plain decimal string, exactly as sent (trailing zeros dropped).

    A value with more than ``scale`` decimal places is refused rather than
    rounded, so what is stored is always exactly what was sent.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ProfileError(f"{name}必须是数字字符串。")
    try:
        return format(fees.bounded_decimal(value, name, maximum, scale), "f")
    except fees.FeeInputError as exc:
        raise ProfileError(str(exc)) from None


SIDE_FIELDS = ("rate", "min", "fixed")


def _side(raw, market: str) -> dict:
    if not isinstance(raw, dict) or set(raw) - set(SIDE_FIELDS):
        raise ProfileError(f"{market}费率只能包含 rate、min、fixed。")
    if any(raw.get(name) is None for name in SIDE_FIELDS):
        raise ProfileError(f"{market}费率需要同时填写 rate、min、fixed（不收的项填 0）。")
    return {
        "rate": _dec(raw["rate"], f"{market}费率", MAX_RATE, RATE_SCALE),
        "min": _dec(raw["min"], f"{market}最低收费", MAX_AMOUNT, AMOUNT_SCALE),
        "fixed": _dec(raw["fixed"], f"{market}固定费用", MAX_AMOUNT, AMOUNT_SCALE),
    }


def _model(raw, market: str) -> Optional[dict]:
    if raw is None:
        return None
    if not isinstance(raw, dict) or not raw:
        raise ProfileError(f"{market}费率模型格式不对。不设置该市场时请填 null。")
    if set(raw) & {"buy", "sell"}:
        if set(raw) - {"buy", "sell"}:
            raise ProfileError(f"{market}费率模型格式不对。")
        sides = {s: None if raw.get(s) is None else _side(raw[s], market) for s in ("buy", "sell")}
        if sides["buy"] is None and sides["sell"] is None:
            raise ProfileError(f"{market}费率模型至少要设置买入或卖出一边。不设置该市场时请填 null。")
        return sides
    shared = _side(raw, market)
    return {"buy": dict(shared), "sell": dict(shared)}


def validate(profile) -> dict:
    """Return the normalised profile or raise ``ProfileError``."""
    if not isinstance(profile, dict):
        raise ProfileError("费率设置必须是一个对象。")
    unknown = set(profile) - {"broker", "a_share", "hk", "us", "is_default", "account", "updated_at"}
    if unknown:
        raise ProfileError("未知字段：" + "、".join(sorted(map(str, unknown))))
    broker = profile.get("broker", "")
    if not isinstance(broker, str) or len(broker.strip()) > 40 or any(ord(c) < 32 for c in broker):
        raise ProfileError("券商名称不超过 40 个字符。")
    a = profile.get("a_share", PRESET["a_share"])
    if not isinstance(a, dict) or set(a) - {"commission_rate", "commission_min", "waive_minimum"}:
        raise ProfileError("A 股费率只能包含 commission_rate、commission_min、waive_minimum。")
    waive = a.get("waive_minimum", False)
    if not isinstance(waive, bool):
        raise ProfileError("免五开关必须是 true 或 false。")
    return {
        "broker": broker.strip(),
        "a_share": {
            "commission_rate": _dec(a.get("commission_rate", PRESET["a_share"]["commission_rate"]), "佣金率",
                                    MAX_RATE, RATE_SCALE),
            "commission_min": _dec(a.get("commission_min", PRESET["a_share"]["commission_min"]), "最低佣金",
                                   MAX_AMOUNT, AMOUNT_SCALE),
            "waive_minimum": waive,
        },
        "hk": _model(profile.get("hk"), "港股"),
        "us": _model(profile.get("us"), "美股"),
    }


def _row(row) -> dict:
    out = json.loads(row["profile"])
    out["is_default"] = False
    out["account"] = row["account"]
    out["updated_at"] = row["updated_at"]
    return out


def get(account: str) -> dict:
    account = check_account(account)
    row = None
    if desk_store.ready(SCHEMA, MIGRATIONS):
        with desk_store.connect() as db:
            row = db.execute("SELECT * FROM account_profiles WHERE account=?", (account,)).fetchone()
    if row is None:
        out = preset()
        out["account"] = account
        return out
    return _row(row)


def list() -> List[dict]:  # noqa: A001 - mirrors the other desk modules
    """Saved profiles only, by account name."""
    if not desk_store.ready(SCHEMA, MIGRATIONS):
        return []
    with desk_store.connect() as db:
        rows = db.execute("SELECT * FROM account_profiles ORDER BY account").fetchall()
    return [_row(r) for r in rows]


def save(account: str, profile: Dict[str, Any]) -> dict:
    account = check_account(account)
    clean = validate(profile)
    desk_store.ensure_schema(SCHEMA, MIGRATIONS)
    with desk_store.transaction() as db:
        db.execute(
            "INSERT INTO account_profiles(account, profile, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(account) DO UPDATE SET profile=excluded.profile, updated_at=excluded.updated_at",
            (account, desk_store.encode(clean), desk_store.now()))
    return get(account)
