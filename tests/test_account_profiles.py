from decimal import localcontext

import pytest

from core import account_profiles as ap
from core import desk_store


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DIR", str(tmp_path))
    return tmp_path


def test_missing_profile_is_the_default_preset():
    p = ap.get("华泰 主账户")
    assert p["is_default"] is True and p["account"] == "华泰 主账户"
    assert p["a_share"] == {"commission_rate": "0.00025", "commission_min": "5", "waive_minimum": False}
    assert p["hk"] is None and p["us"] is None
    assert ap.list() == []


def test_save_and_get_roundtrip_with_chinese_name():
    saved = ap.save(" 华泰 主账户 ", {"broker": "huatai", "a_share": {"commission_rate": "0.0001", "commission_min": "0",
                                                                    "waive_minimum": True}})
    assert saved["is_default"] is False and saved["account"] == "华泰 主账户"
    assert saved["a_share"]["commission_rate"] == "0.0001" and saved["a_share"]["waive_minimum"] is True
    assert ap.get("华泰 主账户") == saved
    assert [x["account"] for x in ap.list()] == ["华泰 主账户"]
    assert ap.get("other")["is_default"] is True


def test_save_overwrites_and_omitted_a_share_uses_preset():
    ap.save("a", {"broker": "x"})
    assert ap.get("a")["a_share"]["commission_min"] == "5"
    ap.save("a", {"a_share": {"commission_min": "3"}})
    assert ap.get("a")["a_share"]["commission_min"] == "3" and ap.get("a")["broker"] == ""


def test_hk_us_models_normalised_per_side():
    # Every side states rate, min and fixed: a missing value is never read as zero.
    p = ap.save("a", {"hk": {"rate": "0.0003", "min": "3", "fixed": "15"},
                      "us": {"buy": {"rate": "0", "min": "0", "fixed": "0"},
                             "sell": {"rate": "0.001", "min": "1", "fixed": "0"}}})
    assert p["hk"]["buy"] == p["hk"]["sell"] == {"rate": "0.0003", "min": "3", "fixed": "15"}
    assert p["us"]["buy"] == {"rate": "0", "min": "0", "fixed": "0"}
    assert p["us"]["sell"]["min"] == "1"


def test_a_market_model_may_cover_one_side_only():
    p = ap.save("a", {"us": {"buy": {"rate": "0", "min": "0", "fixed": "1"}}})
    assert p["us"] == {"buy": {"rate": "0", "min": "0", "fixed": "1"}, "sell": None}
    assert ap.save("a", {"us": {"buy": None, "sell": {"rate": "0.001", "min": "1", "fixed": "0"}}})["us"]["buy"] is None


@pytest.mark.parametrize("model", [
    {}, {"buy": {}}, {"buy": {"fixed": "1"}}, {"sell": {"rate": "0.001", "min": "1"}},
    {"buy": None}, {"buy": None, "sell": None}, {"rate": "0.0003"}, {"rate": "0.0003", "min": "3"},
    {"buy": {"rate": "0", "min": "0", "fixed": None}},
])
@pytest.mark.parametrize("market", ["hk", "us"])
def test_incomplete_market_models_are_rejected(market, model):
    with pytest.raises(ap.ProfileError):
        ap.save("a", {market: model})
    assert ap.list() == []


@pytest.mark.parametrize("bad", [
    {"a_share": {"commission_rate": "-0.1"}}, {"a_share": {"commission_rate": "0.5"}},
    {"a_share": {"commission_rate": "abc"}}, {"a_share": {"commission_rate": "NaN"}},
    {"a_share": {"commission_rate": 0.1}}, {"a_share": {"commission_min": "-1"}},
    {"a_share": {"commission_min": "1000000"}}, {"a_share": {"waive_minimum": "yes"}},
    {"a_share": {"surprise": 1}}, {"a_share": "x"}, {"broker": "x" * 41}, {"broker": 5},
    {"hk": {"rate": "2"}}, {"hk": {"rate": "0.1", "fixed": "-1"}}, {"hk": {"bogus": 1}}, {"hk": "x"},
    {"us": {"buy": {}, "extra": {}}}, {"nope": 1},
])
def test_validation_rejects(bad):
    with pytest.raises(ap.ProfileError):
        ap.save("a", bad)
    assert ap.list() == []


def test_values_are_stored_exactly_whatever_the_ambient_context():
    with localcontext() as context:
        context.prec = 3
        p = ap.save("a", {"a_share": {"commission_rate": "0.000254321", "commission_min": "5.125"},
                          "hk": {"rate": "0.00030000", "min": "3.50", "fixed": "15.0625"}})
    assert p["a_share"]["commission_rate"] == "0.000254321" and p["a_share"]["commission_min"] == "5.125"
    assert p["hk"]["buy"] == {"rate": "0.0003", "min": "3.5", "fixed": "15.0625"}


@pytest.mark.parametrize("field, value", [
    ("commission_rate", "0.00024999999999999999999999999999999"),  # would be stored as 0.00025
    ("commission_rate", "0.00000000001"),                          # 11 decimal places
    ("commission_min", "5.000001"),                                # 6 decimal places for an amount
])
def test_values_with_more_decimal_places_than_kept_are_rejected(field, value):
    with pytest.raises(ap.ProfileError) as error:
        ap.save("a", {"a_share": {field: value}})
    assert "小数" in str(error.value)
    assert ap.list() == []


def test_longest_accepted_scales_are_kept():
    p = ap.save("a", {"a_share": {"commission_rate": "0.0000000001", "commission_min": "4.9999"}})
    assert p["a_share"]["commission_rate"] == "0.0000000001" and p["a_share"]["commission_min"] == "4.9999"


@pytest.mark.parametrize("name", ["", "   ", "x" * 241, "a\nb", None, 5])
def test_bad_account_names(name):
    with pytest.raises(ap.ProfileError):
        ap.get(name)


def test_reading_a_missing_store_creates_nothing(home):
    assert ap.get("a")["is_default"] is True and ap.list() == []
    assert list(home.iterdir()) == []


def test_reading_never_migrates_but_a_write_does(home, monkeypatch):
    ap.save("a", {"broker": "one"})
    monkeypatch.setattr(ap, "MIGRATIONS", ap.MIGRATIONS + [["ALTER TABLE account_profiles ADD COLUMN note TEXT"]])
    with pytest.raises(desk_store.DeskStoreError):
        ap.get("a")
    with pytest.raises(desk_store.DeskStoreError):
        ap.list()
    assert desk_store.schema_version(ap.SCHEMA) == 1
    ap.save("a", {"broker": "two"})
    assert desk_store.schema_version(ap.SCHEMA) == 2 and ap.get("a")["broker"] == "two"


def test_isolated_by_portfolio_dir(tmp_path, monkeypatch):
    ap.save("a", {"broker": "one"})
    other = tmp_path / "other"
    monkeypatch.setenv("PORTFOLIO_DIR", str(other))
    assert ap.get("a")["is_default"] is True and ap.list() == []
    ap.save("a", {"broker": "two"})
    monkeypatch.setenv("PORTFOLIO_DIR", str(tmp_path))
    assert ap.get("a")["broker"] == "one"
