.PHONY: help install dev test test-full test-behavior test-integration test-backtest lint format run scan clean shell sync add upgrade stop test-tesla tesla-list tesla-discover tesla-config tesla-mint tesla-refresh tesla-control

help:
	@echo "Available commands:"
	@echo "  make install     - Install production dependencies"
	@echo "  make dev         - Install development dependencies"
	@echo "  make test        - Run all EV charging tests (behavior + integration + backtest)"
	@echo "  make test-full   - Run complete test suite with coverage"
	@echo "  make test-behavior - Run EV charging behavior tests with clear criteria"
	@echo "  make test-integration - Run full integration tests with synthetic data"
	@echo "  make test-backtest - Run time-accelerated backtesting with real timing"
	@echo "  make test-tesla     - Test Tesla API client (SAFE: read-only, no charging commands)"
	@echo "  make tesla-list     - List registered Tesla products (SAFE: read-only)"
	@echo "  make tesla-control  - Interactive Tesla charging control CLI"
	@echo "  make tesla-discover - Discover your Tesla vehicles and get config values"
	@echo "  make tesla-config   - Show manual configuration guide for vehicle ID/tag"
	@echo "  make tesla-mint     - Complete initial Tesla setup (OAuth + registration)"
	@echo "  make tesla-refresh  - Refresh tokens and verify registration (ongoing)"
	@echo "  make lint        - Run ruff linter"
	@echo "  make format      - Format code with ruff"
	@echo "  make run         - Run the main application in control mode"
	@echo "  make run-dry     - Run in dry-run mode (monitoring only)"
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

test: test-behavior test-integration test-backtest
	@echo ""
	@echo "🎯 All tests completed - behavior, integration, and time-accelerated backtesting"

test-full:
	uv run pytest tests/ -v --cov=ecolit --cov-report=term-missing

test-behavior:
	@echo "Running EV charging behavior tests with deterministic success criteria..."
	@echo "✅ DETERMINISTIC TESTS: Clear pass/fail criteria for each scenario"
	@echo ""
	uv run pytest tests/test_ev_charging_behavior.py -v

test-integration:
	@echo "Running full integration tests with synthetic data..."
	@echo "🔗 INTEGRATION: Full app pipeline with realistic synthetic data & clear criteria"
	@echo ""
	@mkdir -p data/synth
	uv run pytest tests/test_ev_charging_behavior.py::TestEVChargingIntegration -v

test-backtest:
	@echo "Running energy flow effects testing with grid import prevention..."
	@echo "⚡ ENERGY FLOW: Validates ECO/HURRY prevent grid import, EMERGENCY maximizes EV charging"
	@echo ""
	@mkdir -p data/synth
	uv run pytest tests/test_ev_charging_behavior.py::TestEnergyFlowEffects -v

lint:
	uv run ruff check .

format:
	uv run ruff format .
	uv run ruff check --fix .

run:
	uv run python -m ecolit

run-dry:
	uv run python -m ecolit --dry

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
	@uv run python -m ecolit.tesla.test

tesla-list:
	@echo "Listing registered Tesla products..."
	@uv run python -m ecolit.tesla.list

tesla-discover:
	@echo "Discovering your Tesla vehicles..."
	@uv run python -m ecolit.tesla.discover

tesla-config:
	@echo "Tesla configuration guide..."
	@uv run python -m ecolit.tesla.config_guide

tesla-mint:
	@echo "Starting complete Tesla setup (OAuth + registration)..."
	@uv run python -m ecolit.tesla.mint

tesla-refresh:
	@echo "Refreshing Tesla tokens and verifying registration..."
	@uv run python -m ecolit.tesla.refresh

tesla-control:
	@echo "Starting Tesla charging control CLI..."
	@uv run python -m ecolit.tesla.control