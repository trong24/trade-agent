# ---------------------------------------------------------------------------
# DEPRECATED — file này sẽ bị xóa trong phiên bản tiếp theo.
# Import từ subpackage thay thế:
#   from trade_agent.brokers.paper import PaperBroker
# ---------------------------------------------------------------------------
from .brokers.paper import PaperBroker  # noqa: F401

__all__ = ["PaperBroker"]
