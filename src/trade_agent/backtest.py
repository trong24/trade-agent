# ---------------------------------------------------------------------------
# DEPRECATED — file này sẽ bị xóa trong phiên bản tiếp theo.
# Import từ subpackage thay thế:
#   from trade_agent.engine.backtest import BacktestEngine, BacktestResult
#   from trade_agent.engine.metrics import classify_trades, compute_max_drawdown
# ---------------------------------------------------------------------------
from .engine.backtest import BacktestEngine, BacktestResult  # noqa: F401
from .engine.metrics import classify_trades, compute_max_drawdown  # noqa: F401

__all__ = ["BacktestEngine", "BacktestResult", "classify_trades", "compute_max_drawdown"]
