.PHONY: install test lint format sync validate check

install:
	pip install -e ".[dev]"

test:
	pytest tests/ -v --tb=short

lint:
	ruff check .

format:
	ruff format .

sync:
	python -m trade_agent.scripts.sync_klines --symbol BTCUSDT --interval 1m

validate:
	python -m trade_agent.scripts.validate_data --symbol BTCUSDT --interval 1m

check: format lint test
