PY?=python3

.PHONY: setup test lint typecheck train paper live observability up down smoke clean

setup:
	$(PY) -m pip install -U pip
	@if [ -f poetry.lock ]; then \
		pip install poetry && poetry config virtualenvs.create false && poetry install; \
	else \
		$(PY) -m pip install -r requirements.txt; \
	fi

lint:
	ruff check .

typecheck:
	mypy apps || true

test:
	poetry run pytest -q

train:
	$(PY) -m apps.trainer.main train --config config/app.yaml --timeframe 5m

paper:
	$(PY) -m apps.paper.main run --config config/app.yaml --model-path checkpoints/final_model.zip

live:
	$(PY) -m apps.live.main run --config config/app.yaml --model-path checkpoints/final_model.zip --live

observability:
	docker compose -f docker/docker-compose.observability.yml up -d

up: observability

down:
	docker compose -f docker/docker-compose.observability.yml down

smoke:
	$(PY) -c "import torch, gymnasium as gym, numpy, pyarrow; print('SMOKE OK')"

clean:
	@echo "Cleaning caches and compiled artifacts..."
	@find . -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.py[cod]" -delete 2>/dev/null || true
