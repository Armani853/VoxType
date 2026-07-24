"""Eight-step first-run wizard for local dictation setup."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ._optional import PYSIDE6_AVAILABLE, require_pyside6

if PYSIDE6_AVAILABLE:
    from PySide6.QtCore import QSignalBlocker, Qt, Signal
    from PySide6.QtGui import QFont, QTextCursor
    from PySide6.QtWidgets import (
        QComboBox,
        QLabel,
        QLineEdit,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QVBoxLayout,
        QWidget,
        QWizard,
        QWizardPage,
    )

    from .theme import create_app_icon
    from .widgets import AudioLevelMeter, Card

    class _ManagedPage(QWizardPage):
        def __init__(self, title: str, subtitle: str, *, initially_complete: bool = False) -> None:
            super().__init__()
            self.setTitle(title)
            self.setSubTitle(subtitle)
            self._complete = initially_complete

        def isComplete(self) -> bool:
            return self._complete

        def set_complete(self, complete: bool) -> None:
            complete = bool(complete)
            if complete != self._complete:
                self._complete = complete
                self.completeChanged.emit()

    class OnboardingWizard(QWizard):
        microphone_refresh_requested = Signal()
        microphone_selected = Signal(str)
        calibration_requested = Signal(str)
        hardware_probe_requested = Signal()
        model_download_requested = Signal(str)
        phrase_test_requested = Signal(str)
        insertion_test_requested = Signal(str)
        setup_completed = Signal(object)

        RU_TEST_PHRASE = "Сегодня я настраиваю локальную голосовую диктовку."
        EN_TEST_PHRASE = "Today I am setting up local voice dictation."
        INSERTION_TEST_TEXT = "Проверка вставки VoxType: русский и English."

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setObjectName("onboardingWizard")
            self.setWindowTitle("Первый запуск VoxType")
            self.setWindowIcon(create_app_icon())
            self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
            self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
            self.setOption(QWizard.WizardOption.NoCancelButtonOnLastPage, True)
            self.resize(760, 580)
            self.setMinimumSize(680, 520)
            self.setButtonText(QWizard.WizardButton.NextButton, "Далее")
            self.setButtonText(QWizard.WizardButton.BackButton, "Назад")
            self.setButtonText(QWizard.WizardButton.CancelButton, "Отмена")
            self.setButtonText(QWizard.WizardButton.FinishButton, "Начать работу")
            self._hardware_probe_emitted = False
            self._build_pages()
            self.currentIdChanged.connect(self._page_changed)

        def _build_pages(self) -> None:
            self.intro_page = QWizardPage()
            self.intro_page.setTitle("VoxType работает локально")
            self.intro_page.setSubTitle("Без аккаунта, облачного API и отправки аудио.")
            intro_layout = QVBoxLayout(self.intro_page)
            hero = Card(self.intro_page)
            hero_title = QLabel("Ваш голос остаётся на этом компьютере", hero)
            hero_title.setProperty("heading", True)
            hero_text = QLabel(
                "VoxType записывает микрофон только во время активной диктовки и "
                "распознаёт речь локальной моделью. Для первой загрузки модели "
                "понадобится интернет; после этого диктовка работает офлайн.",
                hero,
            )
            hero_text.setWordWrap(True)
            privacy_text = QLabel(
                "Технические логи не содержат продиктованный текст или PCM-аудио.", hero
            )
            privacy_text.setProperty("muted", True)
            privacy_text.setWordWrap(True)
            hero.content_layout.addWidget(hero_title)
            hero.content_layout.addWidget(hero_text)
            hero.content_layout.addWidget(privacy_text)
            intro_layout.addWidget(hero)
            intro_layout.addStretch(1)
            self.addPage(self.intro_page)

            self.microphone_page = _ManagedPage(
                "Выберите микрофон",
                "Говорите обычным голосом — индикатор должен уверенно двигаться.",
            )
            mic_layout = QVBoxLayout(self.microphone_page)
            mic_card = Card(self.microphone_page)
            self.microphone_combo = QComboBox(mic_card)
            self.microphone_combo.setObjectName("onboardingMicrophoneCombo")
            self.live_meter = AudioLevelMeter(mic_card)
            self.live_meter.setObjectName("onboardingMicrophoneMeter")
            self.microphone_status = QLabel("Ищу устройства записи…", mic_card)
            self.microphone_status.setProperty("muted", True)
            self.microphone_status.setWordWrap(True)
            self.refresh_microphones_button = QPushButton("Обновить список", mic_card)
            self.refresh_microphones_button.clicked.connect(self.microphone_refresh_requested)
            mic_card.content_layout.addWidget(QLabel("Устройство записи", mic_card))
            mic_card.content_layout.addWidget(self.microphone_combo)
            mic_card.content_layout.addWidget(self.live_meter)
            mic_card.content_layout.addWidget(self.microphone_status)
            mic_card.content_layout.addWidget(self.refresh_microphones_button)
            mic_layout.addWidget(mic_card)
            mic_layout.addStretch(1)
            self.microphone_combo.currentIndexChanged.connect(self._microphone_changed)
            self.addPage(self.microphone_page)

            self.calibration_page = _ManagedPage(
                "Калибровка тишины и речи",
                "Сначала сохраняйте тишину, затем произнесите несколько слов обычным голосом.",
            )
            calibration_layout = QVBoxLayout(self.calibration_page)
            calibration_card = Card(self.calibration_page)
            self.calibration_status = QLabel("Калибровка ещё не запускалась", calibration_card)
            self.calibration_status.setWordWrap(True)
            self.calibration_progress = QProgressBar(calibration_card)
            self.calibration_progress.setRange(0, 100)
            self.calibration_progress.setValue(0)
            self.calibration_button = QPushButton("Начать калибровку", calibration_card)
            self.calibration_button.setProperty("primary", True)
            self.calibration_button.clicked.connect(self._request_calibration)
            calibration_card.content_layout.addWidget(self.calibration_status)
            calibration_card.content_layout.addWidget(self.calibration_progress)
            calibration_card.content_layout.addWidget(self.calibration_button)
            calibration_layout.addWidget(calibration_card)
            calibration_layout.addStretch(1)
            self.addPage(self.calibration_page)

            self.hardware_page = _ManagedPage(
                "Проверка компьютера",
                "Подберём профиль, который оставит ресурсы браузеру и редактору.",
            )
            hardware_layout = QVBoxLayout(self.hardware_page)
            hardware_card = Card(self.hardware_page)
            self.hardware_status = QLabel("Ожидаю диагностику…", hardware_card)
            self.hardware_status.setWordWrap(True)
            self.recommended_profile_label = QLabel("", hardware_card)
            self.recommended_profile_label.setProperty("sectionHeading", True)
            self.retry_hardware_button = QPushButton("Проверить снова", hardware_card)
            self.retry_hardware_button.hide()
            self.retry_hardware_button.clicked.connect(self._request_hardware_probe)
            hardware_card.content_layout.addWidget(self.hardware_status)
            hardware_card.content_layout.addWidget(self.recommended_profile_label)
            hardware_card.content_layout.addWidget(self.retry_hardware_button)
            hardware_layout.addWidget(hardware_card)
            hardware_layout.addStretch(1)
            self.hardware_page_id = self.addPage(self.hardware_page)

            self.model_page = _ManagedPage(
                "Локальная модель",
                "Выберите предложенный профиль и дождитесь проверки или загрузки модели.",
            )
            model_layout = QVBoxLayout(self.model_page)
            model_card = Card(self.model_page)
            self.model_combo = QComboBox(model_card)
            self.model_combo.addItem("Быстро — меньше нагрузка", "fast")
            self.model_combo.addItem("Баланс — выше нагрузка", "balanced")
            self.model_combo.addItem("Максимальная точность", "accurate")
            self.model_combo.setCurrentIndex(0)
            self.model_status = QLabel("Модель не проверена", model_card)
            self.model_status.setProperty("muted", True)
            self.model_progress = QProgressBar(model_card)
            self.model_progress.setRange(0, 100)
            self.model_progress.setValue(0)
            self.download_button = QPushButton("Проверить и скачать", model_card)
            self.download_button.setProperty("primary", True)
            self.download_button.clicked.connect(
                lambda: self.model_download_requested.emit(str(self.model_combo.currentData()))
            )
            model_card.content_layout.addWidget(self.model_combo)
            model_card.content_layout.addWidget(self.model_status)
            model_card.content_layout.addWidget(self.model_progress)
            model_card.content_layout.addWidget(self.download_button)
            model_layout.addWidget(model_card)
            model_layout.addStretch(1)
            self.addPage(self.model_page)

            self.phrase_page = _ManagedPage(
                "Проверка русского и английского",
                "Произнесите обе фразы. Результаты никуда не отправляются.",
            )
            phrase_layout = QVBoxLayout(self.phrase_page)
            self._phrase_ok = {"ru": False, "en": False}
            ru_card, self.ru_phrase_result, ru_button = self._phrase_card(
                self.phrase_page, "RU", self.RU_TEST_PHRASE
            )
            en_card, self.en_phrase_result, en_button = self._phrase_card(
                self.phrase_page, "EN", self.EN_TEST_PHRASE
            )
            ru_button.clicked.connect(lambda: self.phrase_test_requested.emit("ru"))
            en_button.clicked.connect(lambda: self.phrase_test_requested.emit("en"))
            phrase_layout.addWidget(ru_card)
            phrase_layout.addWidget(en_card)
            phrase_layout.addStretch(1)
            self.addPage(self.phrase_page)

            self.insertion_page = _ManagedPage(
                "Проверка вставки",
                "Поставьте курсор в поле и запустите тест. Это специальное поле разрешено только мастеру.",
            )
            insertion_layout = QVBoxLayout(self.insertion_page)
            insertion_card = Card(self.insertion_page)
            self.insertion_edit = QPlainTextEdit(insertion_card)
            self.insertion_edit.setObjectName("onboardingInsertionField")
            self.insertion_edit.setPlaceholderText("Тестовый текст появится здесь")
            self.insertion_status = QLabel("Тест ещё не выполнен", insertion_card)
            self.insertion_status.setProperty("muted", True)
            self.insertion_button = QPushButton("Проверить вставку", insertion_card)
            self.insertion_button.clicked.connect(self._request_insertion_test)
            self.insertion_edit.textChanged.connect(self._check_insertion)
            insertion_card.content_layout.addWidget(self.insertion_edit)
            insertion_card.content_layout.addWidget(self.insertion_status)
            insertion_card.content_layout.addWidget(self.insertion_button)
            insertion_layout.addWidget(insertion_card)
            insertion_layout.addStretch(1)
            self.addPage(self.insertion_page)

            self.finish_page = QWizardPage()
            self.finish_page.setTitle("Всё готово")
            self.finish_page.setSubTitle("Диктовку можно включать из любого обычного приложения.")
            finish_layout = QVBoxLayout(self.finish_page)
            finish_card = Card(self.finish_page)
            shortcut = QLabel("Ctrl + Alt + Space", finish_card)
            shortcut.setAlignment(Qt.AlignmentFlag.AlignCenter)
            shortcut_font = QFont(shortcut.font())
            shortcut_font.setPointSize(19)
            shortcut_font.setBold(True)
            shortcut.setFont(shortcut_font)
            self.hotkey_edit = QLineEdit("Ctrl+Alt+Space", finish_card)
            self.hotkey_edit.setObjectName("onboardingHotkey")
            self.hotkey_edit.setToolTip("Сочетание можно позже изменить в настройках")
            finish_hint = QLabel(
                "Нажмите сочетание, говорите, затем нажмите его ещё раз. "
                "RU/EN переключается через Ctrl+Alt+L.",
                finish_card,
            )
            finish_hint.setWordWrap(True)
            finish_card.content_layout.addWidget(shortcut)
            finish_card.content_layout.addWidget(QLabel("Горячая клавиша", finish_card))
            finish_card.content_layout.addWidget(self.hotkey_edit)
            finish_card.content_layout.addWidget(finish_hint)
            finish_layout.addWidget(finish_card)
            finish_layout.addStretch(1)
            self.addPage(self.finish_page)

        def _phrase_card(
            self, parent: QWidget, language: str, phrase: str
        ) -> tuple[Card, QLabel, QPushButton]:
            card = Card(parent, spacing=6)
            title = QLabel(f"{language}: {phrase}", card)
            title.setWordWrap(True)
            result = QLabel("Не проверено", card)
            result.setProperty("muted", True)
            result.setWordWrap(True)
            button = QPushButton(f"Произнести {language}", card)
            button.setObjectName(f"test{language}PhraseButton")
            card.content_layout.addWidget(title)
            card.content_layout.addWidget(result)
            card.content_layout.addWidget(button)
            return card, result, button

        def _microphone_changed(self, _index: int) -> None:
            identifier = str(self.microphone_combo.currentData() or "")
            self.microphone_page.set_complete(bool(identifier))
            if identifier:
                self.microphone_selected.emit(identifier)

        def set_microphones(
            self,
            microphones: Iterable[str | tuple[str, object] | object],
            selected_id: object | None = None,
        ) -> None:
            blocker = QSignalBlocker(self.microphone_combo)
            self.microphone_combo.clear()
            for microphone in microphones:
                if isinstance(microphone, str):
                    self.microphone_combo.addItem(microphone, microphone)
                elif isinstance(microphone, tuple):
                    self.microphone_combo.addItem(str(microphone[0]), microphone[1])
                else:
                    name = getattr(microphone, "name", str(microphone))
                    identifier = getattr(microphone, "id", getattr(microphone, "device_id", name))
                    self.microphone_combo.addItem(str(name), identifier)
            if selected_id is not None:
                selected_index = self.microphone_combo.findData(selected_id)
                if selected_index >= 0:
                    self.microphone_combo.setCurrentIndex(selected_index)
            del blocker
            identifier = str(self.microphone_combo.currentData() or "")
            self.microphone_page.set_complete(bool(identifier))
            self.microphone_status.setText(
                "Говорите обычным голосом" if identifier else "Устройства записи не найдены"
            )
            if identifier:
                self.microphone_selected.emit(identifier)

        def update_microphone_level(
            self, level: float, clipping_percentage: float = 0.0, message: str = ""
        ) -> None:
            self.live_meter.set_level(level, clipping_percentage)
            if message:
                text = message
            elif clipping_percentage > 0.1 or level >= 0.98:
                text = "Есть клиппинг — уменьшите громкость или говорите тише"
            elif level < 0.02:
                text = "Слишком тихо — подвиньтесь ближе к микрофону"
            else:
                text = "Уровень в норме"
            self.microphone_status.setText(text)

        def _request_calibration(self) -> None:
            identifier = str(self.microphone_combo.currentData() or "")
            if not identifier:
                self.calibration_status.setText("Сначала выберите микрофон")
                return
            self.calibration_button.setEnabled(False)
            self.calibration_status.setText("Слушаю тишину и речь…")
            self.calibration_progress.setRange(0, 0)
            self.calibration_requested.emit(identifier)

        def set_calibration_result(self, ok: bool, message: str) -> None:
            self.calibration_progress.setRange(0, 100)
            self.calibration_progress.setValue(100 if ok else 0)
            self.calibration_status.setText(message)
            self.calibration_status.setStyleSheet(f"color: {'#1A9B67' if ok else '#D34A55'};")
            self.calibration_button.setEnabled(True)
            self.calibration_button.setText("Повторить калибровку")
            self.calibration_page.set_complete(ok)

        def _page_changed(self, page_id: int) -> None:
            if page_id == self.hardware_page_id and not self._hardware_probe_emitted:
                self._request_hardware_probe()

        def _request_hardware_probe(self) -> None:
            self._hardware_probe_emitted = True
            self.retry_hardware_button.hide()
            self.hardware_status.setText("Проверяю CPU, RAM и доступные ускорители…")
            self.hardware_probe_requested.emit()

        def set_hardware_recommendation(
            self, profile: str, summary: str, *, ok: bool = True
        ) -> None:
            profile_key = profile if profile in {"fast", "balanced", "accurate"} else "balanced"
            labels = {
                "fast": "Рекомендуемый профиль: Быстро",
                "balanced": "Рекомендуемый профиль: Баланс",
                "accurate": "Рекомендуемый профиль: Максимальная точность",
            }
            self.hardware_status.setText(summary)
            self.recommended_profile_label.setText(labels[profile_key])
            self.retry_hardware_button.setVisible(not ok)
            index = self.model_combo.findData(profile_key)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
            self.hardware_page.set_complete(ok)

        def set_model_progress(self, percent: int, message: str = "") -> None:
            value = min(100, max(0, int(percent)))
            self.model_progress.setRange(0, 100)
            self.model_progress.setValue(value)
            self.model_status.setText(message or f"Загружено {value}%")
            self.download_button.setEnabled(value >= 100 or value == 0)

        def set_model_ready(self, ready: bool, message: str) -> None:
            self.model_progress.setRange(0, 100)
            self.model_progress.setValue(100 if ready else 0)
            self.model_status.setText(message)
            self.model_status.setStyleSheet(f"color: {'#1A9B67' if ready else '#D34A55'};")
            self.download_button.setEnabled(True)
            self.download_button.setText("Проверить снова" if ready else "Повторить")
            self.model_page.set_complete(ready)

        def set_phrase_result(self, language: str, recognized_text: str, ok: bool) -> None:
            normalized = "en" if language.lower().startswith("en") else "ru"
            label = self.en_phrase_result if normalized == "en" else self.ru_phrase_result
            label.setText(
                ("Распознано: " if ok else "Не удалось распознать: ")
                + (recognized_text or "пустой результат")
            )
            label.setStyleSheet(f"color: {'#1A9B67' if ok else '#D34A55'};")
            self._phrase_ok[normalized] = bool(ok)
            self.phrase_page.set_complete(all(self._phrase_ok.values()))

        def _request_insertion_test(self) -> None:
            self.insertion_edit.clear()
            self.insertion_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            cursor = self.insertion_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.insertion_edit.setTextCursor(cursor)
            self.insertion_status.setText("Ожидаю вставку…")
            self.insertion_test_requested.emit(self.INSERTION_TEST_TEXT)

        def set_insertion_test_text(self, text: str) -> None:
            """Allow a controller test adapter to fill the dedicated target field."""

            self.insertion_edit.setPlainText(text)
            self._check_insertion()

        def _check_insertion(self) -> None:
            actual = self.insertion_edit.toPlainText().strip()
            ok = actual == self.INSERTION_TEST_TEXT
            self.insertion_page.set_complete(ok)
            if ok:
                self.insertion_status.setText("Вставка Unicode работает")
                self.insertion_status.setStyleSheet("color: #1A9B67;")
            elif actual:
                self.insertion_status.setText("Текст отличается — повторите проверку")
                self.insertion_status.setStyleSheet("color: #D34A55;")

        def setup_values(self) -> dict[str, object]:
            return {
                "audio_device_id": self.microphone_combo.currentData(),
                "model_profile": self.model_combo.currentData(),
                "toggle_hotkey": self.hotkey_edit.text().strip() or "Ctrl+Alt+Space",
                "onboarding_completed": True,
            }

        def accept(self) -> None:
            self.setup_completed.emit(self.setup_values())
            super().accept()

else:

    class OnboardingWizard:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs
            require_pyside6()
