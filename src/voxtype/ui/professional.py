"""Professional tools in the existing VoxType desktop shell."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, replace
from functools import partial
from importlib.util import find_spec
from pathlib import Path
from threading import Thread
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from PySide6.QtCore import QIODevice, QSaveFile, Qt, QTimer, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from voxtype.core.capabilities import CapabilityRegistry
from voxtype.core.models import RecognitionResult
from voxtype.core.profiles import (
    BUILTIN_IDS,
    FORMATTING_MODES,
    AppRule,
    DictationContext,
    DictationProfile,
)
from voxtype.settings.organization_pack import OrganizationProfilePack
from voxtype.transcript.corrections import CorrectionSuggestion, CorrectionSuggestionService
from voxtype.transcript.editing import (
    RevisionSource,
    TextEditService,
    TranscriptRevision,
    TranscriptRevisions,
    VoiceEditCommandInterpreter,
)
from voxtype.transcript.enhancer import OPERATIONS, LlamaCppTextEnhancer, LocalTextEnhancer
from voxtype.transcript.professional import (
    ACTION_PARAMETERS,
    MAX_HINT_CHARACTERS,
    VOICE_ACTION_TYPES,
    RecognitionRequest,
    Snippet,
)
from voxtype.transcript.workflows import (
    ACTIONS,
    WORKFLOW_TEMPLATES,
    WorkflowDefinition,
    parse_workflows,
)
from voxtype.windows.selection import SelectedText, SelectionUnavailable

from .obsidian import (
    VoxButton,
    VoxCard,
    VoxComboBox,
    VoxDialog,
    VoxEmptyState,
    VoxSearchField,
    VoxSectionHeader,
    VoxTextField,
    VoxToggle,
    confirm_delete,
)
from .records_view import RecordsView

if TYPE_CHECKING:
    from voxtype.asr.transcription_queue import FileTranscriptionQueue
    from voxtype.core.controller import ApplicationController

    from .transcription_panel import TranscriptionPanel

_LOGGER = logging.getLogger(__name__)


class ProfessionalWindow(QDialog):
    operation_finished = Signal(object, str)
    insertion_requested = Signal(str)
    selection_finished = Signal(object, str)
    statistics_ready = Signal(int, object)
    enhancement_ready = Signal(str, str)
    workflow_finished = Signal(str)
    profiles_refreshed = Signal()
    editor_draft_requested = Signal(object)
    scratchpad_toggle_requested = Signal()
    voice_edit_toggle_requested = Signal()
    correction_ready = Signal(object)

    def __init__(self, controller: ApplicationController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.profiles = controller.profiles
        self.setWindowTitle("Профессиональные инструменты VoxType")
        self.resize(720, 560)
        root = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        root.addWidget(self.tabs)
        self.feedback = QLabel(self)
        self.feedback.setWordWrap(True)
        self.workflow_finished.connect(self.feedback.setText)
        root.addWidget(self.feedback)
        page = QWidget(self)
        self.tabs.addTab(page, "Профили")
        profile_root = QHBoxLayout(page)
        self.profile_splitter = QSplitter(page)
        profile_root.addWidget(self.profile_splitter)
        profile_list = QWidget(self.profile_splitter)
        list_layout = QVBoxLayout(profile_list)
        self.profile_list = QListWidget(profile_list)
        self.profile_list.setAccessibleName("Профили диктовки")
        list_layout.addWidget(self.profile_list)
        self.profile_hint = QLabel("Нет пользовательских профилей", profile_list)
        self.profile_hint.setProperty("muted", True)
        self.profile_hint.setWordWrap(True)
        list_layout.addWidget(self.profile_hint)
        create_actions = QHBoxLayout()
        self._button("Создать", lambda: self._create(False), create_actions)
        self._button("Дублировать", lambda: self._create(True), create_actions)
        list_layout.addLayout(create_actions)
        scroll = QScrollArea(self.profile_splitter)
        scroll.setWidgetResizable(True)
        details = QWidget(scroll)
        scroll.setWidget(details)
        self.profile_splitter.addWidget(profile_list)
        self.profile_splitter.addWidget(scroll)
        self.profile_splitter.setStretchFactor(1, 2)
        form = QFormLayout(details)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.profile = QComboBox(page)
        self.profile.hide()
        self.name = VoxTextField(page)
        self.language = self._choices(("Язык настроек", "", "Русский", "ru", "English", "en"))
        self.model = self._choices(
            ("Модель настроек", "", "small", "small", "large-v3-turbo", "large-v3-turbo")
        )
        self.formatting = self._choices(
            tuple(value for mode in FORMATTING_MODES for value in (mode, mode))
        )
        self.insertion = self._choices(
            (
                "Режим настроек",
                "",
                "Автоматический",
                "auto",
                "Буфер обмена",
                "clipboard_paste",
                "Unicode",
                "unicode_input",
                "Только копирование",
                "safe_clipboard",
            )
        )
        self.enabled = VoxToggle("Профиль активен", page)
        self.rules = QPlainTextEdit(page)
        self.vocabulary_id = QLineEdit(page)
        self.command_set_id = QLineEdit(page)
        self.snippet_set_id = QLineEdit(page)
        self.rules.setPlaceholderText("app.exe | *заголовок* | 10 | 1\nC:\\Apps\\app.exe | | 5 | 0")
        for label, widget in (
            ("Название", self.name),
            ("Язык", self.language),
            ("Модель", self.model),
            ("Формат", self.formatting),
            ("Вставка", self.insertion),
            ("Включён", self.enabled),
            ("ID словаря", self.vocabulary_id),
            ("ID набора команд", self.command_set_id),
            ("ID набора шаблонов", self.snippet_set_id),
            ("Правила: EXE | заголовок | приоритет | 0/1", self.rules),
        ):
            form.addRow(label, widget)
        actions = QHBoxLayout()
        self.save_button = self._button("Сохранить", self._save, actions)
        self.remove_button = self._button("Удалить", self._remove, actions)
        self._button("По умолчанию", self._default, actions)
        form.addRow(actions)
        self.technical_language_hint = QLabel(
            "Основной язык выбирается через RU/EN. Термины и идентификаторы сохраняются без перевода.",
            page,
        )
        self.technical_language_hint.setWordWrap(True)
        self.technical_language_hint.setProperty("muted", True)
        form.addRow(self.technical_language_hint)
        self.profile.currentIndexChanged.connect(self._load)
        self.profile_list.currentRowChanged.connect(self.profile.setCurrentIndex)
        self.operation_finished.connect(self._finished)
        self._reload(self.profiles.default_id)
        self.collection_pages: dict[str, QWidget] = {}
        self.collection_editors: dict[str, QPlainTextEdit] = {}
        self.collection_tables: dict[str, RecordsView] = {}
        for key, label in (
            ("dictionary", "Словарь"),
            ("snippets", "AutoText"),
            ("commands", "Голосовые команды"),
            ("replacements", "Замены"),
        ):
            if (key == "commands" and not self.controller.policy.allow_voice_commands) or (
                key == "snippets" and not self.controller.policy.allow_user_snippets
            ):
                continue
            page = QWidget(self.tabs)
            self.collection_pages[key] = page
            self.tabs.addTab(page, label)
        self.tabs.currentChanged.connect(self._load_visible_collection)
        self._add_text_editor()
        self._add_workflows()
        self._add_organization()
        self._add_statistics()
        self.enhancer: LocalTextEnhancer | None = None
        self.transcription_panel: TranscriptionPanel | None = None
        self._add_capabilities()
        self.selection_finished.connect(self._set_selection)

    def bind_transcription_queue(self, queue: FileTranscriptionQueue) -> None:
        from voxtype.ui.transcription_panel import TranscriptionPanel

        self.transcription_panel = TranscriptionPanel(queue, self.tabs)
        self.tabs.addTab(self.transcription_panel, "Файлы")
        self.tabs.currentChanged.connect(lambda _index: self.transcription_panel.refresh())

    def _add_statistics(self) -> None:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.stats_period = QComboBox(page)
        self.stats_period.addItem("По дням (UTC)", False)
        self.stats_period.addItem("По неделям (UTC)", True)
        row.addWidget(self.stats_period)
        self.stats_wpm = QSpinBox(page)
        self.stats_wpm.setRange(10, 150)
        self.stats_wpm.setSuffix(" слов/мин")
        self.stats_wpm.setValue(self.controller.usage_metrics.typing_speed())
        row.addWidget(self.stats_wpm)
        self._button("Обновить", self._load_statistics, row)
        self._button("Сохранить скорость", self._save_typing_speed, row)
        layout.addLayout(row)
        self.stats_output = QPlainTextEdit(page)
        self.stats_output.setReadOnly(True)
        layout.addWidget(self.stats_output)
        self.stats_page = page
        self._stats_revision = 0
        self._stats_busy = False
        self.statistics_ready.connect(self._render_statistics)
        self.tabs.currentChanged.connect(
            lambda _index: (
                self._load_statistics() if self.tabs.currentWidget() is self.stats_page else None
            )
        )
        self.stats_period.currentIndexChanged.connect(lambda _index: self._load_statistics())
        self.tabs.addTab(page, "Статистика")

    def _load_statistics(self) -> None:
        self._stats_revision += 1
        if self._stats_busy:
            return
        self._stats_busy = True
        revision = self._stats_revision
        weekly = bool(self.stats_period.currentData())

        def query() -> None:
            try:
                data = self.controller.usage_metrics.summary(weekly=weekly)
            except Exception:
                self.statistics_ready.emit(revision, None)
            else:
                self.statistics_ready.emit(revision, data)

        Thread(target=query, name="voxtype-statistics-read", daemon=True).start()

    def _save_typing_speed(self) -> None:
        value = self.stats_wpm.value()

        def save() -> str:
            self.controller.usage_metrics.save_typing_speed(value)
            return self.profiles.default_id

        self._persist(save)

    def _render_statistics(self, revision: int, data: tuple[dict[str, Any], ...] | None) -> None:
        self._stats_busy = False
        if revision != self._stats_revision:
            self._load_statistics()
            return
        if data is None:
            self.stats_output.setPlainText("Статистика недоступна")
            return
        lines = []
        for row in data:
            count = row["sessions"]
            minutes = row["duration_ms"] / 60_000
            processing = row["processing_ms"] / count / 1000
            saved = max(0.0, row["words"] / self.stats_wpm.value() - minutes)
            lines.append(
                f"{row['period']} · {row['profile_id']} · {row['outcome']} · {count} сеанс. · "
                f"{minutes:.1f} мин. · {row['words']} слов · обработка {processing:.2f} с/сеанс · "
                f"экономия ≈ {saved:.1f} мин."
            )
        self.stats_output.setPlainText("\n".join(lines) or "За последние 90 дней нет диктовок")

    def _add_capabilities(self) -> None:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        self.capability_output = QPlainTextEdit(page)
        self.capability_output.setReadOnly(True)
        layout.addWidget(self.capability_output)
        self.capability_page = page
        self.tabs.currentChanged.connect(
            lambda _index: (
                self._refresh_capabilities() if self.tabs.currentWidget() is page else None
            )
        )
        if find_spec("llama_cpp") is not None and self.controller.policy.permits_ai:
            row = QHBoxLayout()
            self.ai_operation = QComboBox(page)
            for operation, title in OPERATIONS.items():
                self.ai_operation.addItem(title, operation)
            row.addWidget(self.ai_operation)
            self.ai_select = self._button("Выбрать GGUF…", self._select_enhancer, row)
            self.ai_run = self._button("Предпросмотр из редактора", self._run_enhancement, row)
            self.ai_run.setEnabled(False)
            layout.addLayout(row)
            self.enhancement_ready.connect(self._show_enhancement)
        else:
            hint = QLabel("Обычные преобразования доступны во вкладке редактирования.", page)
            layout.addWidget(hint)
        self.tabs.addTab(page, "Возможности")
        self._refresh_capabilities()

    def _refresh_capabilities(self) -> None:
        panel = self.transcription_panel
        capabilities = CapabilityRegistry.snapshot(
            self.controller,
            file_queue=panel.queue if panel else None,
            enhancer=self.enhancer,
        )
        text = [
            f"{key} · {'доступно' if item.available else 'недоступно'} · {item.reason}"
            for key, item in capabilities.items()
        ]
        self.capability_output.setPlainText("\n".join(text))

    def _select_enhancer(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Локальная модель", "", "GGUF (*.gguf)")
        if filename:
            try:
                self.enhancer = LlamaCppTextEnhancer(Path(filename))
            except (OSError, ValueError) as exc:
                self.feedback.setText(str(exc))
                return
            self.ai_run.setEnabled(True)
            self._refresh_capabilities()

    def _run_enhancement(self) -> None:
        backend = self.enhancer
        if backend is None or not self.controller.policy.permits_ai:
            return
        text = self.edit_original.toPlainText()
        profile = self._selected()
        language = (profile.language if profile else "") or self.controller.language.value
        operation = self.ai_operation.currentData()
        self.ai_run.setEnabled(False)
        self.ai_select.setEnabled(False)

        def transform() -> None:
            try:
                result = backend.transform(text, operation, language, ())
            except ValueError as exc:
                self.enhancement_ready.emit("", str(exc))
            except Exception as exc:
                self.enhancement_ready.emit(
                    "", f"Локальная модель не обработала текст ({type(exc).__name__})"
                )
            else:
                self.enhancement_ready.emit(result, "")

        Thread(target=transform, name="voxtype-local-text-enhancer", daemon=True).start()

    def _show_enhancement(self, result: str, error: str) -> None:
        self.ai_select.setEnabled(True)
        self.ai_run.setEnabled(True)
        if error:
            self.feedback.setText(error)
            return
        dialog = VoxDialog(self)
        dialog.setWindowTitle("Локальный AI — предпросмотр")
        dialog.resize(680, 430)
        layout = QVBoxLayout(dialog)
        preview = QPlainTextEdit(result, dialog)
        layout.addWidget(preview)
        row = QHBoxLayout()

        def apply() -> None:
            self._write_editor_revision(preview.toPlainText(), RevisionSource.USER_EDIT)
            dialog.accept()

        self._button("Перенести в локальный редактор", apply, row)
        self._button("Отмена", dialog.reject, row)
        layout.addLayout(row)
        dialog.exec()

    def _add_organization(self) -> None:
        from voxtype.settings.enterprise import EnterprisePolicy

        page = QWidget(self)
        layout = QVBoxLayout(page)
        service = self.controller.enterprise
        location = QLabel(f"Политика: {service.status}\n{service.path or ''}", page)
        location.setWordWrap(True)
        layout.addWidget(location)
        defaults = asdict(EnterprisePolicy())
        summary = QPlainTextEdit(page)
        summary.setReadOnly(True)
        summary.setPlainText(
            json.dumps(
                {
                    key: value
                    for key, value in asdict(service.policy).items()
                    if value != defaults[key]
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        layout.addWidget(summary)
        row = QHBoxLayout()

        def import_pack() -> None:
            filename, _ = QFileDialog.getOpenFileName(page, "Оргпакет", "", "JSON (*.json)")
            if not filename:
                return
            packs = self.controller.organization_packs
            try:
                pack = OrganizationProfilePack.read(Path(filename))
                preview = packs.merge(pack)
            except (ValueError, TypeError, OSError) as exc:
                self.feedback.setText(str(exc))
                return
            dialog = QMessageBox(
                QMessageBox.Icon.Question, "Импорт оргпакета", "Добавить новые записи?", parent=page
            )
            dialog.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            dialog.setDefaultButton(QMessageBox.StandardButton.No)
            dialog.setDetailedText("\n".join(preview))
            if dialog.exec() == QMessageBox.StandardButton.Yes:

                def apply() -> str:
                    packs.merge(pack, apply=True)
                    return "organization"

                self._persist(apply)

        def export_pack() -> None:
            name, ok = QInputDialog.getText(page, "Оргпакет", "Название организации")
            if not ok or not name.strip() or len(name) > 128:
                return
            filename, _ = QFileDialog.getSaveFileName(
                page, "Экспорт оргпакета", "VoxType.json", "JSON (*.json)"
            )
            if filename:
                try:
                    active = asdict(self.controller.policy)
                    text = self.controller.organization_packs.export(
                        name.strip(),
                        {key: value for key, value in active.items() if value != defaults[key]},
                    )
                    Path(filename).write_text(text, encoding="utf-8")
                except (OSError, ValueError) as exc:
                    self.feedback.setText(str(exc))

        self._button("Импорт с предпросмотром…", import_pack, row)
        self._button("Экспорт…", export_pack, row)
        layout.addLayout(row)
        self.tabs.addTab(page, "Организация")

    def _add_workflows(self) -> None:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        toolbar = QHBoxLayout()
        templates = VoxComboBox(page)
        for template in WORKFLOW_TEMPLATES:
            templates.addItem(template.name, template)
        toolbar.addWidget(templates, 1)
        self._button(
            "Использовать шаблон",
            lambda: self._edit_workflow(
                self.controller.workflows.copy_template(templates.currentData())
            ),
            toolbar,
        )
        self._button("Новый процесс", lambda: self._edit_workflow(None), toolbar)
        self._button("Папка вывода…", self._workflow_directory, toolbar)
        layout.addLayout(toolbar)
        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        panel = QWidget(scroll)
        self.workflow_cards = QVBoxLayout(panel)
        scroll.setWidget(panel)
        layout.addWidget(scroll, 1)
        self.workflow_page = page
        self.tabs.addTab(page, "Процессы")
        self._refresh_workflows()

    def _refresh_workflows(self) -> None:
        while self.workflow_cards.count():
            item = self.workflow_cards.takeAt(0)
            if item is not None and (widget := item.widget()) is not None:
                widget.hide()
                widget.deleteLater()
        definitions = self.controller.workflows.definitions()
        if not definitions:
            self.workflow_cards.addWidget(
                VoxEmptyState(
                    "workflow",
                    "Нет рабочих процессов",
                    "Выберите готовый шаблон или создайте процесс.",
                    self,
                )
            )
        for workflow in definitions:
            card = VoxCard(self)
            card.content_layout.addWidget(VoxSectionHeader(workflow.name, card))
            trigger = {
                "dictation": "После диктовки",
                "file": "После транскрипции",
                "manual": "Вручную",
            }
            status = "Включён" if workflow.enabled else "Выключен"
            card.content_layout.addWidget(
                QLabel(
                    f"{trigger[workflow.trigger_type]} · шагов: {len(workflow.actions)} · {status}",
                    card,
                )
            )
            actions = QHBoxLayout()
            self._button("Изменить", partial(self._edit_workflow, workflow), actions)
            if workflow.enabled and workflow.trigger_type == "manual":
                self._button("Запустить", partial(self._run_workflow, workflow), actions)
            actions.addStretch()
            card.content_layout.addLayout(actions)
            self.workflow_cards.addWidget(card)
        self.workflow_cards.addStretch()

    def _workflow_directory(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Локальная папка вывода")
        if folder:

            def save() -> str:
                self.controller.workflows.set_directory(Path(folder))
                return self.profiles.default_id

            self._persist(save)

    def _run_workflow(self, workflow: WorkflowDefinition) -> None:
        profile, text = self.controller.workflow_input()
        if not text.strip():
            selected = self._selected()
            profile = selected.id if selected else self.profiles.default_id
            text = self.edit_original.toPlainText()
        if not text.strip() or (workflow.profile_id and workflow.profile_id != profile):
            self.feedback.setText(
                "Нет текста для выбранного профиля. Используйте диктовку или локальный редактор."
            )
            return

        def run() -> str:
            steps = self.controller.workflows.dispatch(
                "manual", profile, text, workflow_id=workflow.id
            )
            self.workflow_finished.emit(
                "\n".join(f"{step.action} · {step.status}" for step in steps)
            )
            return self.profiles.default_id

        self._persist(run)

    def _edit_workflow(self, workflow: WorkflowDefinition | None) -> None:
        dialog = VoxDialog(self)
        dialog.setWindowTitle("Редактор рабочего процесса")
        dialog.resize(590, 450)
        form = QFormLayout(dialog)
        name = VoxTextField(dialog)
        name.setText(workflow.name if workflow else "")
        trigger = VoxComboBox(dialog)
        for text, key in (
            ("Вручную", "manual"),
            ("После диктовки", "dictation"),
            ("После файла", "file"),
        ):
            trigger.addItem(text, key)
        if workflow:
            trigger.setCurrentIndex(trigger.findData(workflow.trigger_type))
        profile = VoxComboBox(dialog)
        profile.addItem("Любой", "")
        for item in self.profiles.profiles():
            profile.addItem(item.name, item.id)
        if workflow:
            profile.setCurrentIndex(max(0, profile.findData(workflow.profile_id)))
        enabled = VoxToggle("Процесс включён", dialog)
        enabled.setChecked(workflow.enabled if workflow else True)
        sequence = QListWidget(dialog)
        sequence.setAccessibleName("Последовательность шагов")
        steps = [asdict(action) for action in workflow.actions] if workflow else []
        action_labels = {
            "save_txt": "Сохранить TXT",
            "save_json": "Сохранить JSON",
            "apply_template": "Применить шаблон",
            "copy": "Скопировать",
            "preview": "Открыть предпросмотр",
            "offer_export": "Предложить экспорт",
        }

        def refresh_steps(position: int = 0) -> None:
            sequence.clear()
            for index, step in enumerate(steps, 1):
                sequence.addItem(f"{index}. {action_labels[step['kind']]}")
            sequence.setCurrentRow(max(0, min(position, len(steps) - 1)))

        def change_step(edit: bool = False) -> None:
            index = sequence.currentRow() if edit else -1
            if edit and index < 0:
                return
            keys = sorted(ACTIONS)
            labels = [action_labels[key] for key in keys]
            selected, ok = QInputDialog.getItem(
                dialog,
                "Шаг процесса",
                "Действие",
                labels,
                keys.index(steps[index]["kind"]) if index >= 0 else 0,
                False,
            )
            if not ok:
                return
            kind = keys[labels.index(selected)]
            template = ""
            if kind == "apply_template":
                template, ok = QInputDialog.getMultiLineText(
                    dialog,
                    "Шаблон",
                    "Текст с параметром {text}",
                    steps[index]["template"] if index >= 0 else "{text}",
                )
                if not ok:
                    return
            step = {"kind": kind, "template": template}
            if index >= 0:
                steps[index] = step
            else:
                steps.append(step)
            refresh_steps(index if index >= 0 else len(steps) - 1)

        def change_order(offset: int) -> None:
            index = sequence.currentRow()
            if index < 0:
                return
            if offset == 0:
                steps.pop(index)
            elif 0 <= index + offset < len(steps):
                steps[index], steps[index + offset] = steps[index + offset], steps[index]
                index += offset
            refresh_steps(index)

        for label, widget in (("Название", name), ("Запуск", trigger), ("Профиль", profile)):
            form.addRow(label, widget)
        form.addRow(enabled)
        form.addRow("Шаги", sequence)
        controls = QHBoxLayout()
        self._button("Добавить", change_step, controls)
        self._button("Изменить", lambda: change_step(True), controls)
        self._button("Вверх", lambda: change_order(-1), controls)
        self._button("Вниз", lambda: change_order(1), controls)
        self._button("Удалить шаг", lambda: change_order(0), controls)
        form.addRow(controls)
        actions = QHBoxLayout()
        self._button("Сохранить", dialog.accept, actions)
        self._button("Отмена", dialog.reject, actions)
        form.addRow(actions)
        refresh_steps()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        identifier = workflow.id if workflow else uuid4().hex
        data = [
            asdict(item)
            for item in self.controller.workflows.definitions()
            if item.id != identifier
        ]
        data.append(
            {
                "id": identifier,
                "name": name.text(),
                "trigger_type": trigger.currentData(),
                "profile_id": profile.currentData(),
                "enabled": enabled.isChecked(),
                "actions": steps,
            }
        )
        try:
            definitions = parse_workflows(json.loads(json.dumps(data, ensure_ascii=False)))
        except (ValueError, TypeError) as exc:
            self.feedback.setText(str(exc))
            return

        def save() -> str:
            self.controller.workflows.save(definitions)
            return "workflows"

        self._persist(save)

    def _choices(self, pairs: tuple[str, ...]) -> QComboBox:
        combo = VoxComboBox(self)
        for index in range(0, len(pairs), 2):
            combo.addItem(pairs[index], pairs[index + 1])
        return combo

    def _button(self, label: str, action: Callable[[], None], row: QHBoxLayout) -> QPushButton:
        button = VoxButton(label, self)
        button.clicked.connect(lambda _checked=False: action())
        row.addWidget(button)
        return button

    def _selected(self) -> DictationProfile | None:
        current = self.profile.currentData()
        return current if isinstance(current, DictationProfile) else None

    def _reload(self, selected: str) -> None:
        self.profile.blockSignals(True)
        self.profile_list.blockSignals(True)
        self.profile.clear()
        self.profile_list.clear()
        index = 0
        for profile in self.profiles.profiles():
            marker = " • по умолчанию" if profile.id == self.profiles.default_id else ""
            if profile.id == selected:
                index = self.profile.count()
            self.profile.addItem(profile.name + marker, profile)
            kind = "Встроенный" if profile.id in BUILTIN_IDS else "Пользовательский"
            state = "Активен" if profile.enabled else "Выключен"
            self.profile_list.addItem(
                QListWidgetItem(
                    f"{profile.name}{marker}\n{kind} · {state} · {profile.formatting_mode}"
                )
            )
        self.profile.setCurrentIndex(index)
        self.profile_list.setCurrentRow(index)
        self.profile_hint.setVisible(
            not any(item.id not in BUILTIN_IDS for item in self.profiles.profiles())
        )
        self.profile_list.blockSignals(False)
        self.profile.blockSignals(False)
        self._load()

    def _load(self) -> None:
        profile = self._selected()
        if profile is None:
            return
        mutable = profile.id not in BUILTIN_IDS
        self.technical_language_hint.setVisible(
            profile.output_language == "preserve_spoken_language"
        )
        self.name.setText(profile.name)
        self.vocabulary_id.setText(profile.vocabulary_id)
        self.command_set_id.setText(profile.command_set_id)
        self.snippet_set_id.setText(profile.snippet_set_id)
        self.enabled.setChecked(profile.enabled)
        self.rules.setPlainText(
            "\n".join(
                f"{rule.executable} | {rule.title_glob} | {rule.priority} | {int(rule.enabled)}"
                for rule in profile.app_rules
            )
        )
        for combo, value in (
            (self.language, profile.language),
            (self.model, profile.model_id),
            (self.formatting, profile.formatting_mode),
            (self.insertion, profile.insertion_mode),
        ):
            combo.setCurrentIndex(combo.findData(value))
            combo.setEnabled(mutable)
        for widget in (
            self.name,
            self.enabled,
            self.rules,
            self.save_button,
            self.remove_button,
            self.vocabulary_id,
            self.command_set_id,
            self.snippet_set_id,
        ):
            widget.setEnabled(mutable)

    def _persist(self, work: Callable[[], str]) -> None:
        self.tabs.setEnabled(False)
        self.feedback.setText("Сохраняю…")

        def worker() -> None:
            try:
                selected = work()
            except ValueError as exc:
                self.operation_finished.emit(None, str(exc))
            except OSError:
                _LOGGER.exception("professional_configuration_write_failed")
                self.operation_finished.emit(None, "Не удалось сохранить настройки")
            else:
                self.operation_finished.emit(selected, "Сохранено")

        Thread(target=worker, name="voxtype-professional-settings", daemon=True).start()

    def _finished(self, selected: object, message: str) -> None:
        self.tabs.setEnabled(True)
        if selected == "workflows":
            self._refresh_workflows()
            selected = None
        if selected == "organization":
            for key, editor in self.collection_editors.items():
                editor.setPlainText(self.controller.professional_content.export_json(key))
            selected = self.profiles.default_id
        if isinstance(selected, str):
            self._reload(selected)
            self.profiles_refreshed.emit()
        self.feedback.setText(message)

    def edit_input_compatibility(self) -> None:
        profiles = [item for item in self.profiles.profiles() if item.id not in BUILTIN_IDS]
        if not profiles:
            self.feedback.setText(
                "Создайте или продублируйте профиль, чтобы назначить правило приложения."
            )
            return
        name, ok = QInputDialog.getItem(
            self, "Совместимость приложений", "Профиль", [p.name for p in profiles], editable=False
        )
        if not ok:
            return
        profile = next(item for item in profiles if item.name == name)
        options = [
            f"{item.executable} · {item.title_glob or 'любое поле'}" for item in profile.app_rules
        ]
        rule_index = -1
        if options:
            options.append("Добавить приложение…")
            selected, ok = QInputDialog.getItem(
                self, "Совместимость приложений", "Правило", options, editable=False
            )
            if not ok:
                return
            rule_index = options.index(selected)
            if rule_index == len(profile.app_rules):
                rule_index = -1
        previous = profile.app_rules[rule_index] if rule_index >= 0 else None
        dialog = VoxDialog(self)
        dialog.setWindowTitle("Совместимость приложения")
        form = QFormLayout(dialog)
        exe = QLineEdit(previous.executable if previous else "", dialog)
        exe.setPlaceholderText("Имя EXE или полный путь")
        form.addRow("Приложение", exe)
        strategy = self._choices(
            (
                "Автоматически",
                "auto",
                "Буфер обмена",
                "clipboard_paste",
                "Unicode",
                "unicode_input",
                "Локальный предпросмотр",
                "local_preview",
                "Только копирование",
                "safe_clipboard",
            )
        )
        restore = self._choices(
            (
                "Восстанавливать исходный буфер",
                "preserve",
                "Не использовать буфер",
                "avoid_clipboard",
            )
        )
        formatting = self._choices(
            ("Настройки профиля", "profile", "Сохранять технический текст", "preserve")
        )
        strict = QCheckBox("Проверять исходное поле", dialog)
        multiline = QCheckBox("Подтверждать многострочную вставку", dialog)
        unicode = QCheckBox("Предпочитать Unicode", dialog)
        automatic = QCheckBox("Разрешать автоматическую вставку", dialog)
        for label, widget in (("Способ", strategy), ("Буфер", restore), ("Формат", formatting)):
            form.addRow(label, widget)
        for checkbox in (strict, multiline, unicode, automatic):
            form.addRow(checkbox)

        def populate(rule: AppRule) -> None:
            for widget, value in (
                (strategy, rule.input_strategy),
                (restore, rule.clipboard_restore_policy),
                (formatting, rule.formatting_policy),
            ):
                widget.setCurrentIndex(widget.findData(value))
            for toggle, checked in (
                (strict, rule.strict_focus),
                (multiline, rule.multiline_requires_confirmation),
                (unicode, rule.unicode_preference),
                (automatic, rule.allow_auto_insert),
            ):
                toggle.setChecked(checked)

        defaults = AppRule("application.exe")
        populate(previous or defaults)
        controls = QHBoxLayout()
        self._button("По умолчанию", lambda: populate(defaults), controls)
        self._button("Отмена", dialog.reject, controls)
        self._button("Сохранить", dialog.accept, controls)
        form.addRow(controls)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            updated = replace(
                previous or defaults,
                executable=exe.text().strip(),
                input_strategy=strategy.currentData(),
                clipboard_restore_policy=restore.currentData(),
                formatting_policy=formatting.currentData(),
                strict_focus=strict.isChecked(),
                multiline_requires_confirmation=multiline.isChecked(),
                unicode_preference=unicode.isChecked(),
                allow_auto_insert=automatic.isChecked(),
            )
        except ValueError:
            self.feedback.setText("Укажите подходящее приложение.")
            return
        rules = list(profile.app_rules)
        if rule_index >= 0:
            rules[rule_index] = updated
        else:
            rules.append(updated)
        if len(rules) > 100:
            self.feedback.setText("Допускается не более ста правил на профиль.")
            return

        def save() -> str:
            self.profiles.save(replace(profile, app_rules=tuple(rules)))
            return profile.id

        self._persist(save)

    def _save(self) -> None:
        profile = self._selected()
        if profile is None or profile.id in BUILTIN_IDS:
            return
        missing = "" if self.name.text().strip() else "Укажите название профиля"
        self.name.set_invalid(missing)
        if missing:
            self.feedback.setText(missing)
            self.name.setFocus()
            return
        try:
            rules = []
            for line in self.rules.toPlainText().splitlines():
                if line.strip():
                    parts = [part.strip() for part in line.split("|")]
                    if len(parts) != 4 or parts[3] not in ("0", "1"):
                        raise ValueError("Для правила нужны четыре поля; последнее 0 или 1")
                    previous = next(
                        (
                            rule
                            for rule in profile.app_rules
                            if rule.executable.casefold() == parts[0].casefold()
                            and rule.title_glob == parts[1]
                        ),
                        None,
                    )
                    if previous is None:
                        rules.append(AppRule(parts[0], parts[1], int(parts[2]), parts[3] == "1"))
                    else:
                        rules.append(
                            replace(previous, priority=int(parts[2]), enabled=parts[3] == "1")
                        )
            updated = replace(
                profile,
                name=self.name.text().strip(),
                language=self.language.currentData(),
                model_id=self.model.currentData(),
                formatting_mode=self.formatting.currentData(),
                insertion_mode=self.insertion.currentData(),
                enabled=self.enabled.isChecked(),
                vocabulary_id=self.vocabulary_id.text().strip(),
                command_set_id=self.command_set_id.text().strip(),
                snippet_set_id=self.snippet_set_id.text().strip(),
                app_rules=tuple(rules),
            )
        except ValueError as exc:
            self.feedback.setText(str(exc))
            return
        self._persist(lambda: self._store(updated))

    def _store(self, profile: DictationProfile) -> str:
        self.profiles.save(profile)
        return profile.id

    def _create(self, copy: bool) -> None:
        from PySide6.QtWidgets import QInputDialog

        source = self._selected() if copy else None
        name, accepted = QInputDialog.getText(self, "Новый профиль", "Название")
        if accepted:
            self._persist(lambda: self.profiles.create(name, source).id)

    def _remove(self) -> None:
        profile = self._selected()
        if profile is None or profile.id in BUILTIN_IDS:
            return
        if (
            QMessageBox.question(self, "Удаление профиля", "Удалить выбранный профиль?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._persist(lambda: self._delete(profile.id))

    def _delete(self, profile_id: str) -> str:
        self.profiles.remove(profile_id)
        return self.profiles.default_id

    def _default(self) -> None:
        profile = self._selected()
        if profile is not None:
            self.set_default_profile(profile.id)

    def set_default_profile(self, profile_id: str) -> None:
        profile = next((item for item in self.profiles.profiles() if item.id == profile_id), None)
        if profile is None or not self.controller.profile_allowed(profile):
            self.feedback.setText("Профиль недоступен или запрещён политикой")
            return
        if self.tabs.isEnabled():
            self._persist(lambda: self._set_default(profile_id))

    def _set_default(self, profile_id: str) -> str:
        self.profiles.set_default(profile_id)
        return profile_id

    def open_collection(self, key: str) -> None:
        if key in self.collection_pages:
            self.tabs.setCurrentWidget(self.collection_pages[key])

    def open_section(self, key: str) -> bool:
        if key in self.collection_pages:
            self.open_collection(key)
        elif key == "profiles":
            self.tabs.setCurrentIndex(0)
        elif key == "files" and self.transcription_panel is not None:
            self.tabs.setCurrentWidget(self.transcription_panel)
        elif key == "workflows":
            self.tabs.setCurrentWidget(self.workflow_page)
        else:
            return False
        return True

    def section_action(self, key: str) -> tuple[str, Callable[[], None]] | None:
        if key == "profiles":
            return "Новый профиль", lambda: self._create(False)
        if key == "dictionary":
            return "Добавить термин", lambda: self._edit_collection_item("dictionary", new=True)
        if key == "snippets":
            return "Новый шаблон", lambda: self._edit_collection_item("snippets", new=True)
        if key == "files" and self.transcription_panel is not None:
            return "Добавить файлы", self.transcription_panel.add_button.click
        return None

    def _load_visible_collection(self, index: int) -> None:
        page = self.tabs.widget(index)
        for key, candidate in self.collection_pages.items():
            if page is candidate and key not in self.collection_editors:
                self._build_collection(key, candidate)
                return

    def _build_collection(self, key: str, page: QWidget) -> None:
        layout = QVBoxLayout(page)
        editor = QPlainTextEdit(page)
        editor.setPlainText(self.controller.professional_content.export_json(key))
        self.collection_editors[key] = editor
        if key in {"dictionary", "snippets"}:
            editor.hide()
            search = VoxSearchField("Поиск…" if key == "snippets" else "Поиск по терминам…", page)
            table = RecordsView(
                ("Произнесённая форма", "Написание", "Язык", "Профиль", "Статус")
                if key == "dictionary"
                else ("Шаблон", "Фраза", "Категория", "Статус"),
                page,
            )
            self.collection_tables[key] = table
            layout.addWidget(search)
            pane = QStackedWidget(page)
            pane.addWidget(table)
            empty = VoxEmptyState(
                "dictionary" if key == "dictionary" else "snippets",
                "Нет записей",
                "Добавьте первую запись или импортируйте коллекцию.",
                pane,
            )
            empty.action.setText("Добавить запись")
            empty.action.clicked.connect(lambda: self._edit_collection_item(key, new=True))
            empty.action.show()
            pane.addWidget(empty)
            if key == "snippets":
                self.snippet_splitter = QSplitter(page)
                self.snippet_splitter.addWidget(pane)
                self.snippet_splitter.addWidget(self._snippet_editor(page))
                self.snippet_splitter.setStretchFactor(1, 2)
                layout.addWidget(self.snippet_splitter, 1)
                table.selectionModel().currentChanged.connect(
                    lambda *_args: self._load_snippet_selection()
                )
            else:
                layout.removeWidget(search)
                self.dictionary_splitter = QSplitter(page)
                filters = QWidget(self.dictionary_splitter)
                filter_layout = QVBoxLayout(filters)
                filter_layout.addWidget(search)
                categories = VoxComboBox(page)
                for caption, language in (("Все языки", ""), ("Русский", "ru"), ("English", "en")):
                    categories.addItem(caption, language)
                categories.setAccessibleName("Категории словаря по языку")
                filter_layout.addWidget(categories)
                filter_layout.addStretch()
                self.dictionary_splitter.addWidget(filters)
                self.dictionary_splitter.addWidget(pane)
                self.dictionary_splitter.setStretchFactor(1, 3)
                layout.addWidget(self.dictionary_splitter, 1)
                categories.currentIndexChanged.connect(
                    lambda _index: self._refresh_collection_table("dictionary")
                )
                self.dictionary_language = categories
            search.textChanged.connect(table.proxy.setFilterFixedString)
            table.doubleClicked.connect(lambda _item: self._edit_collection_item(key, new=False))

            def refresh() -> None:
                self._refresh_collection_table(key)
                pane.setCurrentWidget(table if table.proxy.rowCount() else empty)

            editor.textChanged.connect(refresh)
            table.proxy.modelReset.connect(
                lambda: pane.setCurrentWidget(table if table.proxy.rowCount() else empty)
            )
            table.proxy.rowsInserted.connect(lambda *_args: pane.setCurrentWidget(table))
            table.proxy.rowsRemoved.connect(
                lambda *_args: pane.setCurrentWidget(table if table.proxy.rowCount() else empty)
            )
            refresh()
        else:
            layout.addWidget(editor)
        row = QHBoxLayout()
        if key == "commands":
            self._button("Добавить", lambda: self._edit_command(new=True), row)
            self._button("Изменить", lambda: self._edit_command(new=False), row)
            self._button("Удалить", self._delete_command, row)
            self._button("Активация…", self._edit_processing_options, row)
        else:
            self._button("Добавить запись", lambda: self._edit_collection_item(key, new=True), row)
            self._button("Изменить", lambda: self._edit_collection_item(key, new=False), row)
        if key == "dictionary":
            self._button("Размер подсказки…", self._edit_processing_options, row)
            clear = self._button("Очистить словарь", self._clear_dictionary, row)
            clear.setProperty("danger", True)
        self._button("Сохранить", lambda: self._commit_collection(key), row)
        self._button("Импорт", lambda: self._import_collection(key), row)
        self._button("Экспорт", lambda: self._export_collection(key), row)
        if key == "replacements":
            self._button("Предпросмотр", lambda: self._preview_collection(key), row)
        layout.addLayout(row)

    def _clear_dictionary(self) -> None:
        if not confirm_delete(
            self,
            "Очистить словарь?",
            "Удалить все термины и варианты произношения из этой коллекции?",
        ):
            return
        self.collection_editors["dictionary"].setPlainText(
            '{"schema_version": 1, "dictionary": []}'
        )
        self._commit_collection("dictionary")

    def _snippet_editor(self, parent: QWidget) -> QWidget:
        panel = QWidget(parent)
        layout = QVBoxLayout(panel)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.snippet_name = VoxTextField(panel)
        self.snippet_trigger = VoxTextField(panel)
        self.snippet_category = VoxTextField(panel)
        form.addRow("Название", self.snippet_name)
        form.addRow("Фраза вызова", self.snippet_trigger)
        form.addRow("Категория", self.snippet_category)
        layout.addLayout(form)
        self.snippet_body = QPlainTextEdit(panel)
        self.snippet_body.setAccessibleName("Содержимое шаблона")
        layout.addWidget(self.snippet_body, 1)
        row = QHBoxLayout()
        fields = VoxComboBox(panel)
        for value in ("{date}", "{time}", "{input:client_name}"):
            fields.addItem(value)
        row.addWidget(fields)
        self._button(
            "Добавить поле", lambda: self.snippet_body.insertPlainText(fields.currentText()), row
        )
        self._button("Предпросмотр", self._preview_snippet_editor, row)
        layout.addLayout(row)
        row = QHBoxLayout()
        self._button("Сохранить", lambda: self._store_snippet_editor("save"), row)
        self._button("Дублировать", lambda: self._store_snippet_editor("copy"), row)
        self._button("Удалить", lambda: self._store_snippet_editor("delete"), row)
        layout.addLayout(row)
        return panel

    def _snippet_document(self) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            json.loads(self.collection_editors["snippets"].toPlainText())["snippets"],
        )

    def _load_snippet_selection(self) -> None:
        selected = self.collection_tables["snippets"].selected_key()
        if not selected:
            return
        snippets = self._snippet_document()
        index = int(selected)
        if index >= len(snippets):
            return
        item = snippets[index]
        self.snippet_name.setText(item["name"])
        self.snippet_trigger.setText(item.get("trigger_phrase", ""))
        self.snippet_category.setText(item.get("category", ""))
        self.snippet_body.setPlainText(item["content"])

    def _snippet_editor_data(self, existing: dict[str, Any]) -> dict[str, Any]:
        return {
            **existing,
            "name": self.snippet_name.text().strip(),
            "trigger_phrase": self.snippet_trigger.text().strip(),
            "category": self.snippet_category.text().strip(),
            "content": self.snippet_body.toPlainText(),
        }

    def _preview_snippet_editor(self) -> None:
        entry = Snippet(**self._snippet_editor_data({"id": "preview"}))
        preview = self.render_snippet(entry)
        if (
            preview is not None
            and QMessageBox.question(
                self,
                "Вставить шаблон?",
                "Отправить проверенный текст в целевое окно?",
                defaultButton=QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.insertion_requested.emit(preview)

    def _store_snippet_editor(self, action: str) -> None:
        rows = self._snippet_document()
        current = self.collection_tables["snippets"].selected_key()
        index = int(current) if current else None
        if action == "delete":
            if (
                index is None
                or QMessageBox.question(
                    self,
                    "Удалить шаблон?",
                    rows[index]["name"],
                    defaultButton=QMessageBox.StandardButton.No,
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
            rows.pop(index)
        elif index is not None and action == "save":
            rows[index] = self._snippet_editor_data(rows[index])
        else:
            original = rows[index] if index is not None else {}
            item = self._snippet_editor_data({**original, "id": uuid4().hex})
            if action == "copy":
                item["name"] += " — копия"
            rows.append(item)
        try:
            self.controller.professional_content.parse("snippets", rows)
        except (ValueError, TypeError) as exc:
            self.feedback.setText(str(exc))
            return
        self.collection_editors["snippets"].setPlainText(
            json.dumps({"schema_version": 1, "snippets": rows}, ensure_ascii=False, indent=2)
        )
        self._commit_collection("snippets")

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        orientation = Qt.Orientation.Vertical if self.width() < 650 else Qt.Orientation.Horizontal
        for name in ("profile_splitter", "snippet_splitter", "dictionary_splitter"):
            splitter = getattr(self, name, None)
            if splitter is not None and splitter.orientation() != orientation:
                splitter.setOrientation(orientation)

    def _refresh_collection_table(self, key: str) -> None:
        table = self.collection_tables[key]
        try:
            data = json.loads(self.collection_editors[key].toPlainText())
            entries = self.controller.professional_content.parse(key, data[key])
        except (ValueError, KeyError, TypeError):
            return
        rows = []
        for index, item in enumerate(entries):
            values: tuple[str, ...]
            status = "Включено" if item.enabled else "Выключено"
            if key == "dictionary":
                selected = self.dictionary_language.currentData()
                if selected and item.language and item.language != selected:
                    continue
                values = (
                    ", ".join(item.spoken_variants),
                    item.canonical_text,
                    item.language or "Все",
                    item.profile_id or "Все",
                    status,
                )
            else:
                values = (item.name, item.trigger_phrase, item.category or "—", status)
            rows.append((str(index), values))
        table.records.replace(rows)

    def _edit_processing_options(self) -> None:
        settings = self.controller.professional_content.snapshot()
        dialog = VoxDialog(self)
        dialog.setWindowTitle("Параметры обработки")
        form = QFormLayout(dialog)
        activation = QLineEdit(settings.activation, dialog)
        activation.setMaxLength(64)
        if self.controller.policy.allow_voice_commands:
            form.addRow("Фраза активации команд", activation)
        budget = QSpinBox(dialog)
        budget.setRange(0, MAX_HINT_CHARACTERS)
        budget.setValue(settings.prompt_budget)
        form.addRow("Лимит символов словаря для ASR", budget)
        row = QHBoxLayout()
        self._button("Сохранить", dialog.accept, row)
        self._button("Отмена", dialog.reject, row)
        form.addRow(row)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            value, limit = activation.text(), budget.value()

            def save() -> str:
                self.controller.professional_content.save_options(value, limit)
                return self.profiles.default_id

            self._persist(save)

    def _command_document(self) -> dict[str, Any] | None:
        editor = self.collection_editors["commands"]
        try:
            data = json.loads(editor.toPlainText())
            if not isinstance(data, dict) or data.get("schema_version") != 1:
                raise ValueError("Схема команды должна быть версии 1")
            self.controller.professional_content.parse("commands", data["commands"])
            return data
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self.feedback.setText(str(exc))
            return None

    def _choose_command(self, entries: list[dict[str, Any]]) -> int | None:
        if not entries:
            self.feedback.setText("Список команд пуст")
            return None
        labels = [f"{item['trigger']} · {item['id']}" for item in entries]
        label, selected = QInputDialog.getItem(
            self, "Голосовая команда", "Команда", labels, editable=False
        )
        return labels.index(label) if selected else None

    def _edit_command(self, *, new: bool) -> None:
        data = self._command_document()
        if data is None:
            return
        entries = data["commands"]
        index = None if new else self._choose_command(entries)
        if not new and index is None:
            return
        entry: dict[str, Any] = (
            entries[index]
            if index is not None
            else {
                "id": uuid4().hex,
                "action_type": "InsertLiteralText",
                "payload": {},
            }
        )
        dialog = VoxDialog(self)
        dialog.setWindowTitle("Голосовая команда")
        form = QFormLayout(dialog)
        trigger = QLineEdit(entry.get("trigger", ""), dialog)
        command_set = QLineEdit(entry.get("command_set_id", ""), dialog)
        action = QComboBox(dialog)
        action.addItems(VOICE_ACTION_TYPES)
        action.setCurrentText(entry["action_type"])
        parameter = QPlainTextEdit(dialog)
        parameter.setPlainText(next(iter(entry["payload"].values()), ""))
        panel = QComboBox(dialog)
        panel.addItems(("settings", "dictionary", "snippets", "commands", "replacements"))
        panel.setCurrentText(entry["payload"].get("panel", "settings"))
        enabled = QCheckBox(dialog)
        enabled.setChecked(bool(entry.get("enabled", True)))
        confirm = QCheckBox(dialog)
        confirm.setChecked(entry.get("confirmation_required", False))
        for label, widget in (
            ("Фраза", trigger),
            ("ID набора", command_set),
            ("Действие", action),
            ("Имя или текст", parameter),
            ("Панель VoxType", panel),
            ("Включена", enabled),
            ("С запросом подтверждения", confirm),
        ):
            form.addRow(label, widget)

        def refresh_fields() -> None:
            key = ACTION_PARAMETERS.get(action.currentText())
            panel.setEnabled(key == "panel")
            parameter.setEnabled(key in {"name", "text"})

        action.currentTextChanged.connect(refresh_fields)
        refresh_fields()
        row = QHBoxLayout()
        self._button("Применить", dialog.accept, row)
        self._button("Отмена", dialog.reject, row)
        form.addRow(row)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        kind = action.currentText()
        key = ACTION_PARAMETERS.get(kind)
        value = panel.currentText() if key == "panel" else parameter.toPlainText()
        updated = {
            "id": entry["id"],
            "trigger": trigger.text().strip(),
            "action_type": kind,
            "payload": {key: value} if key else {},
            "command_set_id": command_set.text().strip(),
            "enabled": enabled.isChecked(),
            "confirmation_required": confirm.isChecked(),
        }
        if index is None:
            entries.append(updated)
        else:
            entries[index] = updated
        try:
            self.controller.professional_content.parse("commands", entries)
        except (ValueError, TypeError) as exc:
            self.feedback.setText(str(exc))
            return
        self.collection_editors["commands"].setPlainText(
            json.dumps(data, ensure_ascii=False, indent=2)
        )
        self._commit_collection("commands")

    def _delete_command(self) -> None:
        data = self._command_document()
        if data is None:
            return
        entries = data["commands"]
        index = self._choose_command(entries)
        if (
            index is not None
            and QMessageBox.question(
                self,
                "Удалить голосовую команду?",
                entries[index]["trigger"],
                defaultButton=QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            del entries[index]
            self.collection_editors["commands"].setPlainText(
                json.dumps(data, ensure_ascii=False, indent=2)
            )
            self._commit_collection("commands")

    def _edit_collection_item(self, key: str, *, new: bool) -> None:
        editor = self.collection_editors[key]
        try:
            data = json.loads(editor.toPlainText())
            entries = [
                asdict(item) for item in self.controller.professional_content.parse(key, data[key])
            ]
        except (ValueError, TypeError, KeyError, AttributeError):
            self.feedback.setText("Исправьте JSON в редакторе")
            return
        fields = {
            "dictionary": (
                ("canonical_text", "Написание"),
                ("spoken_variants", "Произношения, по одному на строку"),
                ("language", "Язык: ru или en"),
                ("profile_id", "ID профиля"),
                ("executable", "Имя или путь EXE"),
                ("vocabulary_id", "ID словаря"),
            ),
            "snippets": (
                ("name", "Название"),
                ("trigger_phrase", "Фраза"),
                ("content", "Текст"),
                ("category", "Категория"),
                ("profile_id", "ID профиля"),
                ("snippet_set_id", "ID набора"),
            ),
            "replacements": (
                ("pattern", "Что заменить"),
                ("replacement", "На что заменить"),
                ("scope", "Область: ID профиля, app:EXE или lang:ru"),
            ),
        }[key]
        index = None
        if not new:
            table = self.collection_tables.get(key)
            marked = table.selected_key() if table is not None else ""
            labels = [f"{row + 1}. {entry[fields[0][0]]}" for row, entry in enumerate(entries)]
            if not labels:
                return
            if marked:
                index = int(marked)
            else:
                label, accepted = QInputDialog.getItem(
                    self, "Выберите запись", "Запись", labels, editable=False
                )
                if not accepted:
                    return
                index = labels.index(label)
        entry = (
            entries[index]
            if index is not None
            else ({} if key == "dictionary" else {"id": uuid4().hex})
        )
        dialog = VoxDialog(self)
        dialog.setWindowTitle(
            {
                "dictionary": "Словарь",
                "snippets": "Шаблон AutoText",
                "replacements": "Правило замены",
            }[key]
        )
        form = QFormLayout(dialog)
        if key == "dictionary" and entry.get("origin") == "organization":
            form.addRow("Источник", QLabel("Локальный пакет организации", dialog))
        widgets: dict[str, QLineEdit | QPlainTextEdit] = {}
        for field, label in fields:
            widget: QLineEdit | QPlainTextEdit
            value = entry.get(field, "")
            if field in {"spoken_variants", "content", "replacement"}:
                widget = QPlainTextEdit(dialog)
                widget.setPlainText("\n".join(value) if isinstance(value, (tuple, list)) else value)
                if field == "content":
                    widget.setPlaceholderText("{date}, {time}, {input:client_name}")
            else:
                widget = QLineEdit(value, dialog)
            widgets[field] = widget
            form.addRow(label, widget)
        enabled = QCheckBox(dialog)
        enabled.setChecked(bool(entry.get("enabled", True)))
        form.addRow("Включена", enabled)
        priority = QSpinBox(dialog)
        priority.setRange(-10000, 10000)
        priority.setValue(int(entry.get("priority", 0)))
        if key != "snippets":
            form.addRow("Приоритет", priority)
        match_mode = QComboBox(dialog)
        match_mode.addItems(("word", "case_sensitive", "literal"))
        match_mode.setCurrentText(entry.get("match_mode", "word"))
        case_policy = QComboBox(dialog)
        case_policy.addItems(("canonical", "preserve"))
        case_policy.setCurrentText(entry.get("case_policy", "canonical"))
        case_sensitive, whole_word = QCheckBox(dialog), QCheckBox(dialog)
        case_sensitive.setChecked(bool(entry.get("case_sensitive", False)))
        whole_word.setChecked(bool(entry.get("whole_word", True)))
        if key == "dictionary":
            form.addRow("Сопоставление", match_mode)
            form.addRow("Регистр результата", case_policy)
        elif key == "replacements":
            form.addRow("Учитывать регистр", case_sensitive)
            form.addRow("Только целые слова", whole_word)
        row = QHBoxLayout()
        self._button("Применить", dialog.accept, row)
        self._button("Отмена", dialog.reject, row)
        deleted = False

        def remove() -> None:
            nonlocal deleted
            deleted = True
            dialog.accept()

        if index is not None:
            self._button("Удалить", remove, row)
        form.addRow(row)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if deleted and index is not None:
            del entries[index]
        else:
            updated = {**entry, "enabled": enabled.isChecked()}
            for field, widget in widgets.items():
                updated[field] = (
                    widget.toPlainText()
                    if isinstance(widget, QPlainTextEdit)
                    else widget.text().strip()
                )
            if key == "dictionary":
                updated.update(
                    spoken_variants=tuple(
                        part.strip()
                        for part in updated["spoken_variants"].splitlines()
                        if part.strip()
                    ),
                    priority=priority.value(),
                    match_mode=match_mode.currentText(),
                    case_policy=case_policy.currentText(),
                )
            elif key == "replacements":
                updated.update(
                    match_type="literal",
                    priority=priority.value(),
                    case_sensitive=case_sensitive.isChecked(),
                    whole_word=whole_word.isChecked(),
                )
            if index is None:
                entries.append(updated)
            else:
                entries[index] = updated
        try:
            self.controller.professional_content.parse(key, entries)
        except (TypeError, ValueError) as exc:
            self.feedback.setText(str(exc))
            return
        data[key] = entries
        editor.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))
        self._commit_collection(key)

    def _commit_collection(self, key: str) -> None:
        data = self.collection_editors[key].toPlainText()
        selected = self._selected()
        profile_id = selected.id if selected is not None else self.profiles.default_id

        def save() -> str:
            try:
                self.controller.professional_content.save(key, data)
            except (TypeError, KeyError, AttributeError) as exc:
                raise ValueError("Проверьте типы полей коллекции") from exc
            return profile_id

        self._persist(save)

    def _import_collection(self, key: str) -> None:
        suffixes = "JSON (*.json);;CSV (*.csv)" if key == "dictionary" else "JSON (*.json)"
        path, _ = QFileDialog.getOpenFileName(self, "Открыть коллекцию", "", suffixes)
        if not path:
            return
        try:
            source = Path(path)
            if source.stat().st_size > 1_000_000:
                raise ValueError("Файл коллекции слишком большой")
            data = source.read_text(encoding="utf-8-sig")
            if source.suffix.casefold() == ".csv" and key == "dictionary":
                data = self.controller.professional_content.dictionary_from_csv(data)
            document = json.loads(data)
            if not isinstance(document, dict) or document.get("schema_version") != 1:
                raise ValueError("Ожидается схема версии 1")
            self.controller.professional_content.parse(key, document[key])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.feedback.setText("Не удалось прочитать коллекцию")
            return
        self.collection_editors[key].setPlainText(data)
        self.feedback.setText("Проверьте импортированные записи и нажмите «Сохранить»")

    def _export_collection(self, key: str) -> None:
        filters = "JSON (*.json);;CSV (*.csv)" if key == "dictionary" else "JSON (*.json)"
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Экспорт коллекции", key + ".json", filters
        )
        if not path:
            return
        try:
            if key == "dictionary" and (
                Path(path).suffix.casefold() == ".csv" or selected_filter.startswith("CSV")
            ):
                data = self.controller.professional_content.dictionary_to_csv()
            else:
                data = self.controller.professional_content.export_json(key)
            Path(path).write_text(data, encoding="utf-8")
        except OSError:
            self.feedback.setText("Не удалось сохранить экспорт")

    def _preview_collection(self, key: str) -> None:
        profile = self._selected()
        if profile is None:
            return
        try:
            document = json.loads(self.collection_editors[key].toPlainText())
            items = self.controller.professional_content.parse(key, document[key])
        except (ValueError, KeyError, TypeError, AttributeError):
            self.feedback.setText("Исправьте данные перед предпросмотром")
            return
        if key == "snippets":
            names = [f"{item.name} ({item.id})" for item in items]
            name, accepted = QInputDialog.getItem(
                self, "Шаблон", "Выберите шаблон", names, editable=False
            )
            if accepted and name in names:
                text = self.render_snippet(items[names.index(name)])
                if text is not None:
                    self.insertion_requested.emit(text)
            return
        original, accepted = QInputDialog.getMultiLineText(self, "Предпросмотр замен", "До")
        if accepted:
            pipeline = replace(
                self.controller.professional_content.snapshot(), replacements=items, dictionary=()
            )
            profile = replace(profile, formatting_mode="verbatim")
            request = RecognitionRequest(
                0, profile.language or "ru", profile.id, "", DictationContext(), (), None
            )
            result = pipeline.process(request, original, profile, allow_commands=False)
            result_box = VoxDialog(self)
            result_box.setWindowTitle("После")
            layout = QVBoxLayout(result_box)
            value = QPlainTextEdit(result.final_text, result_box)
            value.setReadOnly(True)
            layout.addWidget(value)
            result_box.exec()

    def render_snippet(self, snippet: Snippet) -> str | None:
        fields = {}
        for key in snippet.input_names():
            value, accepted = QInputDialog.getText(self, snippet.name, key)
            if not accepted:
                return None
            fields[key] = value
        rendered = snippet.render(fields)
        text, accepted = QInputDialog.getMultiLineText(
            self, "Подтвердить вставку", "Текст", rendered
        )
        return text if accepted and text else None

    def _add_text_editor(self) -> None:
        page = QWidget(self)
        self.tabs.addTab(page, "Редактор текста")
        self._editor_page = page
        self._correction_origin: RecognitionResult | None = None
        self._correction_baseline = ""
        self._editor_draft_id: str | None = None
        self._revisions = TranscriptRevisions()
        self._voice_edit_anchor: tuple[str, int] | None = None
        self._draft_timer = QTimer(self, interval=1000, singleShot=True)
        self._draft_timer.timeout.connect(self.flush_editor_draft)
        self._corrections = CorrectionSuggestionService(
            self.controller.settings_repository,
            self.controller.professional_content,
        )
        self.correction_ready.connect(self._offer_correction, Qt.ConnectionType.QueuedConnection)
        layout = QVBoxLayout(page)
        self.editor = TextEditService()
        self._selection: SelectedText | None = None
        self._preview_source: str | None = None
        self._preview_result: str | None = None
        self.edit_original = QPlainTextEdit(page)
        self.edit_original.setPlaceholderText("Вставьте текст сюда или запросите выделение")
        self.edit_original.textChanged.connect(self._debounce_editor_draft)
        self.edit_preview = QPlainTextEdit(page)
        self.edit_preview.setPlaceholderText("Предпросмотр")
        self.edit_preview.setReadOnly(True)
        self.edit_operation = QComboBox(page)
        self.edit_operation.addItems(
            (
                "Uppercase",
                "Lowercase",
                "Sentence case",
                "Remove duplicate spaces",
                "Replace literal phrase",
                "Insert bullet list formatting",
                "Normalize line breaks",
            )
        )
        self.edit_source = QLineEdit(page)
        self.edit_source.setPlaceholderText("Исходная фраза для замены")
        self.edit_replacement = QLineEdit(page)
        self.edit_replacement.setPlaceholderText("Новая фраза")
        for widget in (
            self.edit_operation,
            self.edit_source,
            self.edit_replacement,
            self.edit_original,
            self.edit_preview,
        ):
            layout.addWidget(widget)
        row = QHBoxLayout()
        selection_button = self._button("Прочитать выделение", self._read_selection, row)
        selection = CapabilityRegistry.snapshot(self.controller)["selection_editing"]
        selection_button.setEnabled(selection.available)
        selection_button.setToolTip(selection.reason)
        self._button("Предпросмотр", self._preview_edit, row)
        self._button("Применить локально", self._apply_edit, row)
        self._button("Отменить предпросмотр", self._clear_preview, row)
        self._button("Отменить правку", self._undo_edit, row)
        self.suggest_button = self._button(
            "Учесть исправление в словаре", self._suggest_correction, row
        )
        self.suggest_button.setEnabled(False)
        layout.addLayout(row)
        scratchpad = QHBoxLayout()
        self.scratchpad_button = self._button(
            "Диктовать в блокнот",
            self.scratchpad_toggle_requested.emit,
            scratchpad,
        )
        self.voice_edit_button = self._button(
            "Голосовая правка", self.voice_edit_toggle_requested.emit, scratchpad
        )
        self.voice_edit_button.setEnabled(self.controller.policy.allow_voice_commands)
        self.voice_edit_button.setToolTip("Например: «удали последнее слово»")
        self._button("Версии", self._show_editor_versions, scratchpad)
        self._button(
            "Скопировать",
            lambda: (
                self.edit_original.copy()
                if self.edit_original.textCursor().hasSelection()
                else self._copy_scratchpad()
            ),
            scratchpad,
        )
        self._button("Сохранить черновик", self._save_scratchpad, scratchpad)
        self._button("Экспорт TXT", self._export_scratchpad, scratchpad)
        layout.addLayout(scratchpad)
        row = QHBoxLayout()
        self._button("Заменить выделение", self._replace_selection, row).setEnabled(
            selection.available
        )
        layout.addLayout(row)

    def open_scratchpad(self) -> None:
        self.tabs.setCurrentWidget(self._editor_page)
        self.edit_original.setFocus()

    def set_scratchpad_recording(self, recording: bool, *, voice_edit: bool = False) -> None:
        self.scratchpad_button.setText(
            "Остановить диктовку" if recording and not voice_edit else "Диктовать в блокнот"
        )
        self.scratchpad_button.setEnabled(not recording or not voice_edit)
        self.voice_edit_button.setText(
            "Завершить голосовую правку" if recording and voice_edit else "Голосовая правка"
        )
        self.voice_edit_button.setEnabled(
            self.controller.policy.allow_voice_commands and (not recording or voice_edit)
        )

    def prepare_voice_edit(self) -> None:
        self._record_editor_text()
        self._voice_edit_anchor = self._revisions.id, self._revisions.number

    def apply_voice_edit(self, result: RecognitionResult) -> None:
        anchor, self._voice_edit_anchor = self._voice_edit_anchor, None
        if anchor is None or not self.controller.policy.allow_voice_commands:
            return
        self._record_editor_text()
        try:
            request = VoiceEditCommandInterpreter.parse(result.raw_text, *anchor)
            text = VoiceEditCommandInterpreter.apply(
                request, self._revisions, self.edit_original.toPlainText()
            )
        except ValueError as exc:
            self.feedback.setText(str(exc))
            return
        if request.operation == "open":
            self.open_scratchpad()
            return
        self._write_editor_revision(
            text,
            RevisionSource.RESTORED if request.operation == "undo" else RevisionSource.VOICE_EDIT,
        )
        self.feedback.setText("Голосовая правка применена в блокноте")

    def _record_editor_text(self) -> None:
        self._revisions.append(self.edit_original.toPlainText(), RevisionSource.USER_EDIT)

    def _write_editor_revision(self, text: str, source: RevisionSource) -> None:
        self._clear_preview()
        self._selection = None
        self._revisions.append(text, source)
        self.edit_original.setPlainText(text)

    def _show_editor_versions(self) -> None:
        self._record_editor_text()
        history = tuple(reversed(self._revisions.entries))
        dialog = VoxDialog(self)
        dialog.setWindowTitle("Версии текста")
        dialog.resize(610, 430)
        layout = QVBoxLayout(dialog)
        versions = QListWidget(dialog)
        preview = QPlainTextEdit(dialog, readOnly=True)
        layout.addWidget(versions)
        layout.addWidget(preview, 1)
        for version in history:
            versions.addItem(
                f"{version.number} · {version.created_at_utc[:19]} UTC · {version.source}"
            )

        def load(row: int) -> None:
            preview.setPlainText(history[row].text if row >= 0 else "")

        def restore() -> None:
            if versions.currentRow() >= 0:
                self._write_editor_revision(
                    history[versions.currentRow()].text, RevisionSource.RESTORED
                )
                dialog.accept()

        versions.currentRowChanged.connect(load)
        versions.setCurrentRow(0)
        row = QHBoxLayout()
        self._button("Скопировать версию", lambda: self._copy_text(preview.toPlainText()), row)
        self._button("Восстановить версию", restore, row)
        layout.addLayout(row)
        dialog.exec()

    def append_scratchpad_result(self, result: RecognitionResult) -> None:
        if not result.text.strip():
            return
        self._clear_preview()
        self._selection = None
        self._editor_draft_id = self._editor_draft_id or self._revisions.id
        previous = self.edit_original.toPlainText()
        if not previous and not self._revisions.entries:
            self._revisions.append(result.raw_text, RevisionSource.RAW_ASR)
        self._write_editor_revision(
            f"{previous}\n{result.text}" if previous else result.text, RevisionSource.FORMATTED
        )
        self._correction_origin = result
        self._correction_baseline = self.edit_original.toPlainText()
        self.suggest_button.setEnabled(True)

    def _copy_scratchpad(self) -> None:
        self._copy_text(self.edit_original.toPlainText())

    @staticmethod
    def _copy_text(text: str) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(text)

    def _save_scratchpad(self) -> None:
        if (
            self.controller.policy.disable_history
            or not self.controller.settings.privacy.recover_unsaved_drafts
        ):
            self.feedback.setText("Включите восстановление черновиков, чтобы сохранять этот текст.")
            return
        self._editor_draft_id = self._editor_draft_id or self._revisions.id
        self.flush_editor_draft()

    def _export_scratchpad(self) -> None:
        text = self.edit_original.toPlainText()
        if not text.strip():
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Экспорт блокнота", "VoxType.txt", "Текст (*.txt)"
        )
        if filename:
            target = QSaveFile(filename)
            payload = text.encode("utf-8")
            if (
                not target.open(QIODevice.OpenModeFlag.WriteOnly)
                or target.write(payload) != len(payload)
                or not target.commit()
            ):
                self.feedback.setText("Не удалось сохранить файл.")

    def open_recognition_editor(
        self,
        result: RecognitionResult,
        *,
        edited_text: str | None = None,
    ) -> None:
        if self._draft_timer.isActive():
            self.flush_editor_draft()
        self._correction_origin = None
        self._selection = None
        self._clear_preview()
        self._correction_baseline = result.text or result.raw_text
        self._revisions = TranscriptRevisions()
        if result.raw_text:
            self._revisions.append(result.raw_text, RevisionSource.RAW_ASR)
        self._write_editor_revision(self._correction_baseline, RevisionSource.FORMATTED)
        if edited_text is not None:
            self._write_editor_revision(edited_text, RevisionSource.USER_EDIT)
        self._correction_origin = result
        self._editor_draft_id = self._revisions.id
        self.suggest_button.setEnabled(True)
        self.tabs.setCurrentWidget(self._editor_page)
        if edited_text is not None and edited_text != self._correction_baseline:
            self._debounce_editor_draft()

    def open_restored_draft(
        self, identifier: str, text: str, versions: tuple[TranscriptRevision, ...]
    ) -> None:
        if self._draft_timer.isActive():
            self.flush_editor_draft()
        self._correction_origin = None
        self._editor_draft_id = identifier
        self._selection = None
        self._clear_preview()
        self._revisions = TranscriptRevisions(identifier, list(versions))
        self._write_editor_revision(text, RevisionSource.RESTORED)
        self.suggest_button.setEnabled(False)
        self.tabs.setCurrentWidget(self._editor_page)

    def _debounce_editor_draft(self) -> None:
        self._draft_timer.start()

    def flush_editor_draft(self) -> None:
        self._draft_timer.stop()
        result = self._correction_origin
        text = self.edit_original.toPlainText()
        self._record_editor_text()
        if (
            self._editor_draft_id is not None
            and (text.strip() or len(self._revisions.entries) > 1)
            and self.controller.settings.privacy.recover_unsaved_drafts
            and not self.controller.policy.disable_history
        ):
            language = (
                result.language if result is not None else self.controller.snapshot().language
            )
            versions = tuple(
                revision
                for revision in self._revisions.entries
                if revision.source is not RevisionSource.RAW_ASR
                or self.controller.settings.privacy.save_history
            )
            self.editor_draft_requested.emit(
                (self._editor_draft_id, language.value, text, versions)
            )

    def _suggest_correction(self) -> None:
        original = self._correction_origin
        if original is None:
            return
        edited = self.edit_original.toPlainText()
        baseline = self._correction_baseline
        identifier = self._editor_draft_id
        self.suggest_button.setEnabled(False)

        def find() -> None:
            try:
                suggestion = self._corrections.suggest(
                    baseline, edited, original.language.value, original.profile_id
                )
            except (OSError, ValueError, TypeError):
                suggestion = None
            self.correction_ready.emit((identifier, edited, suggestion))

        Thread(target=find, name="voxtype-correction-preview", daemon=True).start()

    def _offer_correction(
        self, result: tuple[str | None, str, CorrectionSuggestion | None]
    ) -> None:
        identifier, edited, suggestion = result
        if self._editor_draft_id != identifier:
            return
        self.suggest_button.setEnabled(self._correction_origin is not None)
        if self.edit_original.toPlainText() != edited:
            return
        if suggestion is None:
            self.feedback.setText("Подходящего короткого исправления не найдено.")
            return
        dialog = QMessageBox(self)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setWindowTitle("Исправление для словаря")
        dialog.setText(f"Произнесено: {suggestion.spoken}\nПравильно: {suggestion.canonical}")
        save = dialog.addButton("Сохранить исправление", QMessageBox.ButtonRole.AcceptRole)
        never = dialog.addButton(
            "Больше не предлагать эту пару", QMessageBox.ButtonRole.DestructiveRole
        )
        ignore = dialog.addButton("Пропустить", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(ignore)
        dialog.exec()
        if dialog.clickedButton() is save:

            def save_correction() -> str:
                self._corrections.save(suggestion)
                return "organization"

            self._persist(save_correction)
        elif dialog.clickedButton() is never:

            def ignore_correction() -> str:
                self._corrections.ignore_permanently(suggestion)
                return "organization"

            self._persist(ignore_correction)

    def _read_selection(self) -> None:
        if self._draft_timer.isActive():
            self.flush_editor_draft()
        self._correction_origin = None
        self._editor_draft_id = None
        self.suggest_button.setEnabled(False)
        self._selection = None
        self._clear_preview()
        self.tabs.setEnabled(False)

        def worker() -> None:
            try:
                selection = self.controller.read_professional_selection()
            except SelectionUnavailable as exc:
                self.selection_finished.emit(None, str(exc))
            except Exception:
                self.selection_finished.emit(None, "Выделение недоступно. Вставьте текст вручную.")
            else:
                self.selection_finished.emit(selection, "Выделение считано")

        Thread(target=worker, name="voxtype-read-selection", daemon=True).start()

    def _set_selection(self, selection: object, message: str) -> None:
        self.tabs.setEnabled(True)
        if isinstance(selection, SelectedText):
            self._selection = selection
            self._revisions = TranscriptRevisions()
            self.edit_original.setPlainText(selection.text)
            self.show()
            self.raise_()
        self.feedback.setText(message)

    def _preview_edit(self) -> None:
        original = self.edit_original.toPlainText()
        try:
            value = self.editor.preview(
                original,
                self.edit_operation.currentText(),
                self.edit_source.text(),
                self.edit_replacement.text(),
            )
        except ValueError as exc:
            self._clear_preview()
            self.feedback.setText(str(exc))
            return
        self._preview_source, self._preview_result = original, value
        self.edit_preview.setPlainText(value)

    def _clear_preview(self) -> None:
        self._preview_source = self._preview_result = None
        self.edit_preview.clear()

    def _apply_edit(self) -> None:
        original = self.edit_original.toPlainText()
        if self._preview_source != original or self._preview_result is None:
            self.feedback.setText("Сначала создайте актуальный предпросмотр")
            return
        self._record_editor_text()
        self._write_editor_revision(
            self.editor.apply(original, self._preview_result), RevisionSource.USER_EDIT
        )

    def _undo_edit(self) -> None:
        self._write_editor_revision(
            self.editor.undo(self.edit_original.toPlainText()), RevisionSource.RESTORED
        )

    def _replace_selection(self) -> None:
        selection, text = self._selection, self._preview_result
        if selection is None or self._preview_source != selection.text or not text:
            self.feedback.setText("Считайте выделение заново и создайте предпросмотр")
            return
        if (
            QMessageBox.question(
                self, "Замена выделения", "Отправить подготовленный текст в исходный редактор?"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.tabs.setEnabled(False)

        def worker() -> None:
            try:
                sent = self.controller.replace_professional_selection(selection, text)
            except (OSError, RuntimeError):
                sent = False
            self.selection_finished.emit(
                None,
                "Команда вставки отправлена"
                if sent
                else "Выделение изменилось или цель недоступна",
            )

        self._selection = None
        Thread(target=worker, name="voxtype-replace-selection", daemon=True).start()
