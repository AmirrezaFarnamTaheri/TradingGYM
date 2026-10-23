PY?=python
ARGS?=

.PHONY: help setup doctor test lint typecheck typecheck-full train paper live smoke clean

help:
	$(PY) scripts/project.py --help

setup doctor test lint typecheck typecheck-full train paper live smoke:
	$(PY) scripts/project.py $@ $(ARGS)

clean:
	@echo "Remove only project-generated caches deliberately; automatic recursive cleanup is disabled to protect virtual environments."
	@exit 2
