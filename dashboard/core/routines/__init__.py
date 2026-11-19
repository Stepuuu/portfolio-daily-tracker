"""Daily routines the backend runs by itself; see ``core.routines.scheduler``."""
import importlib
import logging

logger = logging.getLogger(__name__)

# Modules that register a routine kind when they are imported. A package that
# adds a kind appends its module here (one line per package).
KIND_MODULES = (
    "core.routines.close_snapshot",
    "core.routines.premarket",
    "core.routines.postmarket",
)


def load_kinds() -> None:
    """Import every kind module, so that each one registers itself.

    A module that fails to import is logged and left out; the other kinds
    still run.
    """
    for name in KIND_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:
            logger.warning("routine module %s failed to import: %s", name, type(exc).__name__)
