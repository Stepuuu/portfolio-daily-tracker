"""No exception text leaves the process: what an exception says goes to the log, never to a person or the model.

The backend sources are parsed and every place an exception is turned into text is found: ``str(exc)``,
``repr(exc)``, ``format(exc)``, ``type(exc).__name__``, ``exc.__class__.__name__``, ``exc.args``, ``'%s' % exc``
and ``{exc}`` / ``{exc!r}`` in f-strings, for every name bound by ``except ... as`` and every parameter that
holds an exception. Each must be

* inside a logging call (``logger.*``, ``logging.*``, ``log_failure``, ``print`` / the engine's ``log``: the
  server's log or a command-line script's own output, which the API never returns), or
* a substring test that classifies the error (``'locked' in str(exc)``) and shows nothing, or
* the message of one of the application's own error classes (``APP_ERRORS``): those carry the application's
  fixed text, and this test makes every place that builds one from another exception's text show up here, or
* in ``ALLOWED`` with the reason, line by line.

Anything else fails: a provider's message or an exception's class name is attacker-influenced text, and the
person and the model get the fixed texts of ``core.provider_errors`` instead.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED = ('dashboard/core', 'dashboard/backend', 'dashboard/agents', 'dashboard/providers', 'engine/scripts')
LOG_METHODS = {'debug', 'info', 'warning', 'warn', 'error', 'exception', 'critical', 'log'}
LOG_FUNCTIONS = {'print', 'log_failure', 'log'}

# The application's own error classes: every message is the application's text (Chinese for people, or a
# fixed English sentence of the code), never an upstream library's. A class defined in the application.
APP_ERRORS = {
    'AlertRuleError', 'ApplyFailed', 'BarError', 'BriefError', 'Conflict', 'CsvRefused', 'DataQualityError',
    'DeskStoreError', 'FeeInputError',
    'ImportFailure', 'IndexFetchError', 'InputUnavailable', 'InstrumentError', 'InvalidProposal', 'LedgerBusy',
    'LedgerDamaged', 'LedgerError', 'LedgerUnavailable', 'LegacyError', 'LegacyStale', 'LockUnavailable',
    'MarketUnavailable', 'MemoryWriteRefused', 'NotFound', 'PlanConflict', 'PlanError', 'PlanNotFound',
    'ProfileError', 'ProposalError', 'QuoteRequestError', 'RuleConflict', 'RuleNotFound', 'RunRefused',
    'SetupError', 'StaleProposal', 'SymbolError', 'ToolArgumentError', 'ToolRequestError', 'WatchlistConflict',
    'WatchlistError',
    'WatchlistNotFound',
}

# (file, the line as written, stripped) -> why its exception text may stay.
ALLOWED = {
    # -- parameters typed or documented as the application's own errors --
    ('dashboard/core/agent_tools/_envelope.py', 'message = str(exc)[:300]'):
        'not_proposed() is given only ProposalError, ReadOnlyDemo or LedgerError (their callers); the message '
        'is fenced as untrusted_text when it repeats what the model sent',
    ('dashboard/core/importers/reconcile.py', 'message = str(exc)'):
        'explain() is given only the LedgerError the ledger raised for the row, to pick a fixed explanation',
    ('dashboard/core/importers/transactions.py', 'message = str(exc)'):
        'ledger_reason() is given only the LedgerError / StaleProposal the ledger raised for a row or a batch (callers in '
        'transactions, reconcile.explain, session._ledger_message): the ledger\'s own Chinese messages pass, '
        'English ones become fixed Chinese texts, anything else 账本拒绝了这条记录',
    ('dashboard/core/importers/session.py', 'message = str(exc)'):
        'its callers pass LedgerError / StaleProposal from the ledger; the text picks a fixed explanation',
    ('dashboard/core/lab_service.py', 'text = str(exc)'):
        'compared with the fixed compute texts; anything else is logged and replaced by EXPERIMENT_FAILED',
    ('dashboard/backend/api/desk_errors.py', 'text = str(exc)'): 'a substring test for SQLite busy; shows nothing',
    ('dashboard/core/alerts/holdings.py', 'text = str(exc)'): 'a substring test for SQLite busy; shows nothing',
    ('dashboard/core/ledger.py', 'text = str(exc)'): 'substring tests for SQLite busy / read-only; shows nothing',
    ('dashboard/core/market/quotes.py',
     'reason = (str(exc) if isinstance(exc, SourceError) and str(exc)'):
        "only a SourceError's text is kept: a quote source raises it with its own fixed text; anything else "
        'goes through log_failure',
    ('dashboard/backend/api/chat.py', 'detail = str(error).strip() if type(error) is RuntimeError else ""'):
        'a plain RuntimeError is what the agent loop and the provider adapters raise, with the fixed texts of '
        'the agent and of providers/llm/error_utils; anything else is logged and answered with a fixed text',
    # -- validators whose ValueErrors are the application's text by contract --
    ('dashboard/core/proposals.py', 'raise ApplyFailed(str(exc) or "这项方案未能应用。", True) from None'):
        "a kind's apply refuses with a ValueError subclass carrying its own Chinese text (register_kind)",
    ('dashboard/core/proposals.py', 'raise InvalidProposal(str(exc) or "方案内容未通过校验。") from None'):
        "a kind's validate refuses with a ValueError subclass carrying its own Chinese text (register_kind)",
    ('dashboard/core/importers/holdings.py', 'row.parse_errors[name] = str(exc)'):
        'the cell parsers (importers.values, mapping.apply_unit) raise ValueError with their own Chinese text '
        'and turn every library ValueError (date, time, Decimal) into one',
    ('dashboard/core/importers/transactions.py', "raise ImportFailure(f'第 {row} 行：{what} {exc}') from None"):
        'importers.values.decimal: the cell parser, as above',
    ('dashboard/core/importers/transactions.py', 'row.parse_errors[name] = str(exc)'):
        'parse_date / parse_time / decimal / _unit_value: the cell parsers, as above',
    ('dashboard/core/importers/transactions.py',
     "row.parse_errors['fees'] = f'{header[index] if index < len(header) else \"费用\"}：{exc}'"):
        'decimal / _unit_value: the cell parsers, as above',
    ('dashboard/core/importers/transactions.py', "return f'{name}（{label}）：{exc}'"):
        'importers.values.bounded: the cell parser, as above',
    # -- the person's own code (custom strategies, off unless TRACKER_ALLOW_CUSTOM_STRATEGIES=1) --
    ('dashboard/backend/api/backtest.py', 'raise HTTPException(status_code=400, detail=f"Python 语法错误: {e}")'):
        "the compiler's message about the strategy file the person uploaded: the line to fix in their own code",
    ('dashboard/backend/api/backtest.py', 'raise HTTPException(status_code=400, detail=f"策略加载失败: {e}")'):
        "what importing the person's own uploaded strategy raised: about their code, which they must fix",
    # -- kept inside the process --
    ('dashboard/core/lab_compute.py',
     'output = {"error": classify(exc), "diagnostic": f"{type(exc).__name__}: {str(exc)[:1000]}"}'):
        "the compute child's private result file; the worker only logs diagnostic, people get classify()",
    ('dashboard/core/routines/scheduler.py', 'self.last_error = type(exc).__name__'):
        'kept on the scheduler object for debugging; never serialised or returned',
    ('dashboard/core/close_reports.py', '(type(error).__name__, retry, stamp, *key))'):
        "the type of what an extension's on_close_report raised, kept in desk-extensions.sqlite3 for diagnosis "
        '(close_reports.deliveries); never returned by the API or given to the model',
    # -- the operator's own code (extensions, none unless TRACKER_EXTENSIONS names one) --
    ('dashboard/backend/extensions.py', 'kind = type(exc).__name__'):
        "the type (never the message) of what an installed extension's own code raised, listed for the operator "
        'by GET /api/app/status; never given to the model',
    # -- command-line scripts: their own output, for the person running them --
    ('engine/scripts/create_demo.py',
     'parser.error(str(exc))   # its own refusal: a day it cannot make the demo for, or not a demo directory'):
        "create_demo's own ValueErrors (a day it cannot make the demo for, NotADemo for --refresh), fixed texts: "
        "the usage error of the command",
    ('engine/scripts/portfolio_snapshot.py', 'sys.exit(f"❌ 未保存 {date_str} 快照：{error}")'):
        "calculate_snapshot's own ValueError (a missing exchange rate ...): the command's exit message",
    ('engine/scripts/portfolio_write_lock.py', "raise OSError(f'{command[0]} was not started: {type(error).__name__}') from None"):
        'the pipeline prints it as its own output; the routine keeps that only in the log tail the API never returns',
}


def _caught(handler):
    if handler.type is None:
        return ['BaseException']
    items = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return [item.id if isinstance(item, ast.Name) else item.attr if isinstance(item, ast.Attribute) else '?'
            for item in items]


class _Finder(ast.NodeVisitor):
    def __init__(self):
        self.bound, self.calls, self.found = [], [], []
        self.parents = {}

    def generic_visit(self, node):
        for child in ast.iter_child_nodes(node):
            self.parents[child] = node
        super().generic_visit(node)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.bound.append((node.name, _caught(node)))
        self.generic_visit(node)
        if node.name:
            self.bound.pop()

    def visit_FunctionDef(self, node):
        added = 0
        for argument in node.args.args + node.args.kwonlyargs:
            annotation = ast.unparse(argument.annotation) if argument.annotation is not None else ''
            if argument.arg in ('exc', 'exception') or 'Exception' in annotation or 'Error' in annotation:
                self.bound.append((argument.arg, [annotation or 'parameter']))
                added += 1
        self.generic_visit(node)
        del self.bound[len(self.bound) - added:]

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        function = node.func
        if isinstance(function, ast.Name) and function.id in ('str', 'repr', 'format') and node.args \
                and isinstance(node.args[0], ast.Name):
            self._found(node, node.args[0].id)
        self.calls.append(node)
        self.generic_visit(node)
        self.calls.pop()

    def visit_Attribute(self, node):
        value = node.value
        if node.attr == '__name__' and isinstance(value, ast.Call) and isinstance(value.func, ast.Name) \
                and value.func.id == 'type' and value.args and isinstance(value.args[0], ast.Name):
            self._found(node, value.args[0].id)
        elif node.attr == '__name__' and isinstance(value, ast.Attribute) and value.attr == '__class__' \
                and isinstance(value.value, ast.Name):
            self._found(node, value.value.id)
        elif node.attr == 'args' and isinstance(value, ast.Name):
            self._found(node, value.id)
        self.generic_visit(node)

    def visit_FormattedValue(self, node):
        if isinstance(node.value, ast.Name):
            self._found(node, node.value.id)
        self.generic_visit(node)

    def visit_BinOp(self, node):
        if isinstance(node.op, ast.Mod) and isinstance(node.right, ast.Name):
            self._found(node, node.right.id)
        self.generic_visit(node)

    def _types(self, name):
        return next((types for bound, types in reversed(self.bound) if bound == name), None)

    def _logged(self):
        for call in self.calls:
            function = call.func
            if isinstance(function, ast.Name) and function.id in LOG_FUNCTIONS:
                return True
            if isinstance(function, ast.Attribute) and function.attr in LOG_METHODS:
                base = function.value
                name = base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else ''
                if name in ('logging', 'log') or name.endswith('logger'):
                    return True
        return False

    def _found(self, node, name):
        types = self._types(name)
        if types is not None:
            self.found.append((node, types, self._logged()))


def _substring_test(node, parents):
    """``'locked' in str(exc)`` (also through ``.lower()``): classifies the error, shows nothing."""
    child, parent = node, parents.get(node)
    while isinstance(parent, (ast.Attribute, ast.Call)) and parent is not None and (
            isinstance(parent, ast.Attribute) or parent.func is child):
        child, parent = parent, parents.get(parent)
    return isinstance(parent, ast.Compare) and child in parent.comparators and all(
        isinstance(op, (ast.In, ast.NotIn)) for op in parent.ops)


def findings():
    problems, used = [], set()
    for folder in SCANNED:
        for path in sorted((ROOT / folder).rglob('*.py')):
            relative = str(path.relative_to(ROOT))
            source = path.read_text(encoding='utf-8')
            lines = source.splitlines()
            finder = _Finder()
            tree = ast.parse(source)
            for child in ast.iter_child_nodes(tree):
                finder.parents[child] = tree
            finder.visit(tree)
            for node, types, logged in finder.found:
                if logged or _substring_test(node, finder.parents) or all(t in APP_ERRORS for t in types):
                    continue
                line = lines[node.lineno - 1].strip()
                if (relative, line) in ALLOWED:
                    used.add((relative, line))
                    continue
                problems.append(f'{relative}:{node.lineno}: {line[:140]}  (caught: {", ".join(types)})')
    return problems, used


def test_no_exception_text_reaches_a_person_or_the_model():
    problems, _ = findings()
    assert problems == [], 'exception text outside a log:\n' + '\n'.join(problems)


def test_every_allowed_line_still_exists_and_every_app_error_is_ours():
    _, used = findings()
    assert set(ALLOWED) - used == set(), 'allowed lines that no longer exist: ' + repr(sorted(set(ALLOWED) - used))
    defined = set()
    for folder in ('dashboard', 'engine/scripts'):                   # the application's sources, not the frontend
        for path in (ROOT / folder).rglob('*.py'):
            if 'frontend' in path.parts or 'node_modules' in path.parts:
                continue
            defined |= {node.name for node in ast.walk(ast.parse(path.read_text(encoding='utf-8')))
                        if isinstance(node, ast.ClassDef)}
    assert APP_ERRORS <= defined, 'not classes of this application: ' + repr(sorted(APP_ERRORS - defined))
