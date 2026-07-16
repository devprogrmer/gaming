.PHONY: help install test cov lint format build check clean

help:
	@echo "Targets:"
	@echo "  install   install package with dev extras (editable)"
	@echo "  test      run the test suite"
	@echo "  cov       run tests with coverage report"
	@echo "  lint      run ruff checks"
	@echo "  format    auto-format and sort imports with ruff"
	@echo "  build     build sdist + wheel and verify with twine"
	@echo "  check     lint + tests (CI gate)"
	@echo "  clean     remove build/test artifacts"

install:
	python -m pip install -e ".[dev]"

test:
	pytest

cov:
	pytest --cov=gaming --cov-report=term-missing

lint:
	ruff check .

format:
	ruff format .
	ruff check --fix .

build:
	python -m build
	twine check dist/*

check: lint test

clean:
	rm -rf build dist *.egg-info src/*.egg-info .pytest_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
