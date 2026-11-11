"""Hands sandbox world: many resettable fixture sites in one stdlib-only server (#39830)."""

from . import (  # noqa: F401  (register sites)
    core,
    sites_commerce,
    sites_comms,
    sites_forms,
    sites_session,
    sites_traps,
)
from .core import SITES, App, Store
from .tasks import TASK_SITES

__all__ = ["App", "SITES", "Store", "TASK_SITES", "core"]
