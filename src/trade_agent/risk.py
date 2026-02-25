# ---------------------------------------------------------------------------
# DEPRECATED — file này sẽ bị xóa trong phiên bản tiếp theo.
# Import từ subpackage thay thế:
#   from trade_agent.risks.fixed_fraction import FixedFractionRisk
# ---------------------------------------------------------------------------
from .risks.fixed_fraction import FixedFractionRisk  # noqa: F401

__all__ = ["FixedFractionRisk"]
