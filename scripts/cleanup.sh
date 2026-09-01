#!/bin/bash
echo "🧹 Starting Project Cleanup..."

# Remove temporary test files from earlier phases
rm -f tests/phase1_tests.py
rm -f tests/phase4_features.py
rm -f panic.signal
rm -f health.heartbeat
rm -f temp_runtime_config.yaml
rm -f shared_metrics.dat

# Note: Documentation files like docs/phase5_operations.md are kept
# as they are referenced in the README and contain valuable info.
# If they are truly legacy and fully integrated into 1-6 guides,
# they can be removed, but for now we keep them to be safe based on conflict analysis.

# Remove pycache
find . -type d -name "__pycache__" -exec rm -rf {} +
find . -type f -name "*.pyc" -delete

# Clean logs
rm -rf logs/local/*
rm -rf tensorboard/local/*

echo "✅ Cleanup Complete. System ready for launch."
