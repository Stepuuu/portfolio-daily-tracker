"""Account fee profiles and the fee estimator. Mounted at ``/api/accounts``.

Profiles are settings and save directly (no propose/confirm). The estimate is
offered twice: ``POST`` (body) and ``GET`` (query string). The UI should use
GET, because the demo-mode middleware rejects every POST.

Every route exists in two forms that do the same thing:

* ``/profile?account=<name>`` and ``/fee-estimate?account=<name>``: work for
  every valid account name, including one that contains ``/``. Use these.
* ``/<name>/profile`` and ``/<name>/fee-estimate``: kept for names without
  ``/`` (a slash cannot be carried in one path segment, not even as ``%2F``).
"""
import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from backend.api.desk_errors import desk_call
from core import account_profiles, fees

router = APIRouter()

TICKER = re.compile(r"(SHA|SHE|HKG|NASDAQ|NYSE):[A-Z0-9.\-]{1,20}")


def attempt(operation):
    """Profile errors are a 422; a busy or unusable store is a 503 (see ``desk_errors``)."""
    try:
        return desk_call(operation)
    except account_profiles.ProfileError as exc:
        raise HTTPException(422, str(exc)) from None


def _text(value) -> Optional[str]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, str)):
        return str(value).strip()
    if isinstance(value, float):
        return repr(value)
    return None


DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _trade_date(when) -> date:
    """Absent, null or empty means today; anything else must be a ``YYYY-MM-DD`` string."""
    if when is None or when == "":
        return date.today()
    if not isinstance(when, str) or not DATE.fullmatch(when.strip()):
        raise HTTPException(422, "日期格式应为 YYYY-MM-DD。")
    try:
        return date.fromisoformat(when.strip())
    except ValueError:
        raise HTTPException(422, "日期格式应为 YYYY-MM-DD。") from None


def _estimate(account: str, ticker, side, quantity, price, when) -> dict:
    account = account_profiles.check_account(account)
    ticker = (ticker or "").strip().upper() if isinstance(ticker, str) else ""
    if not TICKER.fullmatch(ticker):
        raise HTTPException(422, "代码需带交易所前缀，例如 SHA:600519、HKG:0700、NASDAQ:AAPL。")
    if side not in ("buy", "sell"):
        raise HTTPException(422, "方向必须是 buy 或 sell。")
    numbers = {}
    for name, label, raw, read in (("quantity", "数量", quantity, fees.quantity_value),
                                   ("price", "价格", price, fees.price_value)):
        text = _text(raw)
        if not text:
            raise HTTPException(422, f"{label}必须是大于 0 的数字。")
        try:
            # Bounded in length, digits, exponent and magnitude; exponent notation becomes a plain number.
            numbers[name] = read(text)
        except fees.FeeInputError as exc:
            raise HTTPException(422, str(exc)) from None
    trade_date = _trade_date(when)
    profile = account_profiles.get(account)
    try:
        result = fees.estimate(profile, ticker, side, numbers["quantity"], numbers["price"], trade_date)
    except fees.FeeInputError as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "account": account, "ticker": ticker, "side": side,
        "quantity": format(numbers["quantity"], "f"), "price": format(numbers["price"], "f"),
        "date": trade_date.isoformat(), "currency": fees.MARKET_CURRENCY[ticker.split(":")[0]],
        "profile_is_default": profile["is_default"],
        **result.to_dict(),
    }


@router.get("/profiles")
def profiles():
    """Saved profiles plus the preset used for accounts without one."""
    return attempt(lambda: {"items": account_profiles.list(), "preset": account_profiles.preset()})


async def _json(request: Request):
    try:
        return await request.json()
    except ValueError:
        raise HTTPException(422, "请求内容不是有效的 JSON。") from None


async def _estimate_from_body(account: str, request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(422, "请求内容必须是 JSON 对象。")
    return attempt(lambda: _estimate(account, body.get("ticker"), body.get("side"), body.get("quantity"),
                                     body.get("price"), body.get("date")))


# -- account given as a query parameter (any valid name) ---------------------

@router.get("/profile")
def read_profile_by_query(account: str = ""):
    return attempt(lambda: account_profiles.get(account))


@router.put("/profile")
async def write_profile_by_query(request: Request, account: str = ""):
    body = await _json(request)
    return attempt(lambda: account_profiles.save(account, body))


@router.get("/fee-estimate")
def fee_estimate_get_by_query(account: str = "", ticker: str = "", side: str = "", quantity: str = "",
                              price: str = "", date: str = ""):
    """Preferred for the UI (also works in demo mode)."""
    return attempt(lambda: _estimate(account, ticker, side, quantity, price, date))


@router.post("/fee-estimate")
async def fee_estimate_post_by_query(request: Request, account: str = ""):
    return await _estimate_from_body(account, request)


# -- account given in the path (names without a slash) -----------------------

@router.get("/{account}/profile")
def read_profile(account: str):
    return attempt(lambda: account_profiles.get(account))


@router.put("/{account}/profile")
async def write_profile(account: str, request: Request):
    body = await _json(request)
    return attempt(lambda: account_profiles.save(account, body))


@router.get("/{account}/fee-estimate")
def fee_estimate_get(account: str, ticker: str = "", side: str = "", quantity: str = "",
                     price: str = "", date: str = ""):
    return attempt(lambda: _estimate(account, ticker, side, quantity, price, date))


@router.post("/{account}/fee-estimate")
async def fee_estimate_post(account: str, request: Request):
    return await _estimate_from_body(account, request)
