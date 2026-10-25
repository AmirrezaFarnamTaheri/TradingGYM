# Feature-fixture integrity

The 28 checked-in `data/parquet/*_5m_features.parquet` files are engineered research/test features, not an OHLC price history and not live connector records. Their original release hashes were recovered from Git HEAD into `data/feature-fixtures.sha256`, with only path migration.

Run `python scripts/verify_feature_fixtures.py`. It checks unique hash records, exact membership, regular nonsymlink files and matching contents. `tests/data/test_feature_fixture_inventory.py` verifies valid, modified, extra and missing files and the repository inventory. CI runs both the suite and the direct check.

Runtime lake partitions use `trading_gym/data/lake_cli/manifest.py` and their actual symbol/timeframe directories. A fixture checksum is not a lake Merkle root or proof of market provenance. Do not manufacture partitions or prices from these feature files. Externalization requires a tested, versioned restore path and preserved regression consumers.
