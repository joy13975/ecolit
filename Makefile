.PHONY: help install dev test lint format run scan clean shell sync add upgrade stop test-tesla tesla-list tesla-discover tesla-config

help:
	@echo "Available commands:"
	@echo "  make install     - Install production dependencies"
	@echo "  make dev         - Install development dependencies"
	@echo "  make test        - Run tests"
	@echo "  make test-tesla     - Test Tesla API client (SAFE: read-only, no charging commands)"
	@echo "  make tesla-list     - List registered Tesla products (SAFE: read-only)"
	@echo "  make tesla-discover - Discover your Tesla vehicles and get config values"
	@echo "  make tesla-config   - Show manual configuration guide for vehicle ID/tag"
	@echo "  make lint        - Run ruff linter"
	@echo "  make format      - Format code with ruff"
	@echo "  make run         - Run the main application"
	@echo "  make stop        - Stop any running ecolit processes"
	@echo "  make scan        - Scan for ECHONET Lite devices"
	@echo "  make clean       - Clean cache and build files"
	@echo "  make shell       - Start Python REPL in virtual environment"
	@echo "  make sync        - Sync dependencies with pyproject.toml"
	@echo "  make add PKG=x   - Add a new package"
	@echo "  make upgrade     - Upgrade all dependencies"

install:
	uv sync --no-dev

dev:
	uv sync
	uv add --dev ruff pytest pytest-cov pytest-asyncio

test:
	uv run pytest tests/ -v --cov=ecolit --cov-report=term-missing

lint:
	uv run ruff check .

format:
	uv run ruff format .
	uv run ruff check --fix .

run:
	uv run python -m ecolit

scan:
	uv run python scan.py

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.pyd" -delete
	find . -type f -name ".coverage" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true

shell:
	uv run python

sync:
	uv sync

add:
ifndef PKG
	$(error PKG is not set. Usage: make add PKG=package_name)
endif
	uv add $(PKG)

upgrade:
	uv lock --upgrade

stop:
	@echo "Stopping ecolit processes..."
	@pkill -f "ecolit" 2>/dev/null || true
	@lsof -ti:3610 2>/dev/null | xargs kill -9 2>/dev/null || true
	@echo "All ecolit processes stopped."

test-tesla:
	@echo "Testing Tesla API client (read-only operations)..."
	@echo "⚠️  SAFE MODE: Only authentication and read operations will be performed"
	@echo "🚫 NO WRITE OPERATIONS: Charging commands will NOT be executed"
	@echo ""
	@uv run python scripts/tesla_test.py

tesla-list:
	@echo "Listing registered Tesla products..."
	@uv run python scripts/tesla_list.py

tesla-discover:
	@echo "Discovering your Tesla vehicles..."
	@uv run python scripts/tesla_discover.py

tesla-config:
	@echo "Tesla configuration guide..."
	@uv run python scripts/tesla_config_guide.py