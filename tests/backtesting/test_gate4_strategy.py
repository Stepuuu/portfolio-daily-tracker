"""Release gate 4 (cr_g4d PART 1), judged item service.py:1738: a custom strategy that
imports a module of its own (outside backtesting/factors and backtesting/strategies,
here a file on the path) - a constant changed there left same_strategy true.

The modules outside the standard library and the installed packages that a custom
strategy file reaches are fingerprinted too (strategy_definition local_modules); a
rerun compares them, and a record saved without them cannot claim the same strategy.
"""
import sys
import types

import pytest

from backtesting import service as service_module
from backtesting.service import BacktestService, TaskRegistry, parse_spec
from tests.backtesting.test_service import TODAY, FakeLoader, codes_of, fake_presets, wave

SOURCE = """
import gate4_helpers
from backtesting.strategies.base import Strategy


class Custom(Strategy):
    name = "Custom"

    def on_bar(self):
        if gate4_helpers.THRESHOLD and not self.get_position():
            self.buy(pct_cash=0.5)
"""


@pytest.fixture
def custom(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "gate4_helpers.py").write_text("THRESHOLD = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(project))
    monkeypatch.delitem(sys.modules, "gate4_helpers", raising=False)
    module = types.ModuleType("gate4_custom")
    exec(compile(SOURCE, "gate4_custom.py", "exec"), module.__dict__)
    cls = module.Custom
    setattr(cls, service_module.STRATEGY_SOURCE_ATTR, SOURCE)
    yield cls, project / "gate4_helpers.py"
    sys.modules.pop("gate4_helpers", None)


@pytest.fixture
def service(tmp_path, monkeypatch, custom):
    monkeypatch.setenv("TRACKER_BACKTEST_DIR", str(tmp_path / "bt"))
    monkeypatch.delenv("TRACKER_DEMO_MODE", raising=False)
    monkeypatch.setitem(sys.modules, "backtesting.presets", fake_presets())
    fake = FakeLoader({"SHA:600519": wave(), "IDX:000300": wave(base=3800.0, seed=2)})

    def resolver(name):
        if name != "custom:gate4":
            raise ValueError(f"未知策略: {name}")
        return custom[0]

    return BacktestService(strategy_resolver=resolver, loader_factory=lambda plan: fake,
                           today=lambda: TODAY, registry=TaskRegistry())


def finish(task):
    assert task.done_event.wait(30) and task.status == "completed", task.error
    return task.result


def first_run(service):
    return finish(service.start(service.plan(parse_spec({"ticker": "600519", "strategy": "custom:gate4",
                                                         "period": "1y"}))))


def rerun(service, saved):
    return finish(service.start(service.plan_rerun(saved), kind="rerun"))


def test_the_definition_fingerprints_the_modules_a_custom_strategy_imports(service, custom):
    definition = first_run(service)["manifest"]["spec"]["strategy_definition"]
    # Not pandas, the standard library or the backtesting packages (those are dependencies).
    assert set(definition["local_modules"]) == {"gate4_helpers"}
    assert len(definition["local_modules"]["gate4_helpers"]) == 64


def test_a_constant_changed_in_an_imported_module_is_not_the_same_strategy(service, custom):
    first = first_run(service)
    saved = service.history_factory().get_run(first["run_id"])
    custom[1].write_text("THRESHOLD = 2\n", encoding="utf-8")

    again = rerun(service, saved)
    assert again["rerun"]["same_strategy"] is False
    assert "gate4_helpers.py" in codes_of(again)["strategy_changed"]["text"]


def test_an_unchanged_module_is_the_same_strategy(service, custom):
    first = first_run(service)
    again = rerun(service, service.history_factory().get_run(first["run_id"]))
    assert again["rerun"]["same_strategy"] is True


def test_a_record_saved_without_those_fingerprints_cannot_claim_the_same_strategy(service, custom):
    first = first_run(service)
    saved = service.history_factory().get_run(first["run_id"])
    saved["manifest"]["spec"]["strategy_definition"].pop("local_modules")

    again = rerun(service, saved)
    assert again["rerun"]["same_strategy"] is None
    assert "gate4_helpers.py" in codes_of(again)["strategy_unverified"]["text"]


def test_a_built_in_strategy_records_no_local_modules():
    from backtesting.strategies.examples import SmaCrossStrategy
    definition = service_module.strategy_definition(SmaCrossStrategy, {}, {"kind": "strategy", "id": "sma_cross"})
    assert definition["local_modules"] is None
