"""Explainable hallucination checks and a single bounded retry policy."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import ClassVar

import numpy as np

from .base import ASRBackend, TranscriptionRequest, TranscriptionResult

_TOKEN = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+(?:[-'][0-9A-Za-zА-Яа-яЁё]+)*", re.UNICODE)
_SENTENCE = re.compile(r"[.!?…]+")


class HallucinationIssue(StrEnum):
    TEXT_ON_SILENCE = "text_on_silence"
    LOW_PROBABILITY = "low_probability"
    HIGH_COMPRESSION = "high_compression"
    REPEATED_SENTENCE = "repeated_sentence"
    REPEATED_NGRAM = "repeated_ngram"
    WRONG_LANGUAGE = "wrong_language"
    SUBTITLE_JUNK = "subtitle_junk"
    IMPOSSIBLE_TEXT_RATE = "impossible_text_rate"
    REPEATED_WORD = "repeated_word"
    DUPLICATE_COMMITTED_TEXT = "duplicate_committed_text"
    MODEL_NO_SPEECH = "model_no_speech"
    RETRY_FAILED = "retry_failed"


@dataclass(frozen=True, slots=True)
class HallucinationSettings:
    silence_dbfs: float = -56.0
    minimum_word_probability: float = 0.32
    maximum_compression_ratio: float = 2.5
    maximum_words_per_second: float = 7.0
    duplicate_minimum_tokens: int = 3
    suspicious_score: int = 2


@dataclass(frozen=True, slots=True)
class GuardAssessment:
    suspicious: bool
    score: int
    issues: tuple[HallucinationIssue, ...]


@dataclass(frozen=True, slots=True)
class GuardedTranscription:
    result: TranscriptionResult
    assessment: GuardAssessment
    first_assessment: GuardAssessment
    retried: bool
    retry_model_id: str | None = None


class HallucinationGuard:
    """Flag suspect output without silently deleting the user's words."""

    _junk_phrases = (
        "thanks for watching",
        "thank you for watching",
        "subscribe to the channel",
        "subtitles by",
        "amara.org",
        "продолжение следует",
        "спасибо за просмотр",
        "подписывайтесь на канал",
        "редактор субтитров",
    )

    _weights: ClassVar[dict[HallucinationIssue, int]] = {
        HallucinationIssue.TEXT_ON_SILENCE: 4,
        HallucinationIssue.LOW_PROBABILITY: 1,
        HallucinationIssue.HIGH_COMPRESSION: 2,
        HallucinationIssue.REPEATED_SENTENCE: 3,
        HallucinationIssue.REPEATED_NGRAM: 2,
        HallucinationIssue.WRONG_LANGUAGE: 2,
        HallucinationIssue.SUBTITLE_JUNK: 4,
        HallucinationIssue.IMPOSSIBLE_TEXT_RATE: 2,
        HallucinationIssue.REPEATED_WORD: 4,
        HallucinationIssue.DUPLICATE_COMMITTED_TEXT: 3,
        HallucinationIssue.MODEL_NO_SPEECH: 2,
        HallucinationIssue.RETRY_FAILED: 1,
    }

    def __init__(self, settings: HallucinationSettings | None = None) -> None:
        self.settings = settings or HallucinationSettings()

    def assess(
        self,
        request: TranscriptionRequest,
        result: TranscriptionResult,
        *,
        committed_text: str = "",
    ) -> GuardAssessment:
        text = " ".join(result.text.split())
        if not text:
            return GuardAssessment(False, 0, ())
        normalized = _normalize(text)
        tokens = _tokens(normalized)
        issues: list[HallucinationIssue] = []

        rms = (
            float(np.sqrt(np.mean(np.square(request.audio, dtype=np.float64))))
            if request.audio.size
            else 0.0
        )
        dbfs = 20.0 * math.log10(max(rms, 1e-12))
        if dbfs <= self.settings.silence_dbfs:
            issues.append(HallucinationIssue.TEXT_ON_SILENCE)

        probability = result.average_word_probability
        if probability is None:
            log_probabilities = [
                item.average_log_probability
                for item in result.segments
                if item.average_log_probability is not None
            ]
            if log_probabilities:
                probability = math.exp(float(np.mean(log_probabilities)))
        if probability is not None and probability < self.settings.minimum_word_probability:
            issues.append(HallucinationIssue.LOW_PROBABILITY)

        ratios = [
            item.compression_ratio for item in result.segments if item.compression_ratio is not None
        ]
        if ratios and max(ratios) > self.settings.maximum_compression_ratio:
            issues.append(HallucinationIssue.HIGH_COMPRESSION)
        no_speech = [
            item.no_speech_probability
            for item in result.segments
            if item.no_speech_probability is not None
        ]
        if no_speech and max(no_speech) >= 0.8:
            issues.append(HallucinationIssue.MODEL_NO_SPEECH)

        sentences = [_normalize(item) for item in _SENTENCE.split(text) if len(_tokens(item)) >= 3]
        if any(count >= 3 for count in Counter(sentences).values()):
            issues.append(HallucinationIssue.REPEATED_SENTENCE)
        if self._has_repeated_ngram(tokens):
            issues.append(HallucinationIssue.REPEATED_NGRAM)
        if len(tokens) >= 8 and Counter(tokens).most_common(1)[0][1] / len(tokens) >= 0.65:
            issues.append(HallucinationIssue.REPEATED_WORD)
        if any(phrase in normalized for phrase in self._junk_phrases):
            issues.append(HallucinationIssue.SUBTITLE_JUNK)
        if self._wrong_language(text, request.language):
            issues.append(HallucinationIssue.WRONG_LANGUAGE)
        if (
            request.duration_seconds > 0.0
            and len(tokens) / request.duration_seconds > self.settings.maximum_words_per_second
        ):
            issues.append(HallucinationIssue.IMPOSSIBLE_TEXT_RATE)
        if self._duplicates_committed(tokens, committed_text):
            issues.append(HallucinationIssue.DUPLICATE_COMMITTED_TEXT)

        unique = tuple(dict.fromkeys(issues))
        score = sum(self._weights[issue] for issue in unique)
        return GuardAssessment(score >= self.settings.suspicious_score, score, unique)

    @staticmethod
    def _has_repeated_ngram(tokens: list[str]) -> bool:
        for size in (3, 4, 5):
            if len(tokens) < size * 3:
                continue
            grams = Counter(
                tuple(tokens[index : index + size]) for index in range(len(tokens) - size + 1)
            )
            if any(count >= 3 for count in grams.values()):
                return True
        return False

    @staticmethod
    def _wrong_language(text: str, expected: str) -> bool:
        letters = [character for character in text if character.isalpha()]
        if len(letters) < 8:
            return False
        cyrillic = sum("CYRILLIC" in unicodedata.name(character, "") for character in letters)
        latin = sum("LATIN" in unicodedata.name(character, "") for character in letters)
        classified = cyrillic + latin
        if classified < 8:
            return False
        return (expected == "ru" and latin / classified > 0.8) or (
            expected == "en" and cyrillic / classified > 0.8
        )

    def _duplicates_committed(self, tokens: list[str], committed_text: str) -> bool:
        minimum = self.settings.duplicate_minimum_tokens
        if len(tokens) < minimum:
            return False
        committed = _tokens(_normalize(committed_text))
        if len(committed) < minimum:
            return False
        compare = min(len(tokens), 20)
        return committed[-compare:] == tokens[:compare] or committed[-len(tokens) :] == tokens

    def transcribe_guarded(
        self,
        backend: ASRBackend,
        request: TranscriptionRequest,
        *,
        committed_text: str = "",
        retry_backend: ASRBackend | None = None,
    ) -> GuardedTranscription:
        """Retry a suspicious chunk exactly once, optionally on a larger model."""

        first = backend.transcribe(request)
        first_assessment = self.assess(request, first, committed_text=committed_text)
        if not first_assessment.suspicious:
            return GuardedTranscription(first, first_assessment, first_assessment, False)
        retry_engine = retry_backend or backend
        try:
            retry_request = request.conservative_retry()
            if retry_backend is not None:
                # The larger model is already the conservative choice; forcing
                # a wide beam on CPU makes a rare cascade retry unbounded.
                retry_request = replace(retry_request, beam_size=1)
            retry = retry_engine.transcribe(retry_request)
            retry_assessment = self.assess(retry_request, retry, committed_text=committed_text)
        except Exception:
            failed_issues = tuple(
                dict.fromkeys((*first_assessment.issues, HallucinationIssue.RETRY_FAILED))
            )
            assessment = GuardAssessment(
                True,
                first_assessment.score + self._weights[HallucinationIssue.RETRY_FAILED],
                failed_issues,
            )
            marked = first.marked_uncertain(
                tuple(issue.value for issue in failed_issues), retry_count=1
            )
            return GuardedTranscription(
                marked,
                assessment,
                first_assessment,
                True,
                retry_engine.model_id,
            )

        if retry_assessment.score < first_assessment.score:
            chosen, assessment = retry, retry_assessment
        elif retry_assessment.score > first_assessment.score:
            chosen, assessment = first, first_assessment
        else:
            first_probability = first.average_word_probability or 0.0
            retry_probability = retry.average_word_probability or 0.0
            chosen, assessment = (
                (retry, retry_assessment)
                if retry_probability > first_probability
                else (first, first_assessment)
            )
        chosen = replace(
            chosen,
            processing_seconds=first.processing_seconds + retry.processing_seconds,
            retry_count=1,
        )
        if assessment.suspicious:
            chosen = chosen.marked_uncertain(
                tuple(issue.value for issue in assessment.issues), retry_count=1
            )
        return GuardedTranscription(
            chosen,
            assessment,
            first_assessment,
            True,
            retry_engine.model_id,
        )


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _tokens(text: str) -> list[str]:
    return [match.group(0) for match in _TOKEN.finditer(text)]
