"""Complete settings editor for VoxType.

Persistence is intentionally delegated: the dialog emits a typed dictionary
that the settings repository can write atomically.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from typing import Any, ClassVar, cast

from ._optional import PYSIDE6_AVAILABLE, require_pyside6

_KEEP_SELECTION = object()

if PYSIDE6_AVAILABLE:
    from PySide6.QtCore import QSettings, QSignalBlocker, Qt, Signal
    from PySide6.QtGui import QHideEvent, QKeySequence, QStandardItemModel
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QHBoxLayout,
        QKeySequenceEdit,
        QLabel,
        QLayout,
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

    from voxtype.audio.quality import AudioQualitySnapshot
    from voxtype.constants import DEFAULT_HOTKEY, DEFAULT_LANGUAGE_HOTKEY, DEFAULT_PAUSE_HOTKEY

    from .motion import AnimationCoordinator, MotionPreference
    from .obsidian import (
        VoxButton,
        VoxComboBox,
        VoxDisclosure,
        VoxEmptyState,
        VoxMessageBox,
        VoxSectionHeader,
        VoxSettingRow,
        VoxToggle,
        confirm_delete,
    )
    from .theme import SPACING, state_color
    from .widgets import AudioLevelMeter, Card, rms_to_meter

    class _SettingsForm(QFormLayout):
        _details: ClassVar[dict[str, str]] = {
            "Язык по умолчанию": "Применяется со следующей записи.",
            "Целевое окно": "Определяет, куда отправлять готовый текст.",
            "Задержка перед вставкой": "Время, которое получает целевое приложение для чтения буфера.",
            "Активная модель": "Обрабатывает речь на устройстве.",
            "Потоки CPU": "Количество потоков локального распознавания.",
            "Срок хранения": "Срок хранения записей локальной истории.",
        }

        def addRow(self, *args: object) -> None:
            if len(args) == 2:
                label, field = args
                if isinstance(label, str) and isinstance(field, QWidget):
                    super().addRow(
                        VoxSettingRow(
                            label, field, detail=field.toolTip() or self._details.get(label, "")
                        )
                    )
                    return
                if isinstance(label, (str, QWidget)) and isinstance(field, (QWidget, QLayout)):
                    super().addRow(label, field)
                    return
            elif len(args) == 1 and isinstance(row := args[0], (QWidget, QLayout)):
                super().addRow(row)
                return
            raise TypeError("Invalid settings form row")

    class SettingsWindow(QDialog):
        settings_submitted = Signal(object)
        audio_test_requested = Signal(str)
        model_download_requested = Signal(str)
        model_delete_requested = Signal(str)
        model_benchmark_requested = Signal(str)
        open_data_folder_requested = Signal()
        delete_local_data_requested = Signal()
        recovery_center_requested = Signal()
        backup_requested = Signal()
        restore_backup_requested = Signal()
        export_personal_requested = Signal()
        import_personal_requested = Signal()
        export_archive_requested = Signal(str)
        updates_requested = Signal()
        input_compatibility_requested = Signal()
        audio_view_hidden = Signal()
        open_log_requested = Signal()
        export_diagnostics_requested = Signal()
        self_test_requested = Signal()
        show_versions_requested = Signal()
        local_api_credentials_requested = Signal()
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
            self.navigation.setFixedWidth(154)
            self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.pages = QStackedWidget(self)
            self.pages.setObjectName("settingsPages")
            content.addWidget(self.navigation)
            content.addWidget(self.pages, 1)
            root.addLayout(content, 1)

            self._build_general_page()
            self._build_audio_page()
            self._build_dictation_page()
            self._build_model_page()
            self._build_hotkeys_page()
            self._build_appearance_page()
            self._build_privacy_page()
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
                SPACING.space_4,
                SPACING.space_2,
                SPACING.space_4,
                SPACING.space_4,
            )
            layout.setSpacing(SPACING.space_3)
            heading = VoxSectionHeader(title, canvas, role="page")
            hint = QLabel(description, canvas)
            hint.setProperty("muted", True)
            hint.setWordWrap(True)
            layout.addWidget(heading)
            layout.addWidget(hint)
            form = _SettingsForm()
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(24)
            form.setVerticalSpacing(SPACING.space_2)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            layout.addLayout(form)
            layout.addStretch(1)
            scroll.setWidget(canvas)
            self.pages.addWidget(scroll)
            return canvas, form

        @staticmethod
        def _combo(items: Iterable[tuple[str, object]], parent: QWidget) -> QComboBox:
            combo = VoxComboBox(parent)
            for label, data in items:
                combo.addItem(label, data)
            return combo

        def _build_general_page(self) -> None:
            page, form = self._page("Общие", "Запуск приложения и поведение окна.")
            self.autostart_check = VoxToggle("Запускать вместе с Windows", page)
            self.autostart_check.setToolTip(
                "Горячие клавиши работают, пока VoxType запущен. "
                "Включите автозапуск, чтобы они были доступны после входа в Windows."
            )
            self.minimize_tray_check = VoxToggle("Сворачивать в системный трей", page)
            self.overlay_check = VoxToggle("Показывать индикатор диктовки", page)
            self.sounds_check = VoxToggle("Тихие звуки начала и окончания", page)
            form.addRow(self.autostart_check)
            form.addRow(self.minimize_tray_check)
            form.addRow(self.overlay_check)
            form.addRow(self.sounds_check)

        def _build_appearance_page(self) -> None:
            page, form = self._page("Внешний вид", "Цветовая схема и язык интерфейса.")
            self.theme_combo = self._combo(
                (("Как в системе", "system"), ("Светлая", "light"), ("Тёмная", "dark")),
                page,
            )
            self.interface_language_combo = self._combo(
                (("Русский", "ru"), ("English", "en")), page
            )
            form.addRow("Тема", self.theme_combo)
            form.addRow("Язык интерфейса", self.interface_language_combo)
            self.motion_combo = self._combo(
                (
                    ("Как в системе", MotionPreference.SYSTEM.value),
                    ("Обычная", MotionPreference.FULL.value),
                    ("Уменьшенная", MotionPreference.REDUCED.value),
                ),
                page,
            )
            self.motion_combo.setToolTip("Управляет декоративными переходами и волной записи.")
            form.addRow("Анимация", self.motion_combo)

        def set_view_preferences(self, store: QSettings) -> None:
            _select_data(self.motion_combo, MotionPreference.load(store).value)

            def update(_index: int) -> None:
                value = self.motion_combo.currentData()
                store.setValue("ui/motionPreference", value)
                application = QApplication.instance()
                if application is not None:
                    application.setProperty("voxtypeMotionPreference", value)
                AnimationCoordinator.instance().preference_changed()

            self.motion_combo.currentIndexChanged.connect(update)

        def _build_dictation_page(self) -> None:
            page, form = self._page(
                "Распознавание", "Язык, способ вставки и политика целевого окна."
            )
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
            self.live_preview_check = QCheckBox("Предварительный текст во время диктовки", page)
            self.live_preview_check.setToolTip(
                "Текст будет отправлен в целевое окно после остановки."
            )
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
            self.text_transformations_check = QCheckBox(
                "Применять личный словарь и правила замены", page
            )
            form.addRow("Язык по умолчанию", self.default_language_combo)
            form.addRow("Профиль модели", self.profile_combo)
            form.addRow(self.voice_commands_check)
            form.addRow("Пользовательский словарь", self.vocabulary_edit)
            form.addRow(self.text_transformations_check)
            _, form = self._page(
                "Вставка", "Целевое окно, буфер обмена и форматирование фрагментов."
            )
            self.dictation_mode_combo = QComboBox(page)
            for label, mode in (
                ("По нажатию", "toggle"),
                ("При удержании", "push_to_talk"),
                ("Останавливать по тишине", "auto_stop"),
                ("Вручную", "manual"),
            ):
                self.dictation_mode_combo.addItem(label, mode)
            form.addRow("Режим записи", self.dictation_mode_combo)
            self.auto_stop_enabled = QCheckBox("Включить остановку по тишине в этом режиме", page)
            form.addRow(self.auto_stop_enabled)
            self.auto_stop_limits = []
            for label, low, high, value in (
                ("Минимум речи", 100, 10000, 400),
                ("Тишина после речи", 200, 15000, 1500),
                ("Максимальная длительность", 1000, 3600000, 120000),
            ):
                limit = QSpinBox(page)
                limit.setRange(low, high)
                limit.setValue(value)
                limit.setSuffix(" мс")
                self.auto_stop_limits.append(limit)
                form.addRow(label, limit)
            self.safe_terminal_check = QCheckBox("Предпочитать безопасный ввод в терминалах", page)
            form.addRow(self.safe_terminal_check)
            self.caret_overlay_check = QCheckBox("Размещать индикатор рядом с курсором", page)
            form.addRow(self.caret_overlay_check)
            compatibility = QPushButton("Совместимость приложений…", page)
            compatibility.clicked.connect(lambda: self.input_compatibility_requested.emit())
            form.addRow(compatibility)
            form.addRow(self.live_preview_check)
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
            self._audio_page = page
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
            self.preprocessing_mode_combo = self._combo(
                (
                    ("Прежний профиль", "legacy"),
                    ("Без обработки", "off"),
                    ("Базовая обработка", "basic"),
                    ("Улучшенная — недоступна", "enhanced"),
                ),
                page,
            )
            model = self.preprocessing_mode_combo.model()
            if isinstance(model, QStandardItemModel) and (enhanced := model.item(3)) is not None:
                enhanced.setEnabled(False)
                enhanced.setToolTip("Требуется совместимый модуль шумоподавления.")
            self.noise_suppression_check = QCheckBox("Умеренное шумоподавление", page)
            self.startup_pre_roll_check = QCheckBox("Первые звуки во время подготовки модели", page)
            self.startup_pre_roll_check.setToolTip(
                "Микрофон открывается после команды записи. Последние 350 мс подготовки хранятся в памяти. "
                "При сохранении аудио функция отключена."
            )
            self.normalization_check = QCheckBox("Безопасная нормализация громкости", page)
            self.mic_meter = AudioLevelMeter(page)
            self.mic_status_label = QLabel("Нажмите «Тест микрофона»", page)
            self.mic_status_label.setProperty("muted", True)
            meter_row = QWidget(page)
            meter_layout = QVBoxLayout(meter_row)
            meter_layout.setContentsMargins(0, 0, 0, 0)
            meter_layout.addWidget(self.mic_meter)
            meter_layout.addWidget(self.mic_status_label)
            self.audio_quality_label = QLabel("Показатели появятся во время диктовки", page)
            self.audio_quality_label.setWordWrap(True)
            self._last_audio_quality_view: tuple[AudioQualitySnapshot | None, str, str] | None = (
                None
            )
            self.audio_quality_label.setProperty("muted", True)
            meter_layout.addWidget(self.audio_quality_label)
            self.test_microphone_button = QPushButton("Тест микрофона", page)
            form.addRow("Устройство записи", self.audio_device_combo)
            form.addRow("Чувствительность речи", vad_row)
            form.addRow("Предобработка", self.preprocessing_mode_combo)
            form.addRow("Унаследованный профиль", self.preprocessing_combo)
            form.addRow(self.noise_suppression_check)
            form.addRow(self.normalization_check)
            form.addRow(self.startup_pre_roll_check)
            form.addRow("Живой уровень", meter_row)
            form.addRow("", self.test_microphone_button)
            self.preprocessing_mode_combo.currentIndexChanged.connect(
                self._update_audio_processing_options
            )
            self._update_audio_processing_options()

        def _update_audio_processing_options(self) -> None:
            legacy = self.preprocessing_mode_combo.currentData() == "legacy"
            self.preprocessing_combo.setEnabled(legacy)
            self.noise_suppression_check.setEnabled(legacy)
            self.normalization_check.setEnabled(legacy)

        def hideEvent(self, event: QHideEvent) -> None:
            self.audio_view_hidden.emit()
            super().hideEvent(event)

        def update_audio_quality(
            self, snapshot: AudioQualitySnapshot | None, device_name: str
        ) -> None:
            if not self.isVisible() or self.pages.currentWidget() is not self._audio_page:
                return
            mode = {
                "off": "без предобработки",
                "basic": "базовая предобработка",
                "enhanced": "базовая предобработка (расширенная недоступна)",
                "legacy": "унаследованный профиль",
            }.get(self.preprocessing_mode_combo.currentData(), "унаследованный профиль")
            view = (snapshot, device_name, mode)
            if self._last_audio_quality_view == view:
                return
            self._last_audio_quality_view = view
            if snapshot is None:
                self.audio_quality_label.setText(f"{device_name} · {mode}")
                return
            noise = {
                "quiet": "тихий",
                "moderate": "умеренный",
                "noisy": "шумный",
                "unknown": "ещё не оценён",
            }
            warnings = []
            if snapshot.clipping_detected:
                warnings.append("Клиппинг: уменьшите усиление микрофона.")
            if snapshot.low_signal:
                warnings.append("Речь тихая. Попробуйте говорить ближе к микрофону.")
            description = (
                f"{device_name} · {snapshot.sample_rate} Гц · {snapshot.rms_dbfs:.0f} dBFS · "
                f"пик {snapshot.peak_dbfs:.0f} dBFS · фон {noise[snapshot.noise_category.value]} · {mode}"
            )
            text = " ".join((description, *warnings))
            if self.audio_quality_label.text() != text:
                self.audio_quality_label.setText(text)
            self.mic_meter.set_level(
                rms_to_meter(10 ** (snapshot.rms_dbfs / 20)), snapshot.clipping_ratio * 100
            )

        def _build_model_page(self) -> None:
            self._models_index = self.pages.count()
            page, form = self._page(
                "Модели", "Установленные локальные модели и вычислительный профиль."
            )
            self.model_combo = QComboBox(page)
            self.model_cards = QVBoxLayout()
            self._model_widgets: dict[str, tuple[Card, QLabel, QLabel, VoxButton]] = {}
            self._models_installed: dict[str, bool] = {}
            self._model_empty = VoxEmptyState(
                "files",
                "Нет установленных моделей",
                "Для офлайн-диктовки установите одну из моделей ниже.",
                page,
            )
            self._model_empty.hide()
            self.model_cards.addWidget(self._model_empty)
            form.addRow(self.model_cards)
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
            self.cpu_threads_spin.setRange(0, 128)
            self.cpu_threads_spin.setSpecialValueText("Авто · по профилю")
            self.power_mode_combo = QComboBox(page)
            self.power_mode_combo.addItem("Экономия энергии", "energy")
            self.power_mode_combo.addItem("Баланс", "balanced")
            self.power_mode_combo.addItem("Максимальная производительность", "maximum")
            self.decoding_preset_combo = QComboBox(page)
            self.decoding_preset_combo.addItem("Быстро", "fast")
            self.decoding_preset_combo.addItem("Стандартное", "balanced")
            self.decoding_preset_combo.addItem("Точнее (выше нагрузка)", "accurate")
            self.memory_policy_combo = QComboBox(page)
            self.memory_policy_combo.addItem("Оставлять модель в памяти", "keep_loaded")
            self.memory_policy_combo.addItem("Выгружать после простоя", "unload_after_idle")
            self.memory_policy_combo.addItem("Выгружать по запросу", "explicit")
            self.idle_unload_spin = QSpinBox(page)
            self.idle_unload_spin.setRange(1, 120)
            self.idle_unload_spin.setSuffix(" мин")
            self.release_models_button = QPushButton("Выгрузить модель сейчас", page)
            self.memory_policy_combo.currentIndexChanged.connect(
                lambda: self.idle_unload_spin.setEnabled(
                    self.memory_policy_combo.currentData() == "unload_after_idle"
                )
            )
            self.idle_unload_spin.setEnabled(False)
            self.gpu_experimental_check = QCheckBox("Экспериментальный GPU backend", page)
            form.addRow("Активная модель", self.model_combo)
            form.addRow("Управление", model_actions)
            form.addRow("Загрузка", self.model_progress)
            advanced = QWidget(page)
            controls = _SettingsForm(advanced)
            controls.setContentsMargins(0, 0, 0, 0)
            controls.addRow("Потоки CPU", self.cpu_threads_spin)
            controls.addRow("Режим питания", self.power_mode_combo)
            controls.addRow("Декодирование", self.decoding_preset_combo)
            controls.addRow("Модель в памяти", self.memory_policy_combo)
            controls.addRow("Выгрузка после", self.idle_unload_spin)
            controls.addRow(self.release_models_button)
            controls.addRow(self.gpu_experimental_check)
            form.addRow(VoxDisclosure("Расширенные параметры модели", advanced, page))

        def open_general(self) -> None:
            self.navigation.setCurrentRow(0)

        def open_models(self) -> None:
            self.navigation.setCurrentRow(self._models_index)

        def open_audio(self) -> None:
            self.navigation.setCurrentRow(self.pages.indexOf(self._audio_page))

        def open_hotkeys(self) -> None:
            self.navigation.setCurrentRow(self.pages.indexOf(self._hotkeys_page))

        def confirm_navigation(self) -> bool:
            if not self._dirty:
                return True
            message = VoxMessageBox(self)
            message.setWindowTitle("Сохранить изменения?")
            message.setText("В настройках есть неприменённые изменения.")
            message.setStandardButtons(
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel
            )
            message.setDefaultButton(QMessageBox.StandardButton.Cancel)
            choice = message.exec()
            if choice == QMessageBox.StandardButton.Save:
                self._submit()
                return True
            return choice == QMessageBox.StandardButton.Discard

        def set_model_cards(
            self, items: list[tuple[str, str, str, bool, bool, bool, bool, str]]
        ) -> None:
            if items == getattr(self, "_rendered_model_cards", None):
                return
            self._rendered_model_cards = items
            visible = {item[0] for item in items}
            self._model_empty.setVisible(bool(items) and not any(item[3] for item in items))
            for identifier in tuple(self._model_widgets):
                if identifier not in visible:
                    card, *_fields = self._model_widgets.pop(identifier)
                    self.model_cards.removeWidget(card)
                    card.hide()
                    card.deleteLater()
                    self._models_installed.pop(identifier, None)
            for (
                identifier,
                description,
                size,
                installed,
                active,
                install_allowed,
                usable,
                state,
            ) in items:
                fields = self._model_widgets.get(identifier)
                if fields is None:
                    card = Card(self)
                    card.content_layout.addWidget(VoxSectionHeader(identifier, card))
                    detail = QLabel(card)
                    detail.setWordWrap(True)
                    card.content_layout.addWidget(detail)
                    label = QLabel(card)
                    label.setProperty("muted", True)
                    card.content_layout.addWidget(label)
                    action = VoxButton("", card)
                    action.clicked.connect(
                        lambda _checked=False, model=identifier: self._pick_model_card_action(model)
                    )
                    card.content_layout.addWidget(action, alignment=Qt.AlignmentFlag.AlignLeft)
                    self.model_cards.addWidget(card)
                    fields = self._model_widgets[identifier] = (card, detail, label, action)
                _card, detail, label, action = fields
                self._models_installed[identifier] = installed
                detail.setText(f"Whisper · RU / EN · {size}\n{description}")
                label.setText(state if usable else f"{state} · ограничено политикой")
                action.setText(
                    "Активна"
                    if active and installed
                    else "Активировать"
                    if installed
                    else "Установить"
                )
                action.setEnabled((usable and not active) if installed else install_allowed)

        def _pick_model_card_action(self, identifier: str) -> None:
            if self._models_installed.get(identifier, False):
                self._activate_model_card(identifier)
            else:
                self.model_download_requested.emit(identifier)

        def _activate_model_card(self, identifier: str) -> None:
            index = self.model_combo.findData(identifier)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
                self._submit()

        def _build_privacy_page(self) -> None:
            page, form = self._page(
                "Приватность",
                "Речь обрабатывается на этом устройстве. История и запись аудио включаются отдельно.",
            )
            self.save_history_check = QCheckBox("Сохранять текст истории", page)
            self.recover_drafts_check = QCheckBox(
                "Сохранять завершённые черновики для восстановления", page
            )
            self.save_audio_check = QCheckBox("Сохранять исходное аудио", page)
            self.local_api_check = QCheckBox("Разрешить локальное API Windows", page)
            self.local_api_record_check = QCheckBox("Разрешить API запускать микрофон", page)
            self.local_api_record_check.setToolTip(
                "Запрос с токеном включает микрофон. Результат остаётся в VoxType."
            )
            self.local_api_status = QLabel("Выключен", page)
            self.local_api_status.setWordWrap(True)
            self.local_api_credentials_button = QPushButton("Канал и токен API…", page)
            self.local_api_credentials_button.clicked.connect(self.local_api_credentials_requested)
            self.local_api_check.toggled.connect(
                lambda enabled: self.local_api_record_check.setEnabled(
                    enabled and self.local_api_check.isEnabled()
                )
            )
            self.retention_spin = QSpinBox(page)
            self.retention_spin.setRange(1, 3650)
            self.retention_spin.setSuffix(" дней")
            data_actions = QWidget(page)
            actions = QHBoxLayout(data_actions)
            actions.setContentsMargins(0, 0, 0, 0)
            self.open_data_button = QPushButton("Открыть папку данных", data_actions)
            self.delete_data_button = QPushButton("Удалить локальные записи", data_actions)
            self.delete_data_button.setProperty("danger", True)
            actions.addWidget(self.open_data_button)
            actions.addWidget(self.delete_data_button)
            actions.addStretch(1)
            form.addRow(self.save_history_check)
            form.addRow(self.save_audio_check)
            form.addRow(self.recover_drafts_check)
            form.addRow(self.local_api_check)
            form.addRow(self.local_api_record_check)
            form.addRow(self.local_api_credentials_button)
            form.addRow("Локальное API", self.local_api_status)
            for title, action in (
                ("Центр восстановления", self.recovery_center_requested),
                ("Создать резервную копию", self.backup_requested),
                ("Восстановить резервную копию…", self.restore_backup_requested),
                ("Экспортировать настройки…", self.export_personal_requested),
                ("Импортировать настройки…", self.import_personal_requested),
            ):
                button = QPushButton(title, page)
                button.clicked.connect(lambda _checked=False, signal=action: signal.emit())
                form.addRow(button)
            for title, category in (
                ("Экспорт истории диктовок…", "history"),
                ("Экспорт транскриптов файлов…", "transcripts"),
            ):
                button = QPushButton(title, page)
                button.clicked.connect(
                    lambda _checked=False, selected=category: self.export_archive_requested.emit(
                        selected
                    )
                )
                form.addRow(button)
            form.addRow("Срок хранения", self.retention_spin)
            form.addRow("Данные", data_actions)
            notice = QLabel(
                "При вставке текст получает выбранное приложение. Диагностика содержит технические сведения. "
                "Проверка обновлений обращается к доверенному каналу только по запросу и не отправляет диктовку.",
                page,
            )
            notice.setWordWrap(True)
            notice.setProperty("muted", True)
            form.addRow(notice)

        def set_privacy_policy(self, *, history_disabled: bool, audio_disabled: bool) -> None:
            self._history_policy_disabled = history_disabled
            self._audio_policy_disabled = audio_disabled
            self.save_history_check.setEnabled(not history_disabled)
            self.recover_drafts_check.setEnabled(not history_disabled)
            self.save_audio_check.setEnabled(not audio_disabled)
            self.save_history_check.setToolTip(
                "Отключено политикой организации" if history_disabled else ""
            )
            self.save_audio_check.setToolTip(
                "Отключено политикой организации" if audio_disabled else ""
            )

        def set_local_api_status(self, enabled_by_policy: bool, active: bool, status: str) -> None:
            self.local_api_check.setEnabled(enabled_by_policy)
            self.local_api_record_check.setEnabled(
                enabled_by_policy and self.local_api_check.isChecked()
            )
            self.local_api_credentials_button.setEnabled(active)
            self.local_api_status.setText(status)

        def _build_hotkeys_page(self) -> None:
            page, form = self._page(
                "Горячие клавиши", "Глобальные сочетания; конфликты проверяются при применении."
            )
            self._hotkeys_page = page
            self.toggle_hotkey_edit = QKeySequenceEdit(page)
            self.pause_hotkey_edit = QKeySequenceEdit(page)
            self.language_hotkey_edit = QKeySequenceEdit(page)
            self.push_to_talk_hotkey_edit = QKeySequenceEdit(page)
            self.push_to_talk_hotkey_edit.setToolTip(
                "Используется только в режиме записи при удержании."
            )
            form.addRow("Начать / остановить", self.toggle_hotkey_edit)
            form.addRow("Пауза / продолжить", self.pause_hotkey_edit)
            form.addRow("Переключить RU / EN", self.language_hotkey_edit)
            form.addRow("Push-to-talk", self.push_to_talk_hotkey_edit)
            reset = VoxButton("Вернуть стандартные сочетания", page)
            reset.clicked.connect(self._reset_hotkeys)
            form.addRow(reset)

        def _reset_hotkeys(self) -> None:
            for widget, specification in (
                (self.toggle_hotkey_edit, DEFAULT_HOTKEY),
                (self.language_hotkey_edit, DEFAULT_LANGUAGE_HOTKEY),
                (self.pause_hotkey_edit, DEFAULT_PAUSE_HOTKEY),
            ):
                widget.setKeySequence(QKeySequence(specification))

        def _build_diagnostics_page(self) -> None:
            page, form = self._page(
                "Диагностика", "Логи не содержат продиктованный текст или PCM-аудио."
            )
            self.open_log_button = QPushButton("Открыть технический лог", page)
            self.export_diagnostics_button = QPushButton("Экспортировать отчёт", page)
            self.self_test_button = QPushButton("Выполнить self-test", page)
            self.show_versions_button = QPushButton("Показать версии библиотек", page)
            self.check_updates_button = QPushButton("Проверить обновления", page)
            self.check_updates_button.clicked.connect(lambda: self.updates_requested.emit())
            self.diagnostics_status_label = QLabel("Проверки ещё не запускались", page)
            self.diagnostics_status_label.setProperty("muted", True)
            form.addRow(self.open_log_button)
            form.addRow(self.export_diagnostics_button)
            form.addRow(self.self_test_button)
            form.addRow(self.show_versions_button)
            self.update_channel_combo = QComboBox(page)
            self.update_channel_combo.addItem("Стабильный", "stable")
            self.update_channel_combo.addItem("Бета", "beta")
            form.addRow("Канал обновлений", self.update_channel_combo)
            form.addRow(self.check_updates_button)
            form.addRow("Состояние", self.diagnostics_status_label)

        def _connect_actions(self) -> None:
            self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
            self.pages.currentChanged.connect(
                lambda _index: (
                    self.audio_view_hidden.emit()
                    if self.pages.currentWidget() is not self._audio_page
                    else None
                )
            )
            self.button_box.accepted.connect(self._save_and_accept)
            self.button_box.rejected.connect(self.reject)
            self.button_box.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(
                self._submit
            )
            self.vad_slider.valueChanged.connect(
                lambda value: self.vad_value_label.setText(f"{value}%")
            )
            self.test_microphone_button.clicked.connect(self._request_audio_test)
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
                self.live_preview_check,
                self.auto_stop_enabled,
                self.safe_terminal_check,
                self.caret_overlay_check,
                self.auto_insert_check,
                self.copy_complete_check,
                self.auto_hide_result_check,
                self.safe_clipboard_check,
                self.spacing_check,
                self.capitalize_check,
                self.text_transformations_check,
                self.noise_suppression_check,
                self.normalization_check,
                self.startup_pre_roll_check,
                self.gpu_experimental_check,
                self.save_history_check,
                self.save_audio_check,
                self.recover_drafts_check,
                self.local_api_check,
                self.local_api_record_check,
            )
            for check in checks:
                check.toggled.connect(self._mark_dirty)
            combos = (
                self.theme_combo,
                self.dictation_mode_combo,
                self.update_channel_combo,
                self.interface_language_combo,
                self.default_language_combo,
                self.profile_combo,
                self.target_policy_combo,
                self.audio_device_combo,
                self.preprocessing_combo,
                self.preprocessing_mode_combo,
                self.model_combo,
                self.power_mode_combo,
                self.memory_policy_combo,
                self.decoding_preset_combo,
            )
            for combo in combos:
                combo.currentIndexChanged.connect(self._mark_dirty)
            spins: tuple[QSpinBox | QSlider, ...] = (
                self.insertion_delay_spin,
                self.cpu_threads_spin,
                self.idle_unload_spin,
                self.retention_spin,
                self.vad_slider,
                *self.auto_stop_limits,
            )
            for spin in spins:
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
                    "release_channel": self.update_channel_combo.currentData(),
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
                    "live_preview": self.live_preview_check.isChecked(),
                    "mode": self.dictation_mode_combo.currentData(),
                    "safe_terminal_input": self.safe_terminal_check.isChecked(),
                    "caret_overlay": self.caret_overlay_check.isChecked(),
                    "auto_stop": {
                        "enabled": self.auto_stop_enabled.isChecked(),
                        "minimum_speech_ms": self.auto_stop_limits[0].value(),
                        "trailing_silence_ms": self.auto_stop_limits[1].value(),
                        "max_session_duration_ms": self.auto_stop_limits[2].value(),
                    },
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
                    "user_text_transformations": self.text_transformations_check.isChecked(),
                },
                "audio": {
                    "device_id": self.audio_device_combo.currentData(),
                    "vad_sensitivity": self.vad_slider.value() / 100.0,
                    "preprocessing_profile": self.preprocessing_combo.currentData(),
                    "preprocessing_mode": self.preprocessing_mode_combo.currentData(),
                    "noise_suppression": self.noise_suppression_check.isChecked(),
                    "normalization": self.normalization_check.isChecked(),
                    "startup_pre_roll": self.startup_pre_roll_check.isChecked(),
                },
                "model": {
                    "installed_model": self.model_combo.currentData(),
                    "cpu_threads": self.cpu_threads_spin.value(),
                    "power_mode": self.power_mode_combo.currentData(),
                    "decoding_preset": self.decoding_preset_combo.currentData(),
                    "memory_policy": self.memory_policy_combo.currentData(),
                    "idle_unload_minutes": self.idle_unload_spin.value(),
                    "experimental_gpu": self.gpu_experimental_check.isChecked(),
                },
                "privacy": {
                    "save_history": self.save_history_check.isChecked(),
                    "save_audio": self.save_audio_check.isChecked(),
                    "recover_unsaved_drafts": self.recover_drafts_check.isChecked(),
                    "retention_days": self.retention_spin.value(),
                    "local_api_enabled": self.local_api_check.isChecked(),
                    "local_api_allow_recording": (
                        self.local_api_check.isChecked() and self.local_api_record_check.isChecked()
                    ),
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
                _select_data(
                    self.update_channel_combo,
                    _setting(general, "release_channel", default="stable"),
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
                _select_data(
                    self.dictation_mode_combo, _setting(dictation, "mode", default="toggle")
                )
                self.safe_terminal_check.setChecked(
                    bool(_setting(dictation, "safe_terminal_input", default=True))
                )
                self.caret_overlay_check.setChecked(
                    bool(_setting(dictation, "caret_overlay", default=False))
                )
                auto_stop = _section(dictation, "auto_stop")
                self.auto_stop_enabled.setChecked(
                    bool(_setting(auto_stop, "enabled", default=True))
                )
                for spin, key, default in zip(
                    self.auto_stop_limits,
                    (
                        "minimum_speech_ms",
                        "trailing_silence_ms",
                        "max_session_duration_ms",
                    ),
                    (400, 1500, 120000),
                    strict=True,
                ):
                    spin.setValue(int(str(_setting(auto_stop, key, default=default))))
                self.live_preview_check.setChecked(
                    bool(_setting(dictation, "live_preview", default=False))
                )
                self.auto_insert_check.setChecked(
                    bool(_setting(dictation, "auto_insert", default=True))
                )
                self.copy_complete_check.setChecked(
                    bool(_setting(dictation, "copy_full_text_on_complete", default=False))
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
                self.text_transformations_check.setChecked(
                    bool(_setting(dictation, "user_text_transformations", default=True))
                )
                _select_data(self.audio_device_combo, _setting(audio, "device_id", default=""))
                sensitivity = float(str(_setting(audio, "vad_sensitivity", default=0.5)))
                self.vad_slider.setValue(round(min(1.0, max(0.0, sensitivity)) * 100))
                _select_data(
                    self.preprocessing_combo,
                    _setting(audio, "preprocessing_profile", default="normal"),
                )
                _select_data(
                    self.preprocessing_mode_combo,
                    _setting(audio, "preprocessing_mode", default="legacy"),
                )
                self._update_audio_processing_options()
                self.noise_suppression_check.setChecked(
                    bool(_setting(audio, "noise_suppression", default=False))
                )
                self.startup_pre_roll_check.setChecked(
                    bool(_setting(audio, "startup_pre_roll", default=False))
                )
                self.normalization_check.setChecked(
                    bool(_setting(audio, "normalization", default=True))
                )
                _select_data(
                    self.model_combo,
                    _setting(model, "installed_model", "name", default=""),
                )
                self.cpu_threads_spin.setValue(int(str(_setting(model, "cpu_threads", default=0))))
                _select_data(
                    self.power_mode_combo,
                    _setting(model, "power_mode", default="balanced"),
                )
                _select_data(
                    self.memory_policy_combo,
                    _setting(model, "memory_policy", default="keep_loaded"),
                )
                _select_data(
                    self.decoding_preset_combo,
                    _setting(model, "decoding_preset", default="balanced"),
                )
                self.idle_unload_spin.setValue(
                    int(str(_setting(model, "idle_unload_minutes", default=15)))
                )
                self.gpu_experimental_check.setChecked(
                    bool(_setting(model, "experimental_gpu", default=False))
                )
                self.save_history_check.setChecked(
                    not getattr(self, "_history_policy_disabled", False)
                    and bool(_setting(privacy, "save_history", default=False))
                )
                self.recover_drafts_check.setChecked(
                    not getattr(self, "_history_policy_disabled", False)
                    and bool(_setting(privacy, "recover_unsaved_drafts", default=False))
                )
                self.save_audio_check.setChecked(
                    not getattr(self, "_audio_policy_disabled", False)
                    and bool(_setting(privacy, "save_audio", default=False))
                )
                self.local_api_check.setChecked(
                    bool(_setting(privacy, "local_api_enabled", default=False))
                )
                self.local_api_record_check.setChecked(
                    bool(_setting(privacy, "local_api_allow_recording", default=False))
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

        def _request_audio_test(self) -> None:
            device = self.audio_device_combo.currentData()
            self.audio_test_requested.emit("" if device is None else str(device))

        def set_audio_devices(
            self,
            devices: Iterable[str | tuple[str, object] | object],
            selected_id: object = _KEEP_SELECTION,
        ) -> None:
            previous = (
                self.audio_device_combo.currentData()
                if selected_id is _KEEP_SELECTION
                else selected_id
            )
            blocker = QSignalBlocker(self.audio_device_combo)
            self.audio_device_combo.clear()
            self.audio_device_combo.addItem("По умолчанию в Windows", None)
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
            if previous is not None and self.audio_device_combo.findData(previous) < 0:
                self.audio_device_combo.addItem(f"Недоступное устройство ({previous})", previous)
            _select_data(self.audio_device_combo, previous)
            del blocker

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
            _select_data(self.model_combo, previous)
            del blocker

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
            if confirm_delete(
                self,
                "Удалить модель?",
                f"Локальная модель «{self.model_combo.currentText()}» будет удалена.",
            ):
                self.model_delete_requested.emit(model_id)

        def _confirm_delete_data(self) -> None:
            if confirm_delete(
                self,
                "Удалить локальные записи?",
                "Будут удалены история, черновики, журналы сессий и сохранённое аудио. "
                "Это действие нельзя отменить.",
            ):
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
