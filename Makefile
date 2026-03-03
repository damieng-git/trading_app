.PHONY: install install-dev test lint typecheck build serve clean

install:
	pip install -e .

install-dev:
	pip install -e ".[dev]"

test:
	python -m pytest tests/ -v

test-cov:
	python -m pytest tests/ --cov=trading_dashboard --cov=apps --cov-report=term-missing

lint:
	ruff check trading_dashboard/ apps/ tests/

lint-fix:
	ruff check --fix trading_dashboard/ apps/ tests/

typecheck:
	mypy trading_dashboard/ apps/ --ignore-missing-imports

build:
	python -m trading_dashboard dashboard build

serve:
	python -m apps.dashboard.serve_dashboard

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
