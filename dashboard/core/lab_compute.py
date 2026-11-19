"""Disposable compute process. Inputs and outputs are owned by the research worker.

A failure is reported as one of ``ERROR_TEXTS``, a fixed classification: it
is shown to the person and becomes the model's feedback on the experiment, and
the exception's own message can quote the data (a symbol, a column, a value)
or a library. The message goes to ``diagnostic``, which the worker logs and
never stores or passes on.
"""
import json
from pathlib import Path
import sys

from core.lab_data import frames_from_records, validate_records
from core.lab_store import encode

ERROR_VALUE = "实验参数或数据不满足方法要求（例如样本太短、参数超出范围）"
ERROR_FIELD = "实验缺少必要的数据列或参数"
ERROR_TYPE = "实验的数据或参数类型不正确"
ERROR_TEXTS = (ERROR_VALUE, ERROR_FIELD, ERROR_TYPE)


def classify(exc):
    if isinstance(exc, KeyError):
        return ERROR_FIELD
    if isinstance(exc, TypeError):
        return ERROR_TYPE
    return ERROR_VALUE


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
        # A fixed classification for the person and the model; the details stay local (logged by the worker).
        output = {"error": classify(exc), "diagnostic": f"{type(exc).__name__}: {str(exc)[:1000]}"}
    Path(sys.argv[2]).write_text(encode(output), encoding="utf-8")


if __name__ == "__main__":
    main()
