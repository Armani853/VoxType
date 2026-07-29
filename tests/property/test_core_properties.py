from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from voxtype.audio.models import AudioChunk
from voxtype.audio.spool import DiskSpool, LosslessAudioQueue
from voxtype.core.models import (
    InjectionMode,
    Language,
    ModelProfile,
    PreprocessingProfile,
    TargetMode,
    TranscriptFragment,
)
from voxtype.sessions.journal import SessionJournal
from voxtype.sessions.repository import SessionRepository
from voxtype.settings.repository import SettingsRepository
from voxtype.settings.schema import AppSettings
from voxtype.transcript.formatter import normalize_internal_spacing
from voxtype.transcript.merger import TranscriptMerger
from voxtype.transcript.vocabulary import Vocabulary

SAFE_CHARACTER = st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00")
SAFE_TEXT = st.text(SAFE_CHARACTER, max_size=200)
TOKEN = st.text(
    alphabet=st.sampled_from(
        tuple("abcdefghijklmnopqrstuvwxyz0123456789абвгдежзиклмнопрстуфхцчшщ")
    ),
    min_size=1,
    max_size=24,
)


@settings(max_examples=120, deadline=None)
@given(SAFE_TEXT)
def test_spacing_normalization_is_idempotent_and_removes_horizontal_runs(text: str) -> None:
    normalized = normalize_internal_spacing(text)
    assert normalize_internal_spacing(normalized) == normalized
    assert "\t" not in normalized
    assert "  " not in normalized


@settings(max_examples=60, deadline=None)
@given(
    language=st.sampled_from(tuple(Language)),
    profile=st.sampled_from(tuple(ModelProfile)),
    injection=st.sampled_from(tuple(InjectionMode)),
    target=st.sampled_from(tuple(TargetMode)),
    preprocessing=st.sampled_from(tuple(PreprocessingProfile)),
    overlay=st.booleans(),
    auto_insert=st.booleans(),
    retention=st.integers(min_value=1, max_value=3650),
    delay=st.integers(min_value=0, max_value=5000),
    vocabulary=st.lists(TOKEN, max_size=20, unique_by=str.casefold),
)
def test_settings_save_load_round_trip_is_normalized(
    language: Language,
    profile: ModelProfile,
    injection: InjectionMode,
    target: TargetMode,
    preprocessing: PreprocessingProfile,
    overlay: bool,
    auto_insert: bool,
    retention: int,
    delay: int,
    vocabulary: list[str],
) -> None:
    configured = AppSettings()
    configured.general.show_overlay = overlay
    configured.dictation.language = language
    configured.dictation.model_profile = profile
    configured.dictation.injection_mode = injection
    configured.dictation.target_mode = target
    configured.dictation.auto_insert = auto_insert
    configured.dictation.insertion_delay_ms = delay
    configured.dictation.custom_vocabulary = vocabulary
    configured.audio.preprocessing_profile = preprocessing
    configured.privacy.retention_days = retention
    with tempfile.TemporaryDirectory(prefix="voxtype-settings-property-") as directory:
        repository = SettingsRepository(Path(directory) / "settings.json")
        repository.save(configured)
        assert repository.load().to_dict() == configured.to_dict()


@settings(max_examples=80, deadline=None)
@given(st.lists(TOKEN, min_size=1, max_size=40))
def test_non_overlapping_merger_preserves_token_order_and_invents_nothing(
    tokens: list[str],
) -> None:
    merger = TranscriptMerger()
    for sequence, token in enumerate(tokens):
        outcome = merger.merge(
            TranscriptFragment(
                sequence=sequence,
                text=token,
                words=(),
                chunk_start_s=float(sequence),
                chunk_end_s=float(sequence) + 0.5,
                language=Language.RU,
            ),
            capitalize=False,
        )
        assert outcome.committed_text.split() == tokens[: sequence + 1]
    assert merger.committed_text.split() == tokens


@settings(max_examples=50, deadline=None)
@given(st.lists(TOKEN, min_size=1, max_size=30))
def test_journal_commits_are_monotonic_and_never_disappear(fragments: list[str]) -> None:
    with (
        tempfile.TemporaryDirectory(prefix="voxtype-journal-property-") as directory,
        SessionRepository(Path(directory) / "sessions.sqlite3") as repository,
    ):
        session_id = repository.create(Language.RU, "fixture", ModelProfile.BALANCED, "fixture.exe")
        journal = SessionJournal(repository, session_id)
        sequences = [journal.commit_text(fragment) for fragment in fragments]
        record = repository.get(session_id)
        assert sequences == list(range(len(fragments)))
        assert record is not None
        assert record.committed_text == "".join(fragments)


@settings(max_examples=35, deadline=None)
@given(
    total=st.integers(min_value=1, max_value=30),
    ram_chunks=st.integers(min_value=1, max_value=6),
)
def test_lossless_queue_is_fifo_for_every_spill_boundary(total: int, ram_chunks: int) -> None:
    with tempfile.TemporaryDirectory(prefix="voxtype-queue-property-") as directory:
        queue = LosslessAudioQueue(
            max_ram_chunks=ram_chunks,
            spill_threshold=0.5,
            spool=DiskSpool(Path(directory), max_bytes=4_000_000),
        )
        for sequence in range(total):
            queue.put(AudioChunk(sequence, np.full(16, sequence / 100, dtype=np.float32)))
        observed: list[int] = []
        while len(observed) < total:
            lease = queue.acquire(0.05)
            assert lease is not None
            observed.append(lease.chunk.sequence)
            queue.ack(lease)
        assert observed == list(range(total))
        assert queue.snapshot().spooled_chunks == 0
        queue.close()


@settings(max_examples=80, deadline=None)
@given(entries=st.lists(SAFE_TEXT, max_size=260), limit=st.integers(min_value=1, max_value=40))
def test_vocabulary_is_bounded_unique_atomic_and_prompt_limited(
    entries: list[str], limit: int
) -> None:
    vocabulary = Vocabulary(entries, max_entries=limit)
    assert len(vocabulary.entries) <= limit
    assert len({entry.casefold() for entry in vocabulary.entries}) == len(vocabulary.entries)
    assert all(entry and len(entry) <= 120 for entry in vocabulary.entries)
    assert len(vocabulary.initial_prompt(37)) <= 37
    with tempfile.TemporaryDirectory(prefix="voxtype-vocabulary-property-") as directory:
        path = Path(directory) / "vocabulary.json"
        vocabulary.save(path)
        assert Vocabulary.load(path).entries == vocabulary.entries
