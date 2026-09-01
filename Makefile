PY?=python3

.PHONY: setup test lint typecheck train paper live smoke clean typecheck-full

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
	mypy trading_gym/accounting trading_gym/production trading_gym/authz trading_gym/agent/arms trading_gym/agent/fusion.py trading_gym/agent/planner.py trading_gym/agent/autonomy_ladder.py trading_gym/agent/revision.py trading_gym/agent/budget_gate.py --ignore-missing-imports --follow-imports=silent

typecheck-full:
	@echo "Tree-wide mypy is advisory (pre-existing debt in peripheral modules):"
	-mypy . --ignore-missing-imports

test:
	poetry run pytest -q

train:
	$(PY) -m apps.trainer.main train --config config/app.yaml --timeframe 5m

paper:
	$(PY) -m apps.paper.main run --config config/app.yaml --model-path checkpoints/final_model.zip

live:
	$(PY) -m apps.live.main run --config config/app.yaml --model-path checkpoints/final_model.zip --live

smoke:
	$(PY) -c "import torch, gymnasium as gym, numpy, pyarrow; print('SMOKE OK')"

clean:
	@echo "Cleaning caches and compiled artifacts..."
	@find . -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.py[cod]" -delete 2>/dev/null || true
