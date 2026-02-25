# ---------------------------------------------------------------------------
# DEPRECATED — file này sẽ bị xóa trong phiên bản tiếp theo.
# Import từ subpackage thay thế:
#   from trade_agent.loaders.csv import load_candles_from_csv
# ---------------------------------------------------------------------------
from .loaders.csv import load_candles_from_csv  # noqa: F401

__all__ = ["load_candles_from_csv"]
