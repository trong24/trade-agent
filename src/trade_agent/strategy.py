# ---------------------------------------------------------------------------
# DEPRECATED — file này sẽ bị xóa trong phiên bản tiếp theo.
# Import từ subpackage thay thế:
#   from trade_agent.strategies.sma import SMACrossStrategy
# ---------------------------------------------------------------------------
from .strategies.sma import SMACrossStrategy  # noqa: F401

__all__ = ["SMACrossStrategy"]
