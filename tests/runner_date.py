"""Run ``engine/scripts/create_demo.py`` as a smoke does, optionally on a faked runner date.

A smoke's demo must be made for the day its scripted clock shows (``--today``), never for the day the
tests happen to run: otherwise, weeks later, the demo's opening balance falls after the scripted clock
and the pages have no holdings. To show that a smoke does not depend on the runner's date, set
``TRACKER_TEST_RUNNER_DATE=YYYY-MM-DD``: the script then runs with its clock (``create_demo.datetime``,
which gives its default day) answering that day at 10:00 Shanghai, as if the tests ran then. Used by
the smokes and by ``tests/test_fixture_dates.py``.

Run as a script, this module is that wrapper: ``runner_date.py <YYYY-MM-DD> [create_demo args...]``.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CREATE_DEMO = ROOT / "engine" / "scripts" / "create_demo.py"
RUNNER_DATE_ENV = "TRACKER_TEST_RUNNER_DATE"


def create_demo_argv(*args, runner=None):
    """The command that runs ``create_demo.py`` with ``args``, on the faked runner date when one is set
    (``runner``, else ``TRACKER_TEST_RUNNER_DATE``)."""
    runner = runner or os.environ.get(RUNNER_DATE_ENV)
    if runner:
        return [sys.executable, str(Path(__file__).resolve()), runner, *args]
    return [sys.executable, str(CREATE_DEMO), *args]


def runner_datetime(day):
    """A ``datetime`` whose ``now()`` is 10:00 Shanghai on ``day`` (YYYY-MM-DD), for ``create_demo.datetime``."""
    import datetime as real

    fake_day = real.date.fromisoformat(day)
    shanghai = real.timezone(real.timedelta(hours=8))

    class RunnerDatetime(real.datetime):
        @classmethod
        def now(cls, tz=None):
            moment = real.datetime.combine(fake_day, real.time(10, 0), tzinfo=shanghai)
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    return RunnerDatetime


if __name__ == "__main__":
    here = str(Path(__file__).resolve().parent)
    sys.path[:] = [entry for entry in sys.path if str(Path(entry or ".").resolve()) != here]
    sys.path.insert(0, str(CREATE_DEMO.parent))
    import create_demo
    create_demo.datetime = runner_datetime(sys.argv[1])
    create_demo.main(sys.argv[2:])
