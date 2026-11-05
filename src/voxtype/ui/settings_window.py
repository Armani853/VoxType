"""Complete settings editor for VoxType.

Persistence is intentionally delegated: the dialog emits a typed dictionary
that the settings repository can write atomically.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from typing import Any, cast

from ._optional import PYSIDE6_AVAILABLE, require_pyside6

if PYSIDE6_AVAILABLE:
    from PySide6.QtCore import QSignalBlocker, Qt, Signal
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QHBoxLayout,
        QKeySequenceEdit,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSlider,
        QSpinBox,
        QStackedWidget,
        QVBoxLayout,
        QWidget,
    )

    from .theme import SPACING, state_color
    from .widgets import AudioLevelMeter, Card, rms_to_meter

    class SettingsWindow(QDialog):
        settings_submitted = Signal(object)
        audio_test_requested = Signal(str)
        model_download_requested = Signal(str)
        model_delete_requested = Signal(str)
        model_benchmark_requested = Signal(str)
        open_data_folder_requested = Signal()
        delete_local_data_requested = Signal()
        open_log_requested = Signal()
        export_diagnostics_requested = Signal()
        self_test_requested = Signal()
        show_versions_requested = Signal()
        onboarding_requested = Signal()

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setObjectName("settingsWindow")
            self.setWindowTitle("Настройки VoxType")
            self.resize(900, 650)
            self.setMinimumSize(760, 540)
            self._loading = False
            self._dirty = False
            self._non_safe_injection_mode = "auto"
            self._build_ui()
            self._connect_actions()
            self._connect_dirty_tracking()
            self.set_values({})

        def _build_ui(self) -> None:
            root = QVBoxLayout(self)
            root.setContentsMargins(
                SPACING.space_5,
                SPACING.space_5,
                SPACING.space_5,
                SPACING.space_4,
            )
            root.setSpacing(SPACING.space_4)
            header = QHBoxLayout()
            heading = QLabel("Настройки", self)
            heading.setProperty("heading", True)
            header.addWidget(heading)
            header.addStretch(1)
            self.onboarding_button = QPushButton("Повторить первый запуск", self)
            header.addWidget(self.onboarding_button)
            root.addLayout(header)

            content = QHBoxLayout()
            self.navigation = QListWidget(self)
            self.navigation.setObjectName("settingsNavigation")
            self.navigation.setFixedWidth(210)
            self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.pages = QStackedWidget(self)
            self.pages.setObjectName("settingsPages")
            content.addWidget(self.navigation)
            content.addWidget(self.pages, 1)
            root.addLayout(content, 1)

            self._build_general_page()
            self._build_dictation_page()
            self._build_audio_page()
            self._build_model_page()
            self._build_privacy_page()
            self._build_hotkeys_page()
            self._build_diagnostics_page()
            self.navigation.setCurrentRow(0)

            self.button_box = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Apply
                | QDialogButtonBox.StandardButton.Cancel,
                parent=self,
            )
            save_button = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
            apply_button = self.button_box.button(QDialogButtonBox.StandardButton.Apply)
            cancel_button = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
            save_button.setText("Сохранить")
            save_button.setProperty("compactPrimary", True)
            apply_button.setText("Применить")
            cancel_button.setText("Отмена")
            cancel_button.setProperty("flat", True)
            for button in (save_button, cancel_button):
                style = button.style()
                style.unpolish(button)
                style.polish(button)
            root.addWidget(self.button_box)

        def _page(self, title: str, description: str) -> tuple[QWidget, QFormLayout]:
            item = QListWidgetItem(title, self.navigation)
            item.setData(Qt.ItemDataRole.UserRole, self.pages.count())
            scroll = QScrollArea(self.pages)
            scroll.setWidgetResizable(True)
            canvas = QWidget(scroll)
            layout = QVBoxLayout(canvas)
            layout.setContentsMargins(
                SPACING.space_5,
                SPACING.space_2,
                SPACING.space_5,
                SPACING.space_4,
            )
            layout.setSpacing(SPACING.space_3)
            heading = QLabel(title, canvas)
            heading.setProperty("heading", True)
            hint = QLabel(description, canvas)
            hint.setProperty("muted", True)
            hint.setWordWrap(True)
            layout.addWidget(heading)
            layout.addWidget(hint)
            form_card = Card(canvas)
            form = QFormLayout()
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(24)
            form.setVerticalSpacing(SPACING.space_3)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            form_card.content_layout.addLayout(form)
            layout.addWidget(form_card)
            layout.addStretch(1)
            scroll.setWidget(canvas)
            self.pages.addWidget(scroll)
            return canvas, form

        @staticmethod
        def _combo(items: Iterable[tuple[str, object]], parent: QWidget) -> QComboBox:
            combo = QComboBox(parent)
            for label, data in items:
                combo.addItem(label, data)
            return combo

        def _build_general_page(self) -> None:
            page, form = self._page("Общие", "Поведение приложения и внешний вид.")
            self.autostart_check = QCheckBox("Запускать вместе с Windows", page)
            self.minimize_tray_check = QCheckBox("Сворачивать в системный трей", page)
            self.overlay_check = QCheckBox("Показывать индикатор диктовки", page)
            self.sounds_check = QCheckBox("Тихие звуки начала и окончания", page)
            self.theme_combo = self._combo(
                (("Как в системе", "system"), ("Светлая", "light"), ("Тёмная", "dark")),
                page,
            )
            self.interface_language_combo = self._combo(
                (("Русский", "ru"), ("English", "en")), page
            )
            form.addRow(self.autostart_check)
            form.addRow(self.minimize_tray_check)
            form.addRow(self.overlay_check)
            form.addRow(self.sounds_check)
            form.addRow("Тема", self.theme_combo)
            form.addRow("Язык интерфейса", self.interface_language_combo)

        def _build_dictation_page(self) -> None:
            page, form = self._page("Диктовка", "Язык, способ вставки и политика целевого окна.")
            self.default_language_combo = self._combo(
                (("RU — Русский", "ru"), ("EN — English", "en")), page
            )
            self.profile_combo = self._combo(
                (
                    ("Быстро", "fast"),
                    ("Баланс", "balanced"),
                    ("Максимальная точность", "maximum_accuracy"),
                ),
                page,
            )
            self.voice_commands_check = QCheckBox("Распознавать голосовые команды", page)
            self.vocabulary_edit = QPlainTextEdit(page)
            self.vocabulary_edit.setPlaceholderText(
                "По одному имени, термину или сокращению на строку (до 200)"
            )
            self.vocabulary_edit.setMaximumHeight(110)
            self.auto_insert_check = QCheckBox("Автоматически вставлять подтверждённый текст", page)
            self.copy_complete_check = QCheckBox(
                "Копировать весь текст в буфер после завершения", page
            )
            self.auto_hide_result_check = QCheckBox(
                "Автоматически скрывать результат после завершения", page
            )
            self.safe_clipboard_check = QCheckBox("Безопасный режим буфера обмена", page)
            self.target_policy_combo = self._combo(
                (("Закрепить окно при старте", "locked"), ("Следовать за фокусом", "follow_focus")),
                page,
            )
            self.insertion_delay_spin = QSpinBox(page)
            self.insertion_delay_spin.setRange(0, 5000)
            self.insertion_delay_spin.setSingleStep(50)
            self.insertion_delay_spin.setSuffix(" мс")
            self.spacing_check = QCheckBox("Добавлять пробел между фрагментами", page)
            self.capitalize_check = QCheckBox("Начинать предложение с заглавной буквы", page)
            form.addRow("Язык по умолчанию", self.default_language_combo)
            form.addRow("Профиль модели", self.profile_combo)
            form.addRow(self.voice_commands_check)
            form.addRow("Пользовательский словарь", self.vocabulary_edit)
            form.addRow(self.auto_insert_check)
            form.addRow(self.copy_complete_check)
            form.addRow(self.auto_hide_result_check)
            form.addRow(self.safe_clipboard_check)
            form.addRow("Целевое окно", self.target_policy_combo)
            form.addRow("Задержка перед вставкой", self.insertion_delay_spin)
            form.addRow(self.spacing_check)
            form.addRow(self.capitalize_check)

        def _build_audio_page(self) -> None:
            page, form = self._page("Аудио", "Микрофон и осторожная обработка сигнала.")
            self.audio_device_combo = QComboBox(page)
            self.audio_device_combo.setObjectName("audioDeviceCombo")
            self.audio_device_combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            self.audio_device_combo.setMinimumContentsLength(0)
            self.audio_device_combo.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
            )
            self.vad_slider = QSlider(Qt.Orientation.Horizontal, page)
            self.vad_slider.setRange(0, 100)
            self.vad_slider.setValue(50)
            self.vad_value_label = QLabel("50%", page)
            vad_row = QWidget(page)
            vad_layout = QHBoxLayout(vad_row)
            vad_layout.setContentsMargins(0, 0, 0, 0)
            vad_layout.addWidget(self.vad_slider, 1)
            vad_layout.addWidget(self.vad_value_label)
            self.preprocessing_combo = self._combo(
                (
                    ("Без обработки", "raw"),
                    ("Обычная", "normal"),
                    ("Шумная комната", "noisy_room"),
                ),
                page,
            )
            self.noise_suppression_check = QCheckBox("Умеренное шумоподавление", page)
            self.normalization_check = QCheckBox("Безопасная нормализация громкости", page)
            self.mic_meter = AudioLevelMeter(page)
            self.mic_status_label = QLabel("Нажмите «Тест микрофона»", page)
            self.mic_status_label.setProperty("muted", True)
            meter_row = QWidget(page)
            meter_layout = QVBoxLayout(meter_row)
            meter_layout.setContentsMargins(0, 0, 0, 0)
            meter_layout.addWidget(self.mic_meter)
            meter_layout.addWidget(self.mic_status_label)
            self.test_microphone_button = QPushButton("Тест микрофона", page)
            form.addRow("Устройство записи", self.audio_device_combo)
            form.addRow("Чувствительность речи", vad_row)
            form.addRow("Профиль обработки", self.preprocessing_combo)
            form.addRow(self.noise_suppression_check)
            form.addRow(self.normalization_check)
            form.addRow("Живой уровень", meter_row)
            form.addRow("", self.test_microphone_button)

        def _build_model_page(self) -> None:
            page, form = self._page(
                "Модель", "Установленные локальные модели и вычислительный профиль."
            )
            self.model_combo = QComboBox(page)
            model_actions = QWidget(page)
            actions = QHBoxLayout(model_actions)
            actions.setContentsMargins(0, 0, 0, 0)
            self.download_model_button = QPushButton("Скачать", model_actions)
            self.delete_model_button = QPushButton("Удалить", model_actions)
            self.benchmark_button = QPushButton("Benchmark", model_actions)
            actions.addWidget(self.download_model_button)
            actions.addWidget(self.delete_model_button)
            actions.addWidget(self.benchmark_button)
            actions.addStretch(1)
            self.model_progress = QProgressBar(page)
            self.model_progress.setRange(0, 100)
            self.model_progress.setValue(0)
            self.model_progress.setTextVisible(False)
            self.model_progress.hide()
            self.cpu_threads_spin = QSpinBox(page)
            self.cpu_threads_spin.setRange(1, 128)
            self.cpu_threads_spin.setValue(4)
            self.power_mode_combo = QComboBox(page)
            self.power_mode_combo.addItem("Экономия энергии", "energy")
            self.power_mode_combo.addItem("Баланс", "balanced")
            self.power_mode_combo.addItem("Максимальная производительность", "maximum")
            self.gpu_experimental_check = QCheckBox("Экспериментальный GPU backend", page)
            form.addRow("Активная модель", self.model_combo)
            form.addRow("Управление", model_actions)
            form.addRow("Загрузка", self.model_progress)
            form.addRow("Потоки CPU", self.cpu_threads_spin)
            form.addRow("Режим питания", self.power_mode_combo)
            form.addRow(self.gpu_experimental_check)

        def _build_privacy_page(self) -> None:
            page, form = self._page(
                "Приватность", "Все данные хранятся локально и не отправляются на серверы."
            )
            self.save_history_check = QCheckBox("Сохранять текст истории", page)
            self.save_audio_check = QCheckBox("Сохранять исходное аудио", page)
            self.retention_spin = QSpinBox(page)
            self.retention_spin.setRange(1, 3650)
            self.retention_spin.setSuffix(" дней")
            data_actions = QWidget(page)
            actions = QHBoxLayout(data_actions)
            actions.setContentsMargins(0, 0, 0, 0)
            self.open_data_button = QPushButton("Открыть папку данных", data_actions)
            self.delete_data_button = QPushButton("Удалить локальные данные", data_actions)
            self.delete_data_button.setProperty("danger", True)
            actions.addWidget(self.open_data_button)
            actions.addWidget(self.delete_data_button)
            actions.addStretch(1)
            form.addRow(self.save_history_check)
            form.addRow(self.save_audio_check)
            form.addRow("Срок хранения", self.retention_spin)
            form.addRow("Данные", data_actions)

        def _build_hotkeys_page(self) -> None:
            page, form = self._page(
                "Горячие клавиши", "Глобальные сочетания; конфликты проверяются при применении."
            )
            self.toggle_hotkey_edit = QKeySequenceEdit(page)
            self.pause_hotkey_edit = QKeySequenceEdit(page)
            self.language_hotkey_edit = QKeySequenceEdit(page)
            self.push_to_talk_hotkey_edit = QKeySequenceEdit(page)
            self.push_to_talk_hotkey_edit.setEnabled(False)
            self.push_to_talk_hotkey_edit.setToolTip(
                "Отключено: RegisterHotKey не сообщает надёжное отпускание клавиши"
            )
            form.addRow("Начать / остановить", self.toggle_hotkey_edit)
            form.addRow("Пауза / продолжить", self.pause_hotkey_edit)
            form.addRow("Переключить RU / EN", self.language_hotkey_edit)
            form.addRow("Push-to-talk", self.push_to_talk_hotkey_edit)

        def _build_diagnostics_page(self) -> None:
            page, form = self._page(
                "Диагностика", "Логи не содержат продиктованный текст или PCM-аудио."
            )
            self.open_log_button = QPushButton("Открыть технический лог", page)
            self.export_diagnostics_button = QPushButton("Экспортировать отчёт", page)
            self.self_test_button = QPushButton("Выполнить self-test", page)
            self.show_versions_button = QPushButton("Показать версии библиотек", page)
            self.diagnostics_status_label = QLabel("Проверки ещё не запускались", page)
            self.diagnostics_status_label.setProperty("muted", True)
            form.addRow(self.open_log_button)
            form.addRow(self.export_diagnostics_button)
            form.addRow(self.self_test_button)
            form.addRow(self.show_versions_button)
            form.addRow("Состояние", self.diagnostics_status_label)

        def _connect_actions(self) -> None:
            self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
            self.button_box.accepted.connect(self._save_and_accept)
            self.button_box.rejected.connect(self.reject)
            self.button_box.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(
                self._submit
            )
            self.vad_slider.valueChanged.connect(
                lambda value: self.vad_value_label.setText(f"{value}%")
            )
            self.test_microphone_button.clicked.connect(
                lambda: self.audio_test_requested.emit(
                    str(self.audio_device_combo.currentData() or "")
                )
            )
            self.download_model_button.clicked.connect(
                lambda: self.model_download_requested.emit(
                    str(self.model_combo.currentData() or "")
                )
            )
            self.delete_model_button.clicked.connect(self._confirm_model_delete)
            self.benchmark_button.clicked.connect(
                lambda: self.model_benchmark_requested.emit(
                    str(self.model_combo.currentData() or "")
                )
            )
            self.open_data_button.clicked.connect(self.open_data_folder_requested)
            self.delete_data_button.clicked.connect(self._confirm_delete_data)
            self.open_log_button.clicked.connect(self.open_log_requested)
            self.export_diagnostics_button.clicked.connect(self.export_diagnostics_requested)
            self.self_test_button.clicked.connect(self.self_test_requested)
            self.show_versions_button.clicked.connect(self.show_versions_requested)
            self.onboarding_button.clicked.connect(self.onboarding_requested)

        def _connect_dirty_tracking(self) -> None:
            checks = (
                self.autostart_check,
                self.minimize_tray_check,
                self.overlay_check,
                self.sounds_check,
                self.voice_commands_check,
                self.auto_insert_check,
                self.copy_complete_check,
                self.auto_hide_result_check,
                self.safe_clipboard_check,
                self.spacing_check,
                self.capitalize_check,
                self.noise_suppression_check,
                self.normalization_check,
                self.gpu_experimental_check,
                self.save_history_check,
                self.save_audio_check,
            )
            for check in checks:
                check.toggled.connect(self._mark_dirty)
            combos = (
                self.theme_combo,
                self.interface_language_combo,
                self.default_language_combo,
                self.profile_combo,
                self.target_policy_combo,
                self.audio_device_combo,
                self.preprocessing_combo,
                self.model_combo,
                self.power_mode_combo,
            )
            for combo in combos:
                combo.currentIndexChanged.connect(self._mark_dirty)
            for spin in (
                self.insertion_delay_spin,
                self.cpu_threads_spin,
                self.retention_spin,
                self.vad_slider,
            ):
                spin.valueChanged.connect(self._mark_dirty)
            for key_edit in (
                self.toggle_hotkey_edit,
                self.pause_hotkey_edit,
                self.language_hotkey_edit,
                self.push_to_talk_hotkey_edit,
            ):
                key_edit.keySequenceChanged.connect(self._mark_dirty)
            self.vocabulary_edit.textChanged.connect(self._mark_dirty)

        def _mark_dirty(self, *_args: object) -> None:
            if not self._loading:
                self._set_dirty(True)

        def _set_dirty(self, dirty: bool) -> None:
            self._dirty = dirty
            apply_button = self.button_box.button(QDialogButtonBox.StandardButton.Apply)
            apply_button.setEnabled(dirty)

        def _save_and_accept(self) -> None:
            self._submit()
            self.accept()

        def _submit(self) -> None:
            self.settings_submitted.emit(self.values())
            self._set_dirty(False)

        def values(self) -> dict[str, object]:
            portable = QKeySequence.SequenceFormat.PortableText
            return {
                "general": {
                    "start_with_windows": self.autostart_check.isChecked(),
                    "minimize_to_tray": self.minimize_tray_check.isChecked(),
                    "show_overlay": self.overlay_check.isChecked(),
                    "play_sounds": self.sounds_check.isChecked(),
                    "theme": self.theme_combo.currentData(),
                    "ui_language": self.interface_language_combo.currentData(),
                },
                "dictation": {
                    "language": self.default_language_combo.currentData(),
                    "model_profile": self.profile_combo.currentData(),
                    "voice_commands": self.voice_commands_check.isChecked(),
                    "custom_vocabulary": [
                        line.strip()
                        for line in self.vocabulary_edit.toPlainText().splitlines()
                        if line.strip()
                    ][:200],
                    "auto_insert": self.auto_insert_check.isChecked(),
                    "copy_full_text_on_complete": self.copy_complete_check.isChecked(),
                    "auto_hide_completed_result": self.auto_hide_result_check.isChecked(),
                    "injection_mode": (
                        "safe_clipboard"
                        if self.safe_clipboard_check.isChecked()
                        else self._non_safe_injection_mode
                    ),
                    "target_mode": self.target_policy_combo.currentData(),
                    "insertion_delay_ms": self.insertion_delay_spin.value(),
                    "add_fragment_space": self.spacing_check.isChecked(),
                    "capitalize_sentences": self.capitalize_check.isChecked(),
                },
                "audio": {
                    "device_id": self.audio_device_combo.currentData(),
                    "vad_sensitivity": self.vad_slider.value() / 100.0,
                    "preprocessing_profile": self.preprocessing_combo.currentData(),
                    "noise_suppression": self.noise_suppression_check.isChecked(),
                    "normalization": self.normalization_check.isChecked(),
                },
                "model": {
                    "installed_model": self.model_combo.currentData(),
                    "cpu_threads": self.cpu_threads_spin.value(),
                    "power_mode": self.power_mode_combo.currentData(),
                    "experimental_gpu": self.gpu_experimental_check.isChecked(),
                },
                "privacy": {
                    "save_history": self.save_history_check.isChecked(),
                    "save_audio": self.save_audio_check.isChecked(),
                    "retention_days": self.retention_spin.value(),
                },
                "hotkeys": {
                    "toggle": self.toggle_hotkey_edit.keySequence().toString(portable),
                    "pause": self.pause_hotkey_edit.keySequence().toString(portable),
                    "language": self.language_hotkey_edit.keySequence().toString(portable),
                    "push_to_talk": self.push_to_talk_hotkey_edit.keySequence().toString(portable),
                },
            }

        def set_values(self, values: Mapping[str, object] | object) -> None:
            data = _as_mapping(values)
            self._loading = True
            try:
                general = _section(data, "general")
                dictation = _section(data, "dictation")
                audio = _section(data, "audio")
                model = _section(data, "model")
                privacy = _section(data, "privacy")
                hotkeys = _section(data, "hotkeys")
                self.autostart_check.setChecked(
                    bool(_setting(general, "start_with_windows", "autostart", default=False))
                )
                self.minimize_tray_check.setChecked(
                    bool(_setting(general, "minimize_to_tray", default=True))
                )
                self.overlay_check.setChecked(bool(_setting(general, "show_overlay", default=True)))
                self.sounds_check.setChecked(
                    bool(_setting(general, "play_sounds", "sounds", default=False))
                )
                _select_data(self.theme_combo, _setting(general, "theme", default="system"))
                _select_data(
                    self.interface_language_combo,
                    _setting(general, "ui_language", "interface_language", default="ru"),
                )
                _select_data(
                    self.default_language_combo,
                    _setting(dictation, "language", "default_language", default="ru"),
                )
                _select_data(
                    self.profile_combo,
                    _setting(dictation, "model_profile", default="balanced"),
                )
                self.voice_commands_check.setChecked(
                    bool(_setting(dictation, "voice_commands", default=False))
                )
                vocabulary = _setting(dictation, "custom_vocabulary", default=[])
                entries = vocabulary if isinstance(vocabulary, list) else []
                self.vocabulary_edit.setPlainText("\n".join(str(item) for item in entries[:200]))
                self.auto_insert_check.setChecked(
                    bool(_setting(dictation, "auto_insert", default=True))
                )
                self.copy_complete_check.setChecked(
                    bool(_setting(dictation, "copy_full_text_on_complete", default=True))
                )
                self.auto_hide_result_check.setChecked(
                    bool(_setting(dictation, "auto_hide_completed_result", default=False))
                )
                injection_mode = _setting(
                    dictation, "injection_mode", "safe_clipboard", default="safe_clipboard"
                )
                primitive_injection = str(_primitive(injection_mode))
                if primitive_injection in {"clipboard_paste", "unicode_input"}:
                    self._non_safe_injection_mode = primitive_injection
                self.safe_clipboard_check.setChecked(
                    bool(injection_mode is True or primitive_injection == "safe_clipboard")
                )
                _select_data(
                    self.target_policy_combo,
                    _setting(dictation, "target_mode", "target_policy", default="locked"),
                )
                self.insertion_delay_spin.setValue(
                    int(str(_setting(dictation, "insertion_delay_ms", default=350)))
                )
                self.spacing_check.setChecked(
                    bool(
                        _setting(
                            dictation,
                            "add_fragment_space",
                            "spacing_between_fragments",
                            default=True,
                        )
                    )
                )
                self.capitalize_check.setChecked(
                    bool(_setting(dictation, "capitalize_sentences", default=True))
                )
                _select_data(self.audio_device_combo, _setting(audio, "device_id", default=""))
                sensitivity = float(str(_setting(audio, "vad_sensitivity", default=0.5)))
                self.vad_slider.setValue(round(min(1.0, max(0.0, sensitivity)) * 100))
                _select_data(
                    self.preprocessing_combo,
                    _setting(audio, "preprocessing_profile", default="normal"),
                )
                self.noise_suppression_check.setChecked(
                    bool(_setting(audio, "noise_suppression", default=False))
                )
                self.normalization_check.setChecked(
                    bool(_setting(audio, "normalization", default=True))
                )
                _select_data(
                    self.model_combo,
                    _setting(model, "installed_model", "name", default=""),
                )
                self.cpu_threads_spin.setValue(int(str(_setting(model, "cpu_threads", default=4))))
                _select_data(
                    self.power_mode_combo,
                    _setting(model, "power_mode", default="balanced"),
                )
                self.gpu_experimental_check.setChecked(
                    bool(_setting(model, "experimental_gpu", default=False))
                )
                self.save_history_check.setChecked(
                    bool(_setting(privacy, "save_history", default=True))
                )
                self.save_audio_check.setChecked(
                    bool(_setting(privacy, "save_audio", default=False))
                )
                self.retention_spin.setValue(
                    int(str(_setting(privacy, "retention_days", default=30)))
                )
                self.toggle_hotkey_edit.setKeySequence(
                    QKeySequence(str(_setting(hotkeys, "toggle", default="Ctrl+Alt+Space")))
                )
                self.pause_hotkey_edit.setKeySequence(
                    QKeySequence(str(_setting(hotkeys, "pause", default="Ctrl+Alt+P")))
                )
                self.language_hotkey_edit.setKeySequence(
                    QKeySequence(str(_setting(hotkeys, "language", default="Ctrl+Alt+L")))
                )
                self.push_to_talk_hotkey_edit.setKeySequence(
                    QKeySequence(
                        str(
                            _setting(
                                hotkeys,
                                "push_to_talk",
                                default="Ctrl+Alt+Shift+Space",
                            )
                        )
                    )
                )
            finally:
                self._loading = False
                self._set_dirty(False)

        def set_audio_devices(
            self,
            devices: Iterable[str | tuple[str, object] | object],
            selected_id: object | None = None,
        ) -> None:
            previous = (
                selected_id if selected_id is not None else self.audio_device_combo.currentData()
            )
            blocker = QSignalBlocker(self.audio_device_combo)
            self.audio_device_combo.clear()
            for device in devices:
                if isinstance(device, str):
                    self.audio_device_combo.addItem(device, device)
                elif isinstance(device, tuple):
                    self.audio_device_combo.addItem(str(device[0]), device[1])
                else:
                    name = getattr(device, "name", str(device))
                    identifier = getattr(device, "id", getattr(device, "device_id", name))
                    self.audio_device_combo.addItem(str(name), identifier)
            for index in range(self.audio_device_combo.count()):
                self.audio_device_combo.setItemData(
                    index,
                    self.audio_device_combo.itemText(index),
                    Qt.ItemDataRole.ToolTipRole,
                )
            del blocker
            _select_data(self.audio_device_combo, previous)

        def set_models(
            self,
            models: Iterable[str | tuple[str, object] | object],
            selected_name: object | None = None,
        ) -> None:
            previous = (
                selected_name if selected_name is not None else self.model_combo.currentData()
            )
            blocker = QSignalBlocker(self.model_combo)
            self.model_combo.clear()
            for model in models:
                if isinstance(model, str):
                    self.model_combo.addItem(model, model)
                elif isinstance(model, tuple):
                    self.model_combo.addItem(str(model[0]), model[1])
                else:
                    name = getattr(model, "name", str(model))
                    identifier = getattr(model, "id", name)
                    self.model_combo.addItem(str(name), identifier)
            for index in range(self.model_combo.count()):
                self.model_combo.setItemData(
                    index,
                    self.model_combo.itemText(index),
                    Qt.ItemDataRole.ToolTipRole,
                )
            del blocker
            _select_data(self.model_combo, previous)

        def update_microphone_level(
            self, level: float, clipping_percentage: float = 0.0, message: str = ""
        ) -> None:
            self.mic_meter.set_level(rms_to_meter(level), clipping_percentage)
            if message:
                self.mic_status_label.setText(message)
            elif clipping_percentage > 0.1 or level >= 0.98:
                self.mic_status_label.setText("Есть клиппинг — говорите тише")
            elif level < 0.02:
                self.mic_status_label.setText("Слишком тихо")
            else:
                self.mic_status_label.setText("Уровень в норме")

        def set_model_download_progress(self, percent: int, message: str = "") -> None:
            value = min(100, max(0, int(percent)))
            self.model_progress.setVisible(value < 100)
            self.model_progress.setValue(value)
            self.model_progress.setToolTip(message)

        def set_diagnostics_status(self, message: str, ok: bool | None = None) -> None:
            self.diagnostics_status_label.setText(message)
            if ok is not None:
                self.diagnostics_status_label.setStyleSheet(
                    f"color: {state_color('success' if ok else 'error')};"
                )

        def _confirm_model_delete(self) -> None:
            model_id = str(self.model_combo.currentData() or "")
            if not model_id:
                return
            answer = QMessageBox.question(
                self,
                "Удалить модель?",
                f"Локальная модель «{self.model_combo.currentText()}» будет удалена.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer is QMessageBox.StandardButton.Yes:
                self.model_delete_requested.emit(model_id)

        def _confirm_delete_data(self) -> None:
            answer = QMessageBox.warning(
                self,
                "Удалить локальные данные?",
                "Будут удалены история, журналы сессий и сохранённое аудио. "
                "Это действие нельзя отменить.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer is QMessageBox.StandardButton.Yes:
                self.delete_local_data_requested.emit()

    def _as_mapping(value: object) -> Mapping[str, object]:
        if isinstance(value, Mapping):
            return cast(Mapping[str, object], value)
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            converted = to_dict()
            if isinstance(converted, Mapping):
                return cast(Mapping[str, object], converted)
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            converted = model_dump()
            if isinstance(converted, Mapping):
                return cast(Mapping[str, object], converted)
        if is_dataclass(value) and not isinstance(value, type):
            return cast(Mapping[str, object], asdict(cast(Any, value)))
        try:
            return cast(Mapping[str, object], vars(value))
        except TypeError:
            return {}

    def _section(data: Mapping[str, object], name: str) -> Mapping[str, object]:
        return _as_mapping(data.get(name, {}))

    def _setting(section: Mapping[str, object], *names: str, default: object) -> object:
        for name in names:
            if name in section:
                return section[name]
        return default

    def _primitive(value: object) -> object:
        return getattr(value, "value", value)

    def _select_data(combo: QComboBox, value: object) -> None:
        primitive = _primitive(value)
        primitive = {
            "accurate": "maximum_accuracy",
            "max_accuracy": "maximum_accuracy",
            "noisy": "noisy_room",
        }.get(str(primitive), primitive)
        index = combo.findData(primitive)
        if index < 0 and primitive is not None:
            index = combo.findText(str(primitive))
        if index >= 0:
            combo.setCurrentIndex(index)

else:

    class SettingsWindow:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs
            require_pyside6()
