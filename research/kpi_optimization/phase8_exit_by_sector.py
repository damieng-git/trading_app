"""Re-export shim — delegates to legacy/phase8_exit_by_sector.py.

Multiple non-legacy phase scripts import from this module name.
The actual implementation lives in legacy/ after the directory reorganization.
"""
from research.kpi_optimization.legacy.phase8_exit_by_sector import *  # noqa: F401,F403
