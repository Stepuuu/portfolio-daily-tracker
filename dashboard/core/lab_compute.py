"""Disposable compute process. Inputs and outputs are owned by the research worker."""
import json
from pathlib import Path
import sys

from core.lab_data import frames_from_records, validate_records
from core.lab_store import encode


def main():
    from core.lab_extensions import load_extensions
    load_extensions()
    from backtesting.research import run_experiment
    payload = json.loads(Path(sys.argv[1]).read_text())
    try:
        records = validate_records(payload["records"])
        result = run_experiment(frames_from_records(records), payload["spec"])
        output = {"result": result}
    except (ValueError, KeyError, TypeError) as exc:
        # Validation errors are method feedback; tracebacks and paths remain local.
        output = {"error": str(exc)[:1000]}
    Path(sys.argv[2]).write_text(encode(output), encoding="utf-8")


if __name__ == "__main__":
    main()
