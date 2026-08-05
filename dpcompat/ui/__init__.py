"""Textual TUI for interactive data-pack migration and plugin management.

The interface lets users pick a data-pack directory or ZIP, choose target
releases, adjust the fail-closed policy, run the migration, and manage the
plugin store.  It is a thin presentation layer: all compilation logic lives in
:mod:`dpcompat.engine` and all plugin logic in :mod:`dpcompat.plugins`.
"""

from .app import DpCompatApp

__all__ = ["DpCompatApp"]
