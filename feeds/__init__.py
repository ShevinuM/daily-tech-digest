"""Feed auto-discovery.

Every .py file in this directory that defines a `fetch(cutoff, **opts)` function
is a feed. Drop a file in to add a source; delete it to remove one. Nothing else
needs editing.

A feed module looks like:

    NAME = "example"          # optional, defaults to the filename
    ENABLED = True            # optional, set False to disable without deleting

    def fetch(cutoff, *, verbose=False, **opts):
        '''Return a list of utils.item() dicts published at or after cutoff.'''
        return []

Raising is fine — main.py records the error and carries on with other feeds.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

_PKG = __name__


def discover(only: list[str] | None = None) -> list:
    """Import every feed module. Returns them sorted by name.

    `only` filters to specific feed names, for `main.py fetch --only dev_to`.
    """
    found = []
    for mod_info in pkgutil.iter_modules([str(Path(__file__).parent)]):
        if mod_info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{_PKG}.{mod_info.name}")
        if not callable(getattr(mod, "fetch", None)):
            continue
        if not getattr(mod, "ENABLED", True):
            continue
        if not hasattr(mod, "NAME"):
            mod.NAME = mod_info.name
        if only and mod.NAME not in only:
            continue
        found.append(mod)
    return sorted(found, key=lambda m: m.NAME)


def names() -> list[str]:
    return [m.NAME for m in discover()]
