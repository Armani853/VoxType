"""Human-facing failure messages without implementation or traceback details."""

from __future__ import annotations

from voxtype.core.feedback import FAILURES, FailureCode

_FAILURES = {
    "session_recovery": (
        "Не удалось восстановить незавершённую диктовку. "
        "Уже распознанный текст и данные восстановления сохранены. "
        "Откройте Диагностику и повторите восстановление."
    ),
    "diagnostics": (
        "Не удалось завершить диагностику. Данные VoxType не изменены. "
        "Закройте другие проверки и нажмите «Повторить»."
    ),
    "model_install": (
        "Не удалось установить модель. Ранее установленные модели не изменены. "
        "Проверьте интернет и свободное место, затем повторите."
    ),
    "benchmark": (
        "Не удалось завершить проверку скорости. Модель и настройки не изменены. "
        "Закройте ресурсоёмкие программы и повторите."
    ),
    "microphone_test": (
        "Не удалось проверить микрофон. Настройки и история не изменены. "
        "Проверьте подключение микрофона и доступ к нему, затем повторите."
    ),
    "phrase_test": (
        "Не удалось распознать тестовую фразу. Данные диктовки не изменены. "
        "Проверьте микрофон и установленную модель, затем повторите."
    ),
}


def failure_message(operation: str) -> str:
    if operation in FailureCode._value2member_map_:
        return FAILURES[FailureCode(operation)].text()
    try:
        return _FAILURES[operation]
    except KeyError as exc:
        raise ValueError(f"unknown user failure operation: {operation}") from exc
