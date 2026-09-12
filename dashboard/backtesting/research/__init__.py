"""Deterministic, offline stock prediction experiments.

This module performs no I/O. Callers own data acquisition, persistence and access
control. Research allocation curves are fractional, long-only simulations, not
broker executions. Models and transforms are fitted on training observations;
only validation observations select regularization.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import platform
import re
from typing import Any

import numpy as np
import pandas as pd

from backtesting.data.quality import validate_daily_bars

ALGORITHM_VERSION = "stock-lab-1"
_TEMPLATES = {
    "momentum": {"id": "momentum", "name": "动量延续", "target": "forward_return",
                 "description": "用过去的趋势、波动和成交量预测未来区间收益，检验趋势是否延续。",
                 "features": ["return_short", "return_medium", "return_long", "volatility_medium", "volume_ratio"]},
    "mean_reversion": {"id": "mean_reversion", "name": "短期反转", "target": "forward_return",
                       "description": "用短期涨跌和相对均线的偏离预测未来收益，检验是否存在反转。",
                       "features": ["return_1", "return_short", "price_zscore", "range_mean", "volume_ratio"]},
    "volatility": {"id": "volatility", "name": "波动预测", "target": "forward_volatility",
                   "description": "预测未来区间的年化实现波动，与近期波动延续基线比较。",
                   "features": ["volatility_short", "volatility_medium", "range_mean", "return_short", "volume_ratio"]},
}


_RUNNERS = {}
_PARAMETERS = {
    "horizon": {"type": "integer", "minimum": 1, "maximum": 30, "default": 5},
    "lookback": {"type": "integer", "minimum": 5, "maximum": 120, "default": 20},
    "train_ratio": {"type": "number", "minimum": 0.4, "maximum": 0.8, "default": 0.6},
    "validation_ratio": {"type": "number", "minimum": 0.1, "maximum": 0.3, "default": 0.2},
    "fee_bps": {"type": "number", "minimum": 0, "maximum": 200, "default": 10},
    "slippage_bps": {"type": "number", "minimum": 0, "maximum": 200, "default": 5},
    "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647, "default": 42},
    "market": {"type": "string", "enum": ["a_share", "hk", "us"], "default": "us"},
    "adjustment": {"type": "string", "enum": ["qfq", "hfq", "none"], "default": "qfq"},
}


def templates() -> list[dict]:
    result = []
    for template in _TEMPLATES.values():
        parameters = template.get("parameters", _PARAMETERS)
        result.append({**template, "parameters": parameters,
                       "defaults": {**{name: definition["default"] for name, definition in parameters.items() if "default" in definition},
                                    **template.get("defaults", {})}})
    return json.loads(json.dumps(result, ensure_ascii=False))


def register_template(template_id: str, metadata: dict, runner=None):
    """Register an explicitly trusted, installed plugin at application startup.

    A custom runner receives copied frames and a JSON specification and returns
    the same JSON facade schema. A metadata-only extension may select any of
    the built-in causal features and either built-in target. Existing template
    identifiers cannot be replaced. This function does not import user code.
    """
    if not isinstance(template_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", template_id):
        raise ValueError("Invalid template identifier")
    if template_id in _TEMPLATES:
        raise ValueError("Template identifier is already registered")
    clean = json.loads(json.dumps(metadata, allow_nan=False))
    if not isinstance(clean, dict) or not clean.get("name") or not clean.get("target"):
        raise ValueError("Template metadata needs a name and prediction target")
    allowed_features = {"return_1", "return_short", "return_medium", "return_long", "volatility_short",
                        "volatility_medium", "price_zscore", "range_mean", "volume_ratio"}
    if runner is None and (clean["target"] not in {"forward_return", "forward_volatility"}
                          or not clean.get("features") or not set(clean["features"]) <= allowed_features):
        raise ValueError("A built-in template extension needs a supported target and causal feature names")
    if runner is not None and not callable(runner):
        raise ValueError("Custom experiment runner must be callable")
    _TEMPLATES[template_id] = {**clean, "id": template_id}
    if runner is not None:
        _RUNNERS[template_id] = runner


@dataclass(frozen=True)
class ResearchConfig:
    symbol: str = "EXAMPLE"
    template: str = "momentum"
    target: str = "forward_return"
    horizon: int = 5
    lookback: int = 20
    seed: int = 42
    train_fraction: float = 0.6
    validation_fraction: float = 0.2
    ridge_alphas: tuple[float, ...] = (0.1, 1.0, 10.0)
    transaction_cost_bps: float = 10.0
    target_volatility: float = 0.15
    market: str = "a_share"
    adjustment: str = "qfq"
    source: str = "provided"

    def validate(self):
        if self.template not in _TEMPLATES:
            raise ValueError("Unknown research template")
        if self.target not in {"forward_return", "forward_volatility"}:
            raise ValueError("Unsupported prediction target")
        if not isinstance(self.horizon, int) or isinstance(self.horizon, bool) or not 1 <= self.horizon <= 60:
            raise ValueError("horizon must be an integer between 1 and 60")
        if not isinstance(self.lookback, int) or isinstance(self.lookback, bool) or not 5 <= self.lookback <= 120:
            raise ValueError("lookback must be an integer between 5 and 120")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool) or not 0 <= self.seed <= 2147483647:
            raise ValueError("seed must be an integer between 0 and 2147483647")
        numbers = [self.train_fraction, self.validation_fraction, self.transaction_cost_bps, self.target_volatility]
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not np.isfinite(x) for x in numbers):
            raise ValueError("Experiment parameters must be finite numbers")
        if not (0.4 <= self.train_fraction <= 0.8 and 0.1 <= self.validation_fraction <= 0.3
                and self.train_fraction + self.validation_fraction <= 0.9):
            raise ValueError("Use chronological train/validation fractions with at least 10% reserved for test")
        if not 0 <= self.transaction_cost_bps <= 400 or not 0.01 <= self.target_volatility <= 1:
            raise ValueError("Costs must be 0–400 bps and target volatility 0.01–1")
        if not self.ridge_alphas or len(self.ridge_alphas) > 20 or any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not np.isfinite(x) or not 0.000001 <= x <= 1000000
            for x in self.ridge_alphas
        ):
            raise ValueError("Provide 1–20 positive finite ridge penalties")
        if self.adjustment not in {"", "qfq", "hfq"}:
            raise ValueError("Unknown adjustment convention")
        if self.market not in {"a_share", "hk", "us", "hk_stock", "us_stock"}:
            raise ValueError("Unknown market")
        for field in (self.symbol, self.source):
            if not isinstance(field, str) or not field or len(field) > 100 or any(x in field for x in ("/", "\\", "\n", "\r")):
                raise ValueError("Symbol and source must be short labels, not filesystem paths")


def _json_safe(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(x) for x in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).date().isoformat()
    return value


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def build_features(df: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    """All columns depend only on the current close and past observations."""
    close = df["close"].astype(float)
    log_returns = np.log(close / close.shift(1))
    out = pd.DataFrame(index=df.index)
    windows = {"1": 1, "short": max(2, lookback // 4), "medium": lookback, "long": lookback * 3}
    for name, window in windows.items():
        out[f"return_{name}"] = close.pct_change(window, fill_method=None)
    for name in ("short", "medium"):
        out[f"volatility_{name}"] = np.sqrt(log_returns.pow(2).rolling(windows[name]).mean() * 252)
    std = close.rolling(lookback).std(ddof=0)
    out["price_zscore"] = ((close - close.rolling(lookback).mean()) / std.replace(0, np.nan)).fillna(0)
    out["range_mean"] = ((df["high"] - df["low"]) / close).rolling(lookback).mean()
    volume_mean = df["volume"].rolling(lookback).mean()
    out["volume_ratio"] = (df["volume"] / volume_mean.replace(0, np.nan)).fillna(0)
    return out


def build_labels(df: pd.DataFrame, horizon: int, target: str) -> pd.DataFrame:
    close = df["close"].astype(float)
    if target == "forward_return":
        values = close.shift(-horizon) / close - 1
    elif target == "forward_volatility":
        log_returns = np.log(close / close.shift(1))
        values = np.sqrt(log_returns.pow(2).rolling(horizon).mean().shift(-horizon) * 252)
    else:
        raise ValueError("Unsupported prediction target")
    return pd.DataFrame({"actual": values, "label_end": pd.Series(df.index, index=df.index).shift(-horizon)})


def _fit_ridge(x, y, alpha):
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-12] = 1.0
    standardized = (x - mean) / scale
    intercept = float(y.mean())
    coefficients = np.linalg.solve(standardized.T @ standardized + alpha * np.eye(x.shape[1]),
                                   standardized.T @ (y - intercept))
    if not (np.isfinite(coefficients).all() and np.isfinite(mean).all() and np.isfinite(scale).all() and np.isfinite(intercept)):
        raise ValueError("Model fit is numerically unstable; inspect input magnitudes")
    return {"mean": mean, "scale": scale, "coefficients": coefficients, "intercept": intercept, "alpha": alpha}


def _predict(model, x, target):
    predictions = ((x - model["mean"]) / model["scale"]) @ model["coefficients"] + model["intercept"]
    if not np.isfinite(predictions).all():
        raise ValueError("Predictions are not finite; inspect input magnitudes")
    return np.maximum(predictions, 0) if target == "forward_volatility" else predictions


def _prediction_metrics(actual, prediction, baseline):
    model_mae = float(np.mean(np.abs(actual - prediction)))
    baseline_mae = float(np.mean(np.abs(actual - baseline)))
    correlation = float(np.corrcoef(actual, prediction)[0, 1]) if np.std(actual) > 1e-12 and np.std(prediction) > 1e-12 else None
    return {"samples": len(actual), "mae": model_mae, "rmse": float(np.sqrt(np.mean((actual - prediction) ** 2))), "model_mae": model_mae, "baseline_mae": baseline_mae,
            "model_rmse": float(np.sqrt(np.mean((actual - prediction) ** 2))),
            "baseline_rmse": float(np.sqrt(np.mean((actual - baseline) ** 2))),
            "improvement_pct": 100 * (1 - model_mae / baseline_mae) if baseline_mae > 1e-12 else None,
            "correlation": correlation}


def _simulate(df, predictions: pd.DataFrame, config: ResearchConfig):
    """Self-financing fractional exposure, rebalance at the next open.

    Overnight P&L belongs to the previous holding. Costs are deducted before
    target weights are allocated; no borrowing or same-close execution.
    """
    fee_rate = config.transaction_cost_bps / 10000
    first_signal = df.index.get_loc(predictions.index[0])
    last_signal = df.index.get_loc(predictions.index[-1])
    cash, shares, benchmark_shares, turnover, total_fees = 1.0, 0.0, None, 0.0, 0.0
    rows = []
    for cursor in range(first_signal + 1, min(last_signal + 2, len(df))):
        bar = df.iloc[cursor]
        signal_date = df.index[cursor - 1]
        nav_open = cash + shares * float(bar.open)
        if signal_date in predictions.index and bar.volume > 0:
            prediction = float(predictions.loc[signal_date, "prediction"])
            weight = (1.0 if prediction > 2 * fee_rate else 0.0) if config.target == "forward_return" else float(
                np.clip(config.target_volatility / max(prediction, 1e-8), 0.1, 1.0))
            previous_value = shares * float(bar.open)
            delta = weight * nav_open - previous_value
            fee = fee_rate * abs(delta) / (1 + fee_rate * weight if delta >= 0 else 1 - fee_rate * weight)
            target_value = weight * (nav_open - fee)
            turnover += abs(target_value - previous_value) / nav_open if nav_open else 0.0
            total_fees += fee
            shares = target_value / float(bar.open)
            cash = nav_open - fee - target_value
        if benchmark_shares is None:
            benchmark_shares = 1 / float(bar.open)
        strategy_value = cash + shares * float(bar.close)
        rows.append({"date": df.index[cursor].date().isoformat(), "strategy": strategy_value,
                     "benchmark": benchmark_shares * float(bar.close),
                     "exposure": shares * float(bar.close) / strategy_value if strategy_value else 0.0})
    values = np.array([1.0] + [r["strategy"] for r in rows])
    returns = values[1:] / values[:-1] - 1
    running_peak = np.maximum.accumulate(values)
    return rows, {"total_return": float(values[-1] - 1), "benchmark_return": rows[-1]["benchmark"] - 1,
                  "max_drawdown": float(np.min(values / running_peak - 1)),
                  "annualized_volatility": float(np.std(returns, ddof=0) * np.sqrt(252)),
                  "turnover": turnover, "fees_fraction_initial_capital": total_fees,
                  "method": "next_open_fractional_long_only", "transaction_cost_bps": config.transaction_cost_bps}


def run_research(df: pd.DataFrame, config: ResearchConfig) -> dict[str, Any]:
    config.validate()
    if not isinstance(df, pd.DataFrame) or not 120 <= len(df) <= 20000:
        raise ValueError("Provide 120–20,000 daily observations; longer horizons may require more rows")
    quality = validate_daily_bars(df, symbol=config.symbol, adjust=config.adjustment)
    quality.raise_if_failed()
    frame = df.loc[:, ["open", "high", "low", "close", "volume"]].astype(float).copy()
    features = build_features(frame, config.lookback)
    labels = build_labels(frame, config.horizon, config.target)
    feature_names = _TEMPLATES[config.template]["features"]
    dataset = features.join(labels).iloc[config.lookback * 3:-config.horizon].copy()
    if not np.isfinite(dataset[feature_names + ["actual"]].to_numpy()).all():
        raise ValueError("Derived features or labels contain non-finite values")
    n = len(dataset)
    validation_start_idx = int(len(frame) * config.train_fraction)
    test_start_idx = int(len(frame) * (config.train_fraction + config.validation_fraction))
    if not 0 < validation_start_idx < test_start_idx < len(frame):
        raise ValueError("Insufficient observations for chronological splits")
    validation_start, test_start = frame.index[validation_start_idx], frame.index[test_start_idx]
    train = dataset[(dataset.index < validation_start) & (dataset.label_end < validation_start)]
    validation = dataset[(dataset.index >= validation_start) & (dataset.index < test_start) & (dataset.label_end < test_start)]
    test = dataset[dataset.index >= test_start]
    if len(train) < 40 or len(validation) < 15 or len(test) < 15:
        raise ValueError("Insufficient matured samples after purging; need 40 training, 15 validation and 15 test observations")
    x_train = train[feature_names].to_numpy()
    y_train = train.actual.to_numpy()
    candidates = []
    for alpha in sorted(set(config.ridge_alphas)):
        model = _fit_ridge(x_train, y_train, alpha)
        prediction = _predict(model, validation[feature_names].to_numpy(), config.target)
        candidates.append((float(np.mean(np.abs(validation.actual.to_numpy() - prediction))), alpha, model))
    _, _, model = min(candidates, key=lambda x: (x[0], x[1]))
    baseline_name = f"trailing_{config.lookback}_day_volatility" if config.target == "forward_volatility" else "training_mean_return"
    metrics, prediction_rows, split_info = {}, [], {}
    simulation_rows = []
    simulation_summary = {}
    for split, part in (("train", train), ("validation", validation), ("test", test)):
        prediction = _predict(model, part[feature_names].to_numpy(), config.target)
        baseline = part.volatility_medium.to_numpy() if config.target == "forward_volatility" else np.full(len(part), y_train.mean())
        metrics[split] = _prediction_metrics(part.actual.to_numpy(), prediction, baseline)
        split_info[split] = {"samples": len(part), "start": part.index[0].date().isoformat(),
                             "end": part.index[-1].date().isoformat(), "last_label_end": part.label_end.max().date().isoformat()}
        predicted = pd.DataFrame({"prediction": prediction}, index=part.index)
        for i, (timestamp, row) in enumerate(part.iterrows()):
            prediction_rows.append({"date": timestamp.date().isoformat(), "split": split,
                                    "actual": float(row.actual), "prediction": float(prediction[i]),
                                    "baseline": float(baseline[i]), "label_end": row.label_end.date().isoformat()})
        if split == "test":
            simulation_rows, simulation_summary = _simulate(frame, predicted, config)
    snapshot = [{"date": timestamp.date().isoformat(), **{col: float(row[col]) for col in frame.columns}}
                for timestamp, row in frame.iterrows()]
    config_dict = asdict(config)
    input_hash = _digest(snapshot)
    experiment_id = _digest({"data": input_hash, "config": config_dict, "algorithm": ALGORITHM_VERSION})
    warnings = list(quality.warnings) + [
        "Research only: allocation curves use fractional long-only positions; they do not model lots, price-limit queues, financing or broker execution.",
        "Close-based labels describe prediction targets, not realized execution returns; allocations change at the following open.",
        "Overlapping forward labels are dependent observations; reported errors are descriptive, not statistical significance tests.",
        "The final test interval is a holdout only while its results are not used to choose another experiment.",
        "Final positions are marked to the last evaluation close, without a forced liquidation or exit fee.",
        "Historical adjustments and a present-day stock selection can introduce revisions or survivorship bias; freeze the supplied dataset.",
    ]
    improvement = metrics["validation"]["improvement_pct"]
    lessons = ["验证集误差优于基线；仍需检查不同时间窗口。" if improvement is not None and improvement > 0
               else "验证集未优于简单基线；保留这一失败结果，先检查标签、特征和样本覆盖。",
               "预测误差与策略表现分别评价；预测更准不保证扣费后的策略更好。"]
    result = {"schema_version": 1, "experiment_id": experiment_id, "symbol": config.symbol,
              "config": config_dict, "dataset": {"rows": len(frame), "start": snapshot[0]["date"], "end": snapshot[-1]["date"]},
              "quality": {"errors": quality.errors, "warnings": quality.warnings, "metrics": quality.metrics},
              "feature_definitions": {name: {"available_at": "signal close", "lookback": config.lookback,
                  "short_window": max(2, config.lookback // 4), "medium_window": config.lookback,
                  "long_window": config.lookback * 3} for name in feature_names},
              "label_definition": {"target": config.target, "horizon": config.horizon,
                                   "formula": "close[t+h]/close[t]-1" if config.target == "forward_return" else "sqrt(252 * mean(log(close[t+i]/close[t+i-1])**2, i=1..h))",
                                   "available_at": "label_end close"},
              "splits": {**split_info, "purged_samples": n - len(train) - len(validation) - len(test),
                         "rule": "Training/validation label_end must precede the next split start"},
              "model": {"type": "standardized_ridge", "features": feature_names, "alpha": model["alpha"],
                        "intercept": model["intercept"], "coefficients": model["coefficients"].tolist(),
                        "mean": model["mean"].tolist(), "scale": model["scale"].tolist(),
                        "candidates": [{"alpha": alpha, "validation_mae": loss} for loss, alpha, _ in candidates],
                        "seed": config.seed, "deterministic": True, "fit_scope": "train only; validation selects alpha; test never fits or selects"},
              "metrics": {**metrics, "baseline": {"name": baseline_name}}, "predictions": prediction_rows,
              "equity": simulation_rows, "simulation": simulation_summary,
              "manifest": {"input_hash": input_hash, "algorithm_version": ALGORITHM_VERSION, "config": config_dict,
                           "data_source": config.source, "symbols": [config.symbol],
                           "environment": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__}},
              "warnings": warnings, "lessons": lessons, "dataset_snapshot": snapshot}
    return _json_safe(result)


def run_experiment(frames: dict[str, pd.DataFrame], spec: dict) -> dict:
    """JSON facade for API/agent callers. No arbitrary code or filesystem paths."""
    if not isinstance(frames, dict) or not 1 <= len(frames) <= 20:
        raise ValueError("Provide 1–20 stock datasets")
    if not isinstance(spec, dict):
        raise ValueError("Experiment specification must be an object")
    template = spec.get("template", spec.get("template_id", "momentum"))
    if template not in _TEMPLATES:
        raise ValueError("Unknown research template")
    if template in _RUNNERS:
        result = _RUNNERS[template]({symbol: frame.copy(deep=True) for symbol, frame in frames.items()},
                                     json.loads(json.dumps(spec, allow_nan=False)))
        required = {"metrics", "equity", "manifest", "warnings", "lessons", "results"}
        if not isinstance(result, dict) or not required <= set(result):
            raise ValueError("Plugin result does not satisfy the research result contract")
        return json.loads(json.dumps(result, allow_nan=False))
    aliases = {"train_ratio": "train_fraction", "validation_ratio": "validation_fraction"}
    normalized = dict(spec)
    for source, target in aliases.items():
        if source in normalized:
            if target in normalized and normalized[source] != normalized[target]:
                raise ValueError("Conflicting experiment parameter aliases")
            normalized[target] = normalized.pop(source)
    if "fee_bps" in normalized or "slippage_bps" in normalized:
        if "transaction_cost_bps" in normalized or "cost_bps" in normalized:
            raise ValueError("Provide combined transaction cost or separate fee and slippage, not both")
        for name in ("fee_bps", "slippage_bps"):
            value = normalized.get(name, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or not 0 <= value <= 200:
                raise ValueError("Fee and slippage must each be finite and between 0–200 bps")
        normalized["transaction_cost_bps"] = normalized.pop("fee_bps", 0) + normalized.pop("slippage_bps", 0)
    if normalized.get("adjustment") == "none":
        normalized["adjustment"] = ""
    fields = set(ResearchConfig.__dataclass_fields__) - {"symbol", "template", "target"}
    unknown = set(normalized) - fields - {"template", "template_id", "cost_bps", "symbols"}
    if unknown:
        raise ValueError("Unknown experiment parameters: " + ", ".join(sorted(unknown)))
    if "template" in normalized and "template_id" in normalized and normalized["template"] != normalized["template_id"]:
        raise ValueError("Conflicting template identifiers")
    if "symbols" in normalized and sorted(normalized["symbols"]) != sorted(frames):
        raise ValueError("Configured symbols do not match supplied datasets")
    options = {key: normalized[key] for key in fields if key in normalized}
    if "cost_bps" in normalized:
        if "transaction_cost_bps" in options and normalized["cost_bps"] != options["transaction_cost_bps"]:
            raise ValueError("Conflicting cost parameter aliases")
        options["transaction_cost_bps"] = normalized["cost_bps"]
    if "ridge_alphas" in options:
        options["ridge_alphas"] = tuple(options["ridge_alphas"])
    results = {symbol: run_research(frame, ResearchConfig(symbol=symbol, template=template,
                target=_TEMPLATES[template]["target"], **options)) for symbol, frame in sorted(frames.items())}
    aggregates = {}
    for split in ("validation", "test"):
        total = sum(result["metrics"][split]["samples"] for result in results.values())
        values = {name: sum(result["metrics"][split][name] * result["metrics"][split]["samples"] for result in results.values()) / total
                  for name in ("model_mae", "baseline_mae")}
        aggregates[split] = {"samples": total, **values, "mae": values["model_mae"],
                             "improvement_pct": 100 * (1 - values["model_mae"] / values["baseline_mae"]) if values["baseline_mae"] > 1e-12 else None}
    aggregates["baseline"] = next(iter(results.values()))["metrics"]["baseline"]
    curves = []
    for symbol, result in results.items():
        curve = pd.DataFrame(result["equity"]).set_index("date")[["strategy", "benchmark"]]
        daily = curve.pct_change(fill_method=None)
        daily.iloc[0] = curve.iloc[0] - 1
        curves.append(daily.add_suffix("_" + symbol))
    joint = pd.concat(curves, axis=1, join="inner")
    if joint.empty:
        raise ValueError("Stock test calendars have no common evaluation dates")
    strategy_columns = [column for column in joint if column.startswith("strategy_")]
    benchmark_columns = [column for column in joint if column.startswith("benchmark_")]
    strategy = (1 + joint[strategy_columns].mean(axis=1)).cumprod()
    benchmark = (1 + joint[benchmark_columns].mean(axis=1)).cumprod()
    equity = [{"date": date, "strategy": float(strategy.loc[date]), "benchmark": float(benchmark.loc[date])} for date in joint.index]
    manifest = {"algorithm_version": ALGORITHM_VERSION, "symbols": sorted(results), "template": template,
                "input_hash": _digest({symbol: result["manifest"]["input_hash"] for symbol, result in results.items()}),
                "runs": {symbol: result["experiment_id"] for symbol, result in results.items()},
                "config": next(iter(results.values()))["config"], "data_source": options.get("source", "provided"),
                "requested_spec": spec,
                "aggregation": "Equal-weight daily returns on common evaluation dates; cross-stock rebalancing cost is not modelled"}
    warnings = list(dict.fromkeys(warning for result in results.values() for warning in result["warnings"]))
    if len(results) > 1:
        warnings.append("Aggregate is an equal-weight research comparison on common test dates, not a fully costed multi-asset portfolio backtest.")
    return _json_safe({"schema_version": 1, "experiment_id": _digest(manifest), "template": template,
                       "metrics": aggregates, "equity": equity, "manifest": manifest,
                       "warnings": warnings, "lessons": list(dict.fromkeys(lesson for result in results.values() for lesson in result["lessons"])),
                       "results": results})
