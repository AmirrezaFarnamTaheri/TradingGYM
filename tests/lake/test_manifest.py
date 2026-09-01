"""Roundtrip tests for lake manifest helpers."""

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from apps.lake.manifest import write_daily_merkle, write_partition_manifest, verify_lake


def test_manifest_and_merkle_roundtrip(tmp_path) -> None:
    dataframe = pd.DataFrame(
        {
            "ts": pd.date_range("2025-01-01", periods=3, freq="5min", tz="UTC"),
            "x": [1, 2, 3],
        }
    )
    partition_dir = tmp_path / "part"
    partition_dir.mkdir()
    pq_path = partition_dir / "part.parquet"
    pq.write_table(pa.Table.from_pandas(dataframe), pq_path)

    manifest_path = tmp_path / "part.manifest.json"
    write_partition_manifest(partition_dir, manifest_path)

    merkle_path = tmp_path / "daily.merkle.json"
    write_daily_merkle(tmp_path, merkle_path)

    assert verify_lake(tmp_path, tmp_path, merkle_path)
