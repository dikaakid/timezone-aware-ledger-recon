"""Timezone-aware ledger reconciliation toolkit.

Reconciles a bank statement, an internal ledger, and a verification
system when the sources share no unique transaction identifier and
report timestamps in different timezones.
"""

from .config import ReconConfig
from .core import build_line_per_line, summarize

__version__ = "1.3.0"
__all__ = ["ReconConfig", "build_line_per_line", "summarize"]
