"""HTTP contract of account profiles and the fee estimator."""
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import features
from backend.api import accounts as api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(api.router, prefix="/api/accounts")
    return TestClient(app)


NAME = "华泰 主账户"
Q = {"ticker": "SHA:600519", "side": "buy", "quantity": "100", "price": "1680.00", "date": "2026-11-14"}


def test_registered_in_features():
    assert any(prefix == "/api/accounts" for _r, prefix, _t in features.routers())


def test_default_profile_and_listing(client):
    body = client.get(f"/api/accounts/{quote(NAME)}/profile").json()
    assert body["is_default"] is True and body["a_share"]["commission_min"] == "5"
    listing = client.get("/api/accounts/profiles").json()
    assert listing["items"] == [] and listing["preset"]["is_default"] is True


def test_put_then_get_with_encoded_name(client):
    url = f"/api/accounts/{quote(NAME)}/profile"
    r = client.put(url, json={"broker": "huatai", "a_share": {"commission_rate": "0.0001", "waive_minimum": True}})
    assert r.status_code == 200 and r.json()["is_default"] is False
    assert client.get(url).json()["a_share"]["commission_rate"] == "0.0001"
    assert [p["account"] for p in client.get("/api/accounts/profiles").json()["items"]] == [NAME]


SLASHED = "华泰/主账户"


def test_account_with_a_slash_is_addressed_by_query(client):
    saved = client.put("/api/accounts/profile", params={"account": SLASHED}, json={"broker": "huatai"})
    assert saved.status_code == 200 and saved.json()["account"] == SLASHED
    assert client.get("/api/accounts/profile", params={"account": SLASHED}).json()["broker"] == "huatai"
    assert [p["account"] for p in client.get("/api/accounts/profiles").json()["items"]] == [SLASHED]
    estimate = client.get("/api/accounts/fee-estimate", params={**Q, "account": SLASHED})
    assert estimate.status_code == 200
    assert estimate.json()["account"] == SLASHED and estimate.json()["profile_is_default"] is False
    assert client.post("/api/accounts/fee-estimate", params={"account": SLASHED}, json=Q).json() == estimate.json()
    assert client.get(f"/api/accounts/{quote(SLASHED, safe='')}/profile").status_code == 404, \
        "the path form cannot carry a slash; that is why the query form exists"


def test_query_and_path_forms_address_the_same_profile(client):
    client.put(f"/api/accounts/{quote(NAME)}/profile", json={"broker": "path"})
    assert client.get("/api/accounts/profile", params={"account": NAME}).json()["broker"] == "path"
    client.put("/api/accounts/profile", params={"account": NAME}, json={"broker": "query"})
    assert client.get(f"/api/accounts/{quote(NAME)}/profile").json()["broker"] == "query"
    by_path = client.get(f"/api/accounts/{quote(NAME)}/fee-estimate", params=Q).json()
    assert client.get("/api/accounts/fee-estimate", params={**Q, "account": NAME}).json() == by_path


def test_query_forms_need_an_account(client):
    for response in (client.get("/api/accounts/profile"), client.put("/api/accounts/profile", json={}),
                     client.get("/api/accounts/fee-estimate", params=Q),
                     client.post("/api/accounts/fee-estimate", json=Q),
                     client.get("/api/accounts/profile", params={"account": " "})):
        assert response.status_code == 422 and "账户名" in response.json()["detail"]


@pytest.mark.parametrize("body", [{"a_share": {"commission_rate": "9"}}, {"zzz": 1}, [], "x"])
def test_put_rejects_bad_profile_in_chinese(client, body):
    r = client.put("/api/accounts/a/profile", json=body)
    assert r.status_code == 422 and any("一" <= c <= "鿿" for c in r.json()["detail"])


def test_put_rejects_non_json(client):
    r = client.put("/api/accounts/a/profile", content=b"{bad", headers={"content-type": "application/json"})
    assert r.status_code == 422


def test_estimate_get_and_post_agree(client):
    url = f"/api/accounts/{quote(NAME)}/fee-estimate"
    got = client.get(url, params=Q).json()
    assert got["total"] == "43.68" and got["currency"] == "CNY" and got["profile_is_default"] is True
    assert got["parts"] == [{"label": "佣金", "amount": "42.00"}, {"label": "过户费", "amount": "1.68"}]
    assert got["basis"] == "按默认费率估算" and got["reason"] is None
    assert client.post(url, json={**Q, "quantity": 100, "price": "1680.00"}).json() == got


def test_estimate_uses_saved_profile_and_sell(client):
    client.put("/api/accounts/a/profile", json={"a_share": {"waive_minimum": True}})
    body = client.get("/api/accounts/a/fee-estimate", params={**Q, "side": "sell", "quantity": "1", "price": "10"}).json()
    assert body["basis"] == "按账户费率估算"
    assert body["parts"][0] == {"label": "佣金", "amount": "0.00"} and body["total"] == "0.01"


def test_no_estimate_is_200_with_reason(client):
    body = client.get("/api/accounts/a/fee-estimate", params={**Q, "ticker": "HKG:0700"}).json()
    assert body["total"] is None and body["reason"] == "未设置该市场费率" and body["currency"] == "HKD"
    body = client.get("/api/accounts/a/fee-estimate", params={**Q, "ticker": "SHA:113050"}).json()
    assert body["total"] is None and body["reason"]


def test_hk_estimate_with_profile(client):
    client.put("/api/accounts/a/profile", json={"hk": {"rate": "0.0003", "min": "3", "fixed": "15"}})
    body = client.get("/api/accounts/a/fee-estimate", params={**Q, "ticker": "HKG:0700", "price": "300"}).json()
    assert body["total"] == "24.00"


def test_rate_that_would_be_rounded_is_refused_not_changed(client):
    response = client.put("/api/accounts/a/profile", json={
        "a_share": {"commission_rate": "0.00024999999999999999999999999999999", "waive_minimum": True}})
    assert response.status_code == 422 and "小数" in response.json()["detail"]
    assert client.get("/api/accounts/profiles").json()["items"] == []


def test_us_model_without_a_sell_side_gives_no_sell_estimate(client):
    assert client.put("/api/accounts/a/profile", json={"us": {"buy": {"fixed": "1"}}}).status_code == 422
    assert client.put("/api/accounts/a/profile", json={"us": {}}).status_code == 422
    saved = client.put("/api/accounts/a/profile", json={"us": {"buy": {"rate": "0", "min": "0", "fixed": "1"}}})
    assert saved.status_code == 200 and saved.json()["us"]["sell"] is None
    query = {**Q, "ticker": "NASDAQ:AAPL", "quantity": "10", "price": "200"}
    sell = client.get("/api/accounts/a/fee-estimate", params={**query, "side": "sell"}).json()
    assert sell["total"] is None and sell["reason"]
    assert client.get("/api/accounts/a/fee-estimate", params=query).json()["total"] == "1.00"


@pytest.mark.parametrize("change", [
    {"ticker": ""}, {"ticker": "600519"}, {"ticker": "XXX:1"}, {"side": "hold"}, {"side": ""},
    {"quantity": "0"}, {"quantity": "abc"}, {"quantity": "NaN"}, {"price": "-1"}, {"price": ""},
    {"date": "2026-02-30"}, {"date": "yesterday"},
])
def test_malformed_estimate_input_is_4xx(client, change):
    r = client.get("/api/accounts/a/fee-estimate", params={**Q, **change})
    assert r.status_code == 422 and r.json()["detail"]
    r = client.post("/api/accounts/a/fee-estimate", json={**Q, **change})
    assert r.status_code == 422


@pytest.mark.parametrize("change", [
    {"quantity": "1e30"}, {"quantity": "1e1000000"}, {"quantity": "1e-1000000"}, {"quantity": "1" * 100},
    {"quantity": "0.000000001"}, {"quantity": "1000000000001"},
    {"price": "1e30"}, {"price": "1e-1000000"}, {"price": "9" * 41}, {"price": "0.000000001"},
])
def test_numbers_out_of_bounds_are_a_short_422(client, change):
    for response in (client.get("/api/accounts/a/fee-estimate", params={**Q, **change}),
                     client.post("/api/accounts/a/fee-estimate", json={**Q, **change})):
        assert response.status_code == 422, response.text[:200]
        assert len(response.content) < 1000


def test_huge_json_numbers_are_a_422(client):
    for value in (10 ** 30, 1e300, 1e-300):
        response = client.post("/api/accounts/a/fee-estimate", json={**Q, "quantity": value})
        assert response.status_code == 422 and len(response.content) < 1000


def test_exponent_notation_is_echoed_as_a_plain_number(client):
    body = client.get("/api/accounts/a/fee-estimate", params={**Q, "quantity": "1E+2", "price": "1.68e3"}).json()
    assert body["quantity"] == "100" and body["price"] == "1680" and body["total"] == "43.68"


@pytest.mark.parametrize("rate", ["1e-1000000", "1e1000000", "0." + "0" * 100 + "1"])
def test_profile_numbers_out_of_bounds_are_a_short_422(client, rate):
    response = client.put("/api/accounts/a/profile", json={"a_share": {"commission_rate": rate}})
    assert response.status_code == 422 and len(response.content) < 1000
    assert client.get("/api/accounts/profiles").json()["items"] == []


def test_post_non_object_and_blank_account(client):
    assert client.post("/api/accounts/a/fee-estimate", json=[1]).status_code == 422
    assert client.get("/api/accounts/%20/fee-estimate", params=Q).status_code == 422


def test_missing_date_defaults_to_today(client):
    q = {k: v for k, v in Q.items() if k != "date"}
    assert client.get("/api/accounts/a/fee-estimate", params=q).json()["date"]


@pytest.mark.parametrize("value", [[], {}, True, False, 20261114, 2026.11, ["2026-11-14"], {"date": "2026-11-14"}])
def test_date_that_is_not_text_is_a_422(client, value):
    response = client.post("/api/accounts/a/fee-estimate", json={**Q, "date": value})
    assert response.status_code == 422 and "日期" in response.json()["detail"]


def test_absent_null_or_empty_date_means_today(client):
    from datetime import date
    today = date.today().isoformat()
    q = {k: v for k, v in Q.items() if k != "date"}
    for response in (client.post("/api/accounts/a/fee-estimate", json=q),
                     client.post("/api/accounts/a/fee-estimate", json={**q, "date": None}),
                     client.post("/api/accounts/a/fee-estimate", json={**q, "date": ""}),
                     client.get("/api/accounts/a/fee-estimate", params={**q, "date": ""})):
        assert response.status_code == 200 and response.json()["date"] == today


def test_reads_do_not_create_the_store(client, tmp_path):
    assert client.get("/api/accounts/profiles").status_code == 200
    assert client.get("/api/accounts/a/profile").json()["is_default"] is True
    assert client.get("/api/accounts/a/fee-estimate", params=Q).json()["total"] == "43.68"
    assert list(tmp_path.iterdir()) == []


def test_reads_of_a_store_that_needs_an_upgrade_are_503(client, monkeypatch):
    from core import account_profiles
    client.put("/api/accounts/a/profile", json={"broker": "one"})
    monkeypatch.setattr(account_profiles, "MIGRATIONS",
                        account_profiles.MIGRATIONS + [["ALTER TABLE account_profiles ADD COLUMN note TEXT"]])
    for response in (client.get("/api/accounts/profiles"), client.get("/api/accounts/a/profile"),
                     client.get("/api/accounts/a/fee-estimate", params=Q)):
        assert response.status_code == 503


def test_lock_contention_is_a_chinese_503(client, tmp_path, monkeypatch):
    import sqlite3
    from core import desk_store
    client.put("/api/accounts/a/profile", json={"broker": "one"})
    monkeypatch.setattr(desk_store, "BUSY_TIMEOUT_MS", 100)
    holder = sqlite3.connect(tmp_path / "desk.sqlite3", timeout=0, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        response = client.put("/api/accounts/a/profile", json={"broker": "two"})
        assert response.status_code == 503 and "稍后" in response.json()["detail"]
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert client.get("/api/accounts/a/profile").json()["broker"] == "one"


def test_demo_mode_middleware_blocks_put_and_post_but_not_get(tmp_path, monkeypatch):
    from backend.main import protect_demo_data
    monkeypatch.setenv("PORTFOLIO_DIR", str(tmp_path))
    monkeypatch.setenv("TRACKER_DEMO_MODE", "1")
    app = FastAPI()
    app.middleware("http")(protect_demo_data)
    app.include_router(api.router, prefix="/api/accounts")
    c = TestClient(app)
    assert c.put("/api/accounts/a/profile", json={}).status_code == 403
    assert c.post("/api/accounts/a/fee-estimate", json=Q).status_code == 403
    assert c.get("/api/accounts/a/fee-estimate", params=Q).status_code == 200
    assert c.get("/api/accounts/a/profile").status_code == 200
