"""Structured holdings CLI, using fictional positions."""
import json
import sys

import pytest

import portfolio_manager as manager


DAY = "2026-09-09"


@pytest.fixture
def cli(tmp_path, monkeypatch):
    folder = tmp_path / "holdings"
    folder.mkdir()
    path = folder / f"{DAY}.json"
    path.write_text(json.dumps({"date": DAY, "groups": {"Account": {
        "positions": [{"name": "Example", "ticker": "SHA:600000", "quantity": 4000, "cost_price": 50}],
        "cash": 0, "fund": 0, "cost_basis": 200000,
    }}}))
    monkeypatch.setattr(manager, "PORTFOLIO_DIR", str(tmp_path))

    def run(*arguments):
        monkeypatch.setattr(sys, "argv", ["portfolio_manager.py", "--date", DAY, *arguments])
        manager.main()
        positions = json.loads(path.read_text())["groups"]["Account"]["positions"]
        return positions[0] if positions else None

    return run


def update(*arguments):
    return ("update", "--group", "Account", "--ticker", "SHA:600000", *arguments)


def test_reducing_a_position_keeps_its_average_cost(cli, capsys):
    position = cli(*update("--quantity", "-1000", "--cost-price", "80"))
    assert position["quantity"] == 3000
    assert position["cost_price"] == 50
    assert "成本价保持" in capsys.readouterr().out
    assert cli(*update("--quantity", "-1000"))["cost_price"] == 50
    assert cli(*update("--quantity", "-2000", "--cost-price", "80")) is None


def test_adding_to_a_position_still_averages_the_cost(cli):
    position = cli(*update("--quantity", "1000", "--cost-price", "80"))
    assert position["quantity"] == 5000
    assert position["cost_price"] == 56
    assert cli(*update("--quantity", "1000"))["cost_price"] == 56
