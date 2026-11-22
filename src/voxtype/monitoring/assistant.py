"""Actionable local findings from confirmed runtime signals."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DiagnosticAction(StrEnum):
    MODELS = "models"
    AUDIO = "audio"
    HOTKEYS = "hotkeys"
    OUTBOX = "outbox"
    PERFORMANCE = "performance"


@dataclass(frozen=True, slots=True)
class DiagnosticSnapshot:
    microphone_available: bool | None
    microphone_permission: bool | None
    model_available: bool
    model_loaded: bool
    hotkey_registered: bool
    audio_overflow_count: int
    clipping_detected: bool
    last_asr_error: str | None
    last_delivery_status: str


@dataclass(frozen=True, slots=True)
class DiagnosticFinding:
    code: str
    detail: str
    action: DiagnosticAction
    label: str


def diagnostic_findings(state: DiagnosticSnapshot) -> tuple[DiagnosticFinding, ...]:
    findings: list[DiagnosticFinding] = []
    if not state.model_available:
        findings.append(
            DiagnosticFinding(
                "model_missing", "Нет доступной модели.", DiagnosticAction.MODELS, "Открыть модели"
            )
        )
    elif state.last_asr_error:
        findings.append(
            DiagnosticFinding(
                "asr_error",
                "Последнее распознавание завершилось ошибкой.",
                DiagnosticAction.MODELS,
                "Проверить модель",
            )
        )
    if state.microphone_available is False or state.microphone_permission is False:
        findings.append(
            DiagnosticFinding(
                "microphone",
                "Микрофон недоступен. Проверьте выбор устройства и разрешения Windows.",
                DiagnosticAction.AUDIO,
                "Открыть настройки звука",
            )
        )
    elif state.clipping_detected:
        findings.append(
            DiagnosticFinding(
                "clipping",
                "Сигнал перегружен. Уменьшите усиление и повторите проверку.",
                DiagnosticAction.AUDIO,
                "Проверить уровень",
            )
        )
    if not state.hotkey_registered:
        findings.append(
            DiagnosticFinding(
                "hotkey",
                "Комбинация запуска записи не зарегистрирована.",
                DiagnosticAction.HOTKEYS,
                "Открыть горячие клавиши",
            )
        )
    if state.last_delivery_status in {
        "queued_target_inactive",
        "copy_required",
        "failed",
        "submitted_unconfirmed",
    }:
        findings.append(
            DiagnosticFinding(
                "delivery",
                "Нужна проверка доставки текста.",
                DiagnosticAction.OUTBOX,
                "Открыть результаты вставки",
            )
        )
    if state.audio_overflow_count:
        findings.append(
            DiagnosticFinding(
                "overflow",
                "Зафиксировано переполнение аудиобуфера. Сравните режимы Eco и Balanced.",
                DiagnosticAction.PERFORMANCE,
                "Открыть настройки производительности",
            )
        )
    return tuple(findings)
