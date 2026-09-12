"""Research correctness checks using deterministic, fictional OHLCV only."""
import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from backtesting.research import (
    ResearchConfig, build_features, build_labels, register_template,
    run_experiment, run_research, templates, _simulate,
)


def bars(n=500):
    index = pd.bdate_range("2020-01-01", periods=n, name="date")
    i = np.arange(n)
    changes = 0.0007 + 0.004 * np.sin(i / 8) + 0.003 * np.cos(i / 19)
    close = 100 * np.exp(np.cumsum(changes))
    open_ = close * (1 + 0.001 * np.sin(i))
    return pd.DataFrame({"open": open_, "high": np.maximum(close, open_) * 1.003,
                         "low": np.minimum(close, open_) * 0.997, "close": close,
                         "volume": 100000 + (i % 17) * 1000}, index=index)


def test_features_cannot_observe_future_values():
    original = bars()
    changed = original.copy()
    changed.loc[changed.index[301]:, ["open", "high", "low", "close"]] *= 1.7
    pd.testing.assert_frame_equal(build_features(original).iloc[:301], build_features(changed).iloc[:301])


def test_labels_use_exact_forward_window_and_have_maturity_date():
    df = bars()
    labels = build_labels(df, 5, "forward_return")
    assert labels.actual.iloc[100] == pytest.approx(df.close.iloc[105] / df.close.iloc[100] - 1)
    assert labels.label_end.iloc[100] == df.index[105]
    assert labels.actual.tail(5).isna().all()
    volatility = build_labels(df, 5, "forward_volatility")
    returns = np.log(df.close.iloc[101:106].to_numpy() / df.close.iloc[100:105].to_numpy())
    assert volatility.actual.iloc[100] == pytest.approx(np.sqrt(np.mean(returns ** 2) * 252))


def test_time_splits_remove_labels_crossing_boundaries():
    result = run_research(bars(), ResearchConfig(horizon=20))
    splits = result["splits"]
    assert splits["train"]["last_label_end"] < splits["validation"]["start"]
    assert splits["validation"]["last_label_end"] < splits["test"]["start"]
    assert splits["purged_samples"] == 40
    assert len(result["model"]["coefficients"]) == 5


def test_test_changes_do_not_change_fit_selection_or_validation():
    df = bars()
    baseline = run_research(df, ResearchConfig())
    changed = df.copy()
    changed.loc[df.index[400]:, ["open", "high", "low", "close"]] *= 1.8
    result = run_research(changed, ResearchConfig())
    assert result["model"] == baseline["model"]
    assert result["metrics"]["validation"] == baseline["metrics"]["validation"]
    assert result["metrics"]["test"] != baseline["metrics"]["test"]
    assert result["manifest"]["input_hash"] != baseline["manifest"]["input_hash"]


def test_split_dates_do_not_shift_when_lookback_changes():
    first = run_research(bars(), ResearchConfig(lookback=10))
    second = run_research(bars(), ResearchConfig(lookback=30))
    assert first["splits"]["test"]["start"] == second["splits"]["test"]["start"]
    assert first["splits"]["validation"]["start"] == second["splits"]["validation"]["start"]


def test_deterministic_results_and_json_artifacts():
    result = run_experiment({"EXAMPLE": bars()}, {"template_id": "momentum", "seed": 42})
    assert result == run_experiment({"EXAMPLE": bars()}, {"template_id": "momentum", "seed": 42})
    json.dumps(result, allow_nan=False)
    assert len(result["manifest"]["input_hash"]) == 64
    assert result["metrics"]["validation"]["mae"] >= 0
    assert result["results"]["EXAMPLE"]["dataset_snapshot"][0]["date"] == "2020-01-01"


@pytest.mark.parametrize("template", ["momentum", "mean_reversion", "volatility"])
def test_all_templates_train_models_and_return_facade(template):
    result = run_experiment({"EXAMPLE": bars()}, {"template_id": template, "lookback": 20})
    assert result["equity"] and result["lessons"]
    assert len(result["results"]["EXAMPLE"]["model"]["candidates"]) == 3
    assert result["results"]["EXAMPLE"]["model"]["type"] == "standardized_ridge"
    if template == "volatility":
        assert result["results"]["EXAMPLE"]["label_definition"]["target"] == "forward_volatility"


def test_root_parameter_aliases_are_applied_and_recorded():
    spec = {"template_id": "momentum", "horizon": 5, "lookback": 10, "train_ratio": 0.6,
            "validation_ratio": 0.2, "fee_bps": 10, "slippage_bps": 5,
            "seed": 42, "market": "us", "adjustment": "none"}
    result = run_experiment({"EXAMPLE": bars()}, spec)
    config = result["results"]["EXAMPLE"]["config"]
    assert config["lookback"] == 10 and config["transaction_cost_bps"] == 15
    assert config["adjustment"] == "" and config["train_fraction"] == 0.6
    assert result["manifest"]["requested_spec"] == spec


@pytest.mark.parametrize("spec", [
    {"horizonn": 5}, {"train_ratio": .6, "train_fraction": .7},
    {"fee_bps": 10, "transaction_cost_bps": 20}, {"horizon": True},
    {"lookback": 0}, {"seed": -1}, {"fee_bps": float("nan")}, {"adjustment": "bad"},
    {"source": "/private/file.csv"}, {"symbols": ["MISMATCH"]},
])
def test_invalid_or_ambiguous_specs_fail(spec):
    with pytest.raises(ValueError):
        run_experiment({"EXAMPLE": bars()}, spec)


def test_simulation_waits_for_following_open():
    df = bars(10)
    df.iloc[4, df.columns.get_loc("close")] *= 2
    predictions = pd.DataFrame({"prediction": [0.1, 0.1]}, index=df.index[4:6])
    rows, _ = _simulate(df, predictions, ResearchConfig(transaction_cost_bps=0))
    assert rows[0]["date"] == df.index[5].date().isoformat()
    assert rows[0]["strategy"] == pytest.approx(df.close.iloc[5] / df.open.iloc[5])


def test_simulation_deducts_fees_without_borrowing():
    df = bars(25)
    predictions = pd.DataFrame({"prediction": [.2] * 10}, index=df.index[5:15])
    cheap, _ = _simulate(df, predictions, ResearchConfig(transaction_cost_bps=0))
    costly, stats = _simulate(df, predictions, ResearchConfig(transaction_cost_bps=100))
    assert costly[-1]["strategy"] < cheap[-1]["strategy"]
    assert stats["fees_fraction_initial_capital"] > 0
    assert all(0 <= row["exposure"] <= 1.0000001 for row in costly)


def test_multi_symbol_summary_uses_common_dates():
    result = run_experiment({"AAA": bars(), "BBB": bars().iloc[5:]}, {"template": "volatility"})
    dates = [set(row["date"] for row in item["equity"]) for item in result["results"].values()]
    assert set(row["date"] for row in result["equity"]) == set.intersection(*dates)
    assert result["manifest"]["symbols"] == ["AAA", "BBB"]


def test_metadata_extension_runs_through_real_pipeline():
    import backtesting.research as module
    try:
        register_template("test_extension", {"name": "Example", "target": "forward_return",
                                            "features": ["return_1", "return_medium"]})
        result = run_experiment({"EXAMPLE": bars()}, {"template_id": "test_extension"})
        assert len(result["results"]["EXAMPLE"]["model"]["coefficients"]) == 2
        with pytest.raises(ValueError, match="already registered"):
            register_template("test_extension", {})
    finally:
        module._TEMPLATES.pop("test_extension", None)


def test_template_schema_exposes_parameter_defaults():
    for template in templates():
        assert template["defaults"]["lookback"] == 20
        assert template["parameters"]["horizon"]["minimum"] == 1
        assert template["parameters"]["market"]["enum"] == ["a_share", "hk", "us"]


def test_custom_runner_registration_preserves_inputs_and_validates_output():
    import backtesting.research as module
    data = bars()
    def runner(frames, spec):
        frames["EXAMPLE"].iloc[0, 0] = -999
        return {"metrics": {}, "equity": [], "manifest": {"custom": True}, "warnings": [], "lessons": [], "results": {}}
    try:
        register_template("custom_runner_test", {"name": "Custom", "target": "custom_target"}, runner)
        result = run_experiment({"EXAMPLE": data}, {"template_id": "custom_runner_test"})
        assert result["manifest"]["custom"] is True
        assert data.iloc[0, 0] > 0
    finally:
        module._TEMPLATES.pop("custom_runner_test", None)
        module._RUNNERS.pop("custom_runner_test", None)


def test_flat_prices_produce_finite_results_and_no_fake_superiority():
    df = bars()
    df[["open", "high", "low", "close"]] = 100.0
    result = run_experiment({"EXAMPLE": df}, {"template": "momentum"})
    json.dumps(result, allow_nan=False)
    assert result["metrics"]["test"]["improvement_pct"] is None
    assert result["metrics"]["test"]["mae"] == 0
