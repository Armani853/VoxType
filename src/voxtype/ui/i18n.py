"""Small built-in translation catalogue for UI chrome and status text."""

from __future__ import annotations

from collections.abc import Mapping

SUPPORTED_INTERFACE_LANGUAGES = ("ru", "en")

_TEXT: Mapping[str, Mapping[str, str]] = {
    "ru": {
        "app_name": "VoxType",
        "local_badge": "ЛОКАЛЬНО",
        "ready": "Готово",
        "starting": "Запуск…",
        "model_loading": "Загрузка модели…",
        "listening": "Слушаю",
        "speech": "Слышу речь",
        "transcribing": "Распознаю",
        "target_lost": "Целевое окно неактивно",
        "finishing": "Завершаю",
        "paused": "Пауза",
        "recoverable_error": "Нужна помощь",
        "fatal_error": "Ошибка",
        "shutting_down": "Завершение…",
        "start": "Начать диктовку",
        "stop": "Остановить",
        "hotkey_hint": "Ctrl+Alt+Space",
        "settings": "Настройки",
        "history": "История",
        "diagnostics": "Диагностика",
        "microphone": "Микрофон",
        "model": "Модель",
        "duration": "Длительность",
        "backlog": "Очередь аудио",
        "target": "Цель",
        "last_fragment": "Последний фрагмент",
        "no_fragment": "Распознанный текст появится здесь",
        "no_target": "Окно будет зафиксировано при старте",
        "processing_ok": "Обработка идёт в реальном времени",
    },
    "en": {
        "app_name": "VoxType",
        "local_badge": "LOCAL",
        "ready": "Ready",
        "starting": "Starting…",
        "model_loading": "Loading model…",
        "listening": "Listening",
        "speech": "Speech detected",
        "transcribing": "Transcribing",
        "target_lost": "Target window is inactive",
        "finishing": "Finishing",
        "paused": "Paused",
        "recoverable_error": "Action needed",
        "fatal_error": "Error",
        "shutting_down": "Shutting down…",
        "start": "Start dictation",
        "stop": "Stop",
        "hotkey_hint": "Ctrl+Alt+Space",
        "settings": "Settings",
        "history": "History",
        "diagnostics": "Diagnostics",
        "microphone": "Microphone",
        "model": "Model",
        "duration": "Duration",
        "backlog": "Audio queue",
        "target": "Target",
        "last_fragment": "Last fragment",
        "no_fragment": "Recognized text will appear here",
        "no_target": "A window will be locked when dictation starts",
        "processing_ok": "Processing in real time",
    },
}


def ui_text(key: str, locale: str = "ru") -> str:
    language = locale if locale in SUPPORTED_INTERFACE_LANGUAGES else "ru"
    return _TEXT[language].get(key, _TEXT["ru"].get(key, key))
