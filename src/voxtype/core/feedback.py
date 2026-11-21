"""User-facing guidance for actionable failures."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FailureCode(StrEnum):
    MICROPHONE_MISSING = "microphone_missing"
    MICROPHONE_BUSY = "microphone_busy"
    MODEL_MISSING = "model_missing"
    MODEL_LOAD_FAILED = "model_load_failed"
    MODEL_DOWNLOAD_FAILED = "model_download_failed"
    AUDIO_CAPTURE_FAILED = "audio_capture_failed"
    ASR_FAILED = "asr_failed"
    TARGET_LOST = "target_lost"
    INSERTION_FAILED = "insertion_failed"
    HOTKEY_CONFLICT = "hotkey_conflict"
    CONFIGURATION_ERROR = "configuration_error"


@dataclass(frozen=True, slots=True)
class FailureMessage:
    title: str
    description: str
    recovery: str

    def text(self) -> str:
        return f"{self.title}. {self.description} {self.recovery}"


FAILURES = {
    FailureCode.MICROPHONE_MISSING: FailureMessage("Микрофон недоступен", "Выбранное устройство не найдено.", "Подключите его или выберите системный микрофон в настройках."),
    FailureCode.MICROPHONE_BUSY: FailureMessage("Микрофон занят", "Устройство занято другим приложением.", "Освободите микрофон или выберите другой."),
    FailureCode.MODEL_MISSING: FailureMessage("Требуется модель", "Для выбранного режима нет локальных файлов.", "Установите модель на странице «Модели»."),
    FailureCode.MODEL_LOAD_FAILED: FailureMessage("Не удалось загрузить модель", "Локальная модель не готова к работе.", "Выберите другую установленную модель или повторите загрузку."),
    FailureCode.MODEL_DOWNLOAD_FAILED: FailureMessage("Не удалось установить модель", "Установка прервалась.", "Проверьте подключение и свободное место, затем повторите."),
    FailureCode.AUDIO_CAPTURE_FAILED: FailureMessage("Ошибка записи", "Не удалось получить звук без потерь.", "Проверьте устройство и разрешения Windows, затем повторите."),
    FailureCode.ASR_FAILED: FailureMessage("Ошибка распознавания", "Обработка речи остановилась.", "Закройте ресурсоёмкие программы и запустите диктовку снова."),
    FailureCode.TARGET_LOST: FailureMessage("Целевое окно недоступно", "Распознанный текст остался в результате.", "Выберите цель заново и нажмите «Повторить вставку» или скопируйте текст."),
    FailureCode.INSERTION_FAILED: FailureMessage("Не удалось отправить текст", "Распознанный текст остался доступен.", "Проверьте целевое поле перед ручным повтором или скопируйте результат."),
    FailureCode.HOTKEY_CONFLICT: FailureMessage("Сочетание недоступно", "Комбинация неверна, занята Windows или назначена другому действию.", "Выберите другое сочетание."),
    FailureCode.CONFIGURATION_ERROR: FailureMessage("Не удалось сохранить настройки", "Не удалось записать конфигурацию.", "Проверьте свободное место и доступ к каталогу VoxType."),
}
