.PHONY: install lint test clean

install:
	pip install -r requirements.txt -r requirements-dev.txt

lint:
	python -m pyflakes src/ tests/

test:
	python -m pytest -q --tb=short

clean:
	find . -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
