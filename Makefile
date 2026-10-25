PY?=python
ARGS?=

.PHONY: help serve paper train doctor test frontend lint

help:
	$(PY) -m trading_gym.cli --help

serve:
	$(PY) -m trading_gym.cli serve $(ARGS)
paper:
	$(PY) -m trading_gym.cli run-agent --source broker --mode paper $(ARGS)
train:
	$(PY) -m trading_gym.cli train $(ARGS)
doctor:
	$(PY) -m trading_gym.cli doctor
test:
	$(PY) -m pytest -q -m "not integration"
frontend:
	cd frontend && npm ci && npm run test:ci && npm run build
lint:
	$(PY) -m ruff check trading_gym/agent/daemon.py trading_gym/agent/strategy_planner.py trading_gym/agent/shadow_learning.py trading_gym/api trading_gym/cli.py
