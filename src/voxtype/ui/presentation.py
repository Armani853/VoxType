"""Translate controller facts into a shared, text-free dictation presentation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import log10

from .models import AppStatus, snapshot_from


class CaptureState(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPING = "stopping"
    FAILED = "failed"


class RecognitionState(StrEnum):
    IDLE = "idle"
    WAITING = "waiting_for_audio"
    STREAMING = "streaming"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"


class TargetState(StrEnum):
    NOT_SELECTED = "not_selected"
    VALID = "valid"
    LOST = "lost"
    UNSAFE = "unsafe"


class DeliveryState(StrEnum):
    BUFFER_ONLY = "buffer_only"
    PENDING = "pending"
    SUBMITTED_UNCONFIRMED = "submitted_unconfirmed"
    CONFIRMED = "confirmed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MicrophonePresentation:
    name: str
    available: bool | None
    capture_open: bool
    signal_dbfs: float | None
    signal_quality: str
    error_code: str
    generation: int


@dataclass(frozen=True, slots=True)
class DictationPresentation:
    session_id: str | None
    capture: CaptureState
    recognition: RecognitionState
    target: TargetState
    delivery: DeliveryState
    microphone: MicrophonePresentation
    primary: str
    tone: str
    delivery_label: str
    language: str
    model_name: str
    duration_seconds: float
    has_confirmed: bool
    has_provisional: bool


def provisional_tail(source: object) -> str:
    live = getattr(source, "live_transcript", None)
    if live is None or getattr(source, "live_final", None) is not None:
        return ""
    return " ".join(
        (*[segment.text for segment in live.stable_segments], live.unstable_text)
    ).strip()


class DictationPresentationResolver:
    @staticmethod
    def resolve(source: object, locale: str = "ru") -> DictationPresentation:
        state = snapshot_from(source)
        ru = locale == "ru"
        status = state.state
        ready = state.readiness
        reason = ready.reason if ready is not None else ""
        recording = status in {
            AppStatus.LISTENING,
            AppStatus.SPEECH_DETECTED,
            AppStatus.TRANSCRIBING,
            AppStatus.PAUSED_TARGET_LOST,
        }
        capture_open = getattr(source, "capture_open", recording)
        confirmed = bool(
            state.last_committed_text
            or state.full_transcript
            or getattr(source, "live_committed_length", 0)
        )
        live = getattr(source, "live_transcript", None)
        provisional = bool(
            live is not None
            and (live.stable_segments or live.unstable_text)
            and getattr(source, "live_final", None) is None
        )
        failure = "" if capture_open else getattr(source, "microphone_failure", "")
        available = getattr(source, "microphone_available", None)
        if reason == "microphone_missing" and not capture_open:
            failure = failure or "microphone_missing"
        quality = getattr(source, "audio_quality", None) if capture_open else None
        dbfs: float | None = None
        signal = ""
        if capture_open and quality is not None:
            dbfs = float(quality.rms_dbfs)
            signal = (
                "clipping"
                if quality.clipping_detected
                else "quiet"
                if quality.low_signal or dbfs < -60
                else "normal"
            )
        elif capture_open and state.input_rms > 0:
            dbfs = 20 * log10(state.input_rms)
            signal = (
                "clipping"
                if state.clipping_percentage > 0.1
                else "quiet"
                if dbfs < -60
                else "normal"
            )
        mic = MicrophonePresentation(
            state.microphone_name,
            True if capture_open else available,
            capture_open,
            dbfs,
            signal,
            failure,
            getattr(source, "capture_generation", 0),
        )
        capture, recognition = CaptureState.IDLE, RecognitionState.IDLE
        tone = "primary"
        if status in {AppStatus.ERROR_RECOVERABLE, AppStatus.ERROR_FATAL}:
            capture, recognition, tone = CaptureState.FAILED, RecognitionState.FAILED, "error"
            primary = (
                ("Диктовка прервана" if ru else "Dictation interrupted")
                if not failure
                else ("Ошибка микрофона" if ru else "Microphone error")
            )
        elif recording:
            capture = CaptureState.RECORDING
            recognition = (
                RecognitionState.STREAMING if confirmed or provisional else RecognitionState.WAITING
            )
            primary, tone = ("Запись идёт" if ru else "Recording"), "recording"
        elif status is AppStatus.PAUSED:
            capture, recognition = CaptureState.PAUSED, RecognitionState.WAITING
            primary, tone = ("Пауза" if ru else "Paused"), "warning"
        elif status is AppStatus.FINISHING:
            capture, recognition = CaptureState.STOPPING, RecognitionState.FINALIZING
            primary = "Завершаем диктовку" if ru else "Finishing dictation"
        elif status in {AppStatus.STARTING, AppStatus.MODEL_LOADING}:
            capture, recognition = CaptureState.STARTING, RecognitionState.WAITING
            primary = "Запуск диктовки" if ru else "Starting dictation"
        elif ready is not None and reason in {
            "model_missing",
            "microphone_missing",
            "device_recovery",
        }:
            primary, tone = ready.message, "warning"
        elif ready is not None and reason not in {"ready", "inserting"}:
            primary = ready.message
        elif status is AppStatus.COMPLETED:
            recognition = RecognitionState.COMPLETED
            primary, tone = (
                (("Диктовка завершена" if ru else "Dictation complete"), "success")
                if confirmed
                else (("Текст не распознан" if ru else "No text recognized"), "warning")
            )
        else:
            primary = "Готов к диктовке" if ru else "Ready to dictate"

        automatic = getattr(source, "automatic_delivery", False)
        target_name = getattr(source, "target_application", "") or state.target_title
        if getattr(source, "target_unsafe", False):
            target = TargetState.UNSAFE
        elif (
            getattr(source, "target_lost", False)
            or status is AppStatus.PAUSED_TARGET_LOST
            or state.delivery_status == "queued_target_inactive"
        ):
            target = TargetState.LOST
        else:
            target = TargetState.VALID if target_name else TargetState.NOT_SELECTED

        delivered = state.delivery_status
        if delivered == "delivered":
            delivery = DeliveryState.CONFIRMED
            label = "Вставка подтверждена" if ru else "Insertion confirmed"
        elif delivered in {"submitted_unconfirmed", "unknown"}:
            delivery = DeliveryState.SUBMITTED_UNCONFIRMED
            label = "Проверьте вставку в целевом поле" if ru else "Check the target field"
        elif target in {TargetState.LOST, TargetState.UNSAFE} and (
            automatic
            or getattr(source, "pending_insertion", False)
            or delivered == "queued_target_inactive"
        ):
            delivery = DeliveryState.FAILED
            label = (
                "Вставка приостановлена · текст в VoxType"
                if ru
                else "Insertion paused · text in VoxType"
            )
        elif delivered in {"failed", "copy_required"}:
            delivery = DeliveryState.FAILED
            label = "Проверьте текст перед вставкой" if ru else "Review text before inserting"
        elif automatic and target is TargetState.VALID:
            delivery = DeliveryState.PENDING
            label = f"{'Вставка' if ru else 'Insert'} · {target_name}"
        else:
            delivery = DeliveryState.BUFFER_ONLY
            label = "Текст остаётся в VoxType" if ru else "Text stays in VoxType"
        return DictationPresentation(
            getattr(source, "live_session_id", None),
            capture,
            recognition,
            target,
            delivery,
            mic,
            primary,
            tone,
            label,
            state.language.value.upper(),
            state.model_name,
            state.session_elapsed_s,
            confirmed,
            provisional,
        )
