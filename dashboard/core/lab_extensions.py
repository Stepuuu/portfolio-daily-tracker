"""Load trusted administrator-installed extensions; never accept modules from a request."""
import importlib
import os
import re

_LOADED = set()


def load_extensions():
    for name in filter(None, (p.strip() for p in os.environ.get("TRACKER_LAB_PLUGINS", "").split(","))):
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", name):
            raise ValueError("Research extension must be an installed Python module")
        if name not in _LOADED:
            module = importlib.import_module(name)
            if not callable(getattr(module, "register", None)):
                raise ValueError("Research extension must export register()")
            module.register()
            _LOADED.add(name)
