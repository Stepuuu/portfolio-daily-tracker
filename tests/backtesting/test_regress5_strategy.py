"""Round 5 (cr_v5c D1.4): a custom strategy that takes a constant from a module of its
own (``from helper import ALLOCATION``), the constant changed from 25% to 75% and the
service restarted before the rerun, still reported ``same_strategy=true``.

The round-4 fingerprint (local_modules) followed the modules the file's namespace
holds; a constant imported by name leaves only its value there. Baseline 40868ac:
true (the defect itself); round 4: true; now: false. A module that cannot be
resolved for fingerprinting makes the answer null, never true.
"""
import sys
import types

import pytest

from backtesting import service as service_module
from backtesting.service import BacktestService, TaskRegistry, parse_spec
from tests.backtesting.test_service import TODAY, FakeLoader, codes_of, fake_presets, wave

CONSTANT = """
from gate5_alloc import ALLOCATION
from backtesting.strategies.base import Strategy


class Custom(Strategy):
    name = "Custom"

    def on_bar(self):
        if not self.get_position():
            self.buy(pct_cash=ALLOCATION)
"""

UNRESOLVED = CONSTANT + """
    def unused(self):
        from gate5_nowhere import FACTOR           # never executed: no such module to fingerprint
        return FACTOR
"""


class Service:
    """The service with a custom strategy file loaded the way the registry loads it."""

    def __init__(self, folder, monkeypatch, source):
        self.folder, self.source = folder, source
        monkeypatch.syspath_prepend(str(folder))
        self.restart()
        fake = FakeLoader({"SHA:600519": wave(), "IDX:000300": wave(base=3800.0, seed=2)})
        self.service = BacktestService(strategy_resolver=self.resolve, loader_factory=lambda plan: fake,
                                       today=lambda: TODAY, registry=TaskRegistry())

    def restart(self):
        """A new process: the helper is imported again and the file executed again."""
        sys.modules.pop("gate5_alloc", None)
        module = types.ModuleType("gate5_custom")
        exec(compile(self.source, "gate5_custom.py", "exec"), module.__dict__)
        self.cls = module.Custom
        setattr(self.cls, service_module.STRATEGY_SOURCE_ATTR, self.source)

    def resolve(self, name):
        if name != "custom:gate5":
            raise ValueError(f"未知策略: {name}")
        return self.cls

    def run(self):
        task = self.service.start(self.service.plan(parse_spec(
            {"ticker": "600519", "strategy": "custom:gate5", "period": "1y"})))
        return finish(task)

    def rerun(self, saved):
        return finish(self.service.start(self.service.plan_rerun(saved), kind="rerun"))

    def saved(self, run_id):
        return self.service.history_factory().get_run(run_id)


def finish(task):
    assert task.done_event.wait(30) and task.status == "completed", task.error
    return task.result


def bought(result):
    return [fill["quantity"] for fill in result["fills"] if fill["direction"] == "buy"]


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_BACKTEST_DIR", str(tmp_path / "bt"))
    monkeypatch.delenv("TRACKER_DEMO_MODE", raising=False)
    monkeypatch.setitem(sys.modules, "backtesting.presets", fake_presets())
    # A rewrite within the same second and of the same size would otherwise be read from a stale .pyc.
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    (helpers / "gate5_alloc.py").write_text("ALLOCATION = 0.25\n", encoding="utf-8")
    yield helpers
    sys.modules.pop("gate5_alloc", None)


def test_a_constant_changed_in_an_imported_module_after_a_restart_is_not_the_same_strategy(folder, monkeypatch):
    app = Service(folder, monkeypatch, CONSTANT)
    first = app.run()
    saved = app.saved(first["run_id"])
    (folder / "gate5_alloc.py").write_text("ALLOCATION = 0.75\n", encoding="utf-8")
    app.restart()

    again = app.rerun(saved)
    assert bought(again) != bought(first)                       # the constant changed the purchase
    assert again["rerun"]["same_strategy"] is False             # 40868ac and round 4: true
    assert "gate5_alloc.py" in codes_of(again)["strategy_changed"]["text"]
    definition = first["manifest"]["spec"]["strategy_definition"]
    assert set(definition["local_modules"]) == {"gate5_alloc"}


def test_an_unchanged_module_after_a_restart_is_still_the_same_strategy(folder, monkeypatch):
    app = Service(folder, monkeypatch, CONSTANT)
    first = app.run()
    app.restart()
    again = app.rerun(app.saved(first["run_id"]))
    assert again["rerun"]["same_strategy"] is True and bought(again) == bought(first)


def test_a_record_saved_by_round_four_without_the_imported_module_is_not_the_same_strategy(folder, monkeypatch):
    # Round 4 recorded local_modules without a module whose constant was imported by name.
    app = Service(folder, monkeypatch, CONSTANT)
    first = app.run()
    saved = app.saved(first["run_id"])
    saved["manifest"]["spec"]["strategy_definition"]["local_modules"] = {}
    (folder / "gate5_alloc.py").write_text("ALLOCATION = 0.75\n", encoding="utf-8")
    app.restart()
    assert app.rerun(saved)["rerun"]["same_strategy"] is False


def test_a_module_that_cannot_be_resolved_makes_the_answer_unverified(folder, monkeypatch):
    app = Service(folder, monkeypatch, UNRESOLVED)
    first = app.run()
    assert first["manifest"]["spec"]["strategy_definition"]["local_modules"]["gate5_nowhere"] is None
    app.restart()
    again = app.rerun(app.saved(first["run_id"]))
    assert again["rerun"]["same_strategy"] is None
    assert "gate5_nowhere" in codes_of(again)["strategy_unverified"]["text"]


def test_a_built_in_strategy_is_unchanged():
    from backtesting.strategies.examples import SmaCrossStrategy
    definition = service_module.strategy_definition(SmaCrossStrategy, {}, {"kind": "strategy", "id": "sma_cross"})
    assert definition["local_modules"] is None
