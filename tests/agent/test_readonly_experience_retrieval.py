"""System-1 read-only memory retrieval must not rewrite its evidence journal."""

from __future__ import annotations

import time

import pytest

from trading_gym.agent.persistent_experience import (
    MemoryJournalIntegrityError,
    PersistentExperienceMemory,
)


def _old_memory(path):
    memory = PersistentExperienceMemory.open(path)
    memory.record(
        context_tags=["symbol:btcusdt", "regime:chop"],
        outcome="loss",
        lesson="historical cost evidence",
        created_at=time.time() - 220 * 86_400,
    )
    return path.read_bytes(), path.with_suffix(path.suffix + ".anchor").read_bytes()


def test_read_only_memory_retrieval_skips_expired_data_without_rewriting(tmp_path):
    path = tmp_path / "experiences.jsonl"
    journal, anchor = _old_memory(path)

    reader = PersistentExperienceMemory.open(path, read_only=True)
    assert reader.observations() == 0
    assert reader.advice(["symbol:btcusdt"]).consulted == 0
    assert path.read_bytes() == journal
    assert path.with_suffix(path.suffix + ".anchor").read_bytes() == anchor


def test_read_only_memory_reader_cannot_record(tmp_path):
    path = tmp_path / "experiences.jsonl"
    _old_memory(path)
    reader = PersistentExperienceMemory.open(path, read_only=True)
    with pytest.raises(PermissionError, match="read.only"):
        reader.record(
            context_tags=["symbol:btcusdt"],
            outcome="win",
            lesson="must not modify",
        )


def test_ordinary_writable_reopen_still_applies_retention(tmp_path):
    path = tmp_path / "experiences.jsonl"
    _old_memory(path)
    memory = PersistentExperienceMemory.open(path)
    assert memory.observations() == 0
    assert b"historical cost evidence" not in path.read_bytes()


def test_empty_journal_with_nonempty_anchor_is_not_trusted(tmp_path):
    path = tmp_path / "experiences.jsonl"
    memory = PersistentExperienceMemory.open(path)
    memory.record(
        context_tags=["symbol:btcusdt"],
        outcome="win",
        lesson="sealed evidence",
    )
    path.write_text("", encoding="utf-8")
    with pytest.raises(MemoryJournalIntegrityError, match="anchor"):
        PersistentExperienceMemory.open(path, read_only=True)


def test_deleted_journal_with_anchor_cannot_look_like_empty_memory(tmp_path):
    path = tmp_path / "experiences.jsonl"
    memory = PersistentExperienceMemory.open(path)
    memory.record(
        context_tags=["symbol:btcusdt"],
        outcome="win",
        lesson="sealed evidence",
    )
    path.unlink()
    with pytest.raises(MemoryJournalIntegrityError, match="anchor"):
        PersistentExperienceMemory.open(path, read_only=True)


def test_corrupt_journal_json_raises_typed_integrity_error(tmp_path):
    path = tmp_path / "experiences.jsonl"
    memory = PersistentExperienceMemory.open(path)
    memory.record(
        context_tags=["symbol:btcusdt"],
        outcome="loss",
        lesson="test record",
    )
    path.write_text("{garbled\n", encoding="utf-8")
    with pytest.raises(MemoryJournalIntegrityError):
        PersistentExperienceMemory.open(path, read_only=True)
