"""Professional tools in the existing VoxType desktop shell."""

from __future__ import annotations

import logging
import json
from collections.abc import Callable
from dataclasses import asdict, replace
from importlib.util import find_spec
from pathlib import Path
from threading import Thread
from uuid import uuid4

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QFileDialog, QInputDialog, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from voxtype.core.profiles import AppRule, BUILTIN_IDS, FORMATTING_MODES, DictationProfile
from voxtype.core.capabilities import CapabilityRegistry
from voxtype.transcript.enhancer import LocalTextEnhancer, LlamaCppTextEnhancer, OPERATIONS
from voxtype.transcript.professional import (
    ACTION_PARAMETERS, MAX_HINT_CHARACTERS, VOICE_ACTION_TYPES, RecognitionRequest, Snippet,
)
from voxtype.core.profiles import DictationContext
from voxtype.transcript.editing import TextEditService
from voxtype.transcript.workflows import WORKFLOW_TEMPLATES, parse_workflows
from voxtype.settings.organization_pack import OrganizationProfilePack
from voxtype.windows.selection import SelectedText, SelectionUnavailable

_LOGGER = logging.getLogger(__name__)


class ProfessionalWindow(QDialog):
    operation_finished = Signal(object, str)
    insertion_requested = Signal(str)
    selection_finished = Signal(object, str)
    statistics_ready = Signal(int, object)
    enhancement_ready = Signal(str, str)

    def __init__(self, controller, parent: QWidget | None = None) -> None:
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
        root.addWidget(self.feedback)
        page = QWidget(self)
        self.tabs.addTab(page, "Профили")
        form = QFormLayout(page)
        self.profile = QComboBox(page)
        self.name = QLineEdit(page)
        self.language = self._choices(("Язык настроек", "", "Русский", "ru", "English", "en"))
        self.model = self._choices(("Модель настроек", "", "small", "small", "large-v3-turbo", "large-v3-turbo"))
        self.formatting = self._choices(tuple(value for mode in FORMATTING_MODES for value in (mode, mode)))
        self.insertion = self._choices((
            "Режим настроек", "", "Автоматический", "auto", "Буфер обмена", "clipboard_paste",
            "Unicode", "unicode_input", "Только копирование", "safe_clipboard",
        ))
        self.enabled = QCheckBox(page)
        self.rules = QPlainTextEdit(page)
        self.vocabulary_id = QLineEdit(page)
        self.command_set_id = QLineEdit(page)
        self.snippet_set_id = QLineEdit(page)
        self.rules.setPlaceholderText("app.exe | *заголовок* | 10 | 1\nC:\\Apps\\app.exe | | 5 | 0")
        for label, widget in (
            ("Профиль", self.profile), ("Название", self.name), ("Язык", self.language),
            ("Модель", self.model), ("Формат", self.formatting), ("Вставка", self.insertion),
            ("Включён", self.enabled),
            ("ID словаря", self.vocabulary_id), ("ID набора команд", self.command_set_id),
            ("ID набора шаблонов", self.snippet_set_id),
            ("Правила: EXE | заголовок | приоритет | 0/1", self.rules),
        ):
            form.addRow(label, widget)
        actions = QHBoxLayout()
        self.save_button = self._button("Сохранить", self._save, actions)
        self.remove_button = self._button("Удалить", self._remove, actions)
        self._button("Создать", lambda: self._create(False), actions)
        self._button("Копировать", lambda: self._create(True), actions)
        self._button("По умолчанию", self._default, actions)
        form.addRow(actions)
        self.profile.currentIndexChanged.connect(self._load)
        self.operation_finished.connect(self._finished)
        self._reload(self.profiles.default_id)
        self.collection_pages: dict[str, QWidget] = {}
        self.collection_editors: dict[str, QPlainTextEdit] = {}
        for key, label in (
            ("dictionary", "Словарь"), ("snippets", "AutoText"),
            ("commands", "Голосовые команды"), ("replacements", "Замены"),
        ):
            if ((key == "commands" and not self.controller.policy.allow_voice_commands)
                    or (key == "snippets" and not self.controller.policy.allow_user_snippets)):
                continue
            self._add_collection(key, label)
        self._add_text_editor()
        self._add_workflows()
        self._add_organization()
        self._add_statistics()
        self.enhancer: LocalTextEnhancer | None = None
        self.transcription_panel = None
        self._add_capabilities()
        self.selection_finished.connect(self._set_selection)

    def bind_transcription_queue(self, queue) -> None:
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
            lambda _index: self._load_statistics() if self.tabs.currentWidget() is self.stats_page else None
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
        self._persist(lambda: self.controller.usage_metrics.save_typing_speed(value) or self.profiles.default_id)

    def _render_statistics(self, revision: int, data: object) -> None:
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
            lambda _index: self._refresh_capabilities() if self.tabs.currentWidget() is page else None
        )
        if find_spec("llama_cpp") is not None and not self.controller.policy.disable_external_processing:
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
            self.controller, file_queue=panel.queue if panel else None, enhancer=self.enhancer,
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
        if backend is None or self.controller.policy.disable_external_processing:
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
                self.enhancement_ready.emit("", f"Локальная модель не обработала текст ({type(exc).__name__})")
            else:
                self.enhancement_ready.emit(result, "")

        Thread(target=transform, name="voxtype-local-text-enhancer", daemon=True).start()

    def _show_enhancement(self, result: str, error: str) -> None:
        self.ai_select.setEnabled(True)
        self.ai_run.setEnabled(True)
        if error:
            self.feedback.setText(error)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Локальный AI — предпросмотр")
        dialog.resize(680, 430)
        layout = QVBoxLayout(dialog)
        preview = QPlainTextEdit(result, dialog)
        layout.addWidget(preview)
        row = QHBoxLayout()
        self._button("Перенести в локальный редактор",
                     lambda: (self.edit_original.setPlainText(preview.toPlainText()), dialog.accept()), row)
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
        summary.setPlainText(json.dumps(
            {key: value for key, value in asdict(service.policy).items() if value != defaults[key]},
            ensure_ascii=False, indent=2,
        ))
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
            dialog = QMessageBox(QMessageBox.Icon.Question, "Импорт оргпакета",
                                 "Добавить новые записи?", parent=page)
            dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
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
            filename, _ = QFileDialog.getSaveFileName(page, "Экспорт оргпакета", "VoxType.json", "JSON (*.json)")
            if filename:
                try:
                    active = asdict(self.controller.policy)
                    text = self.controller.organization_packs.export(
                        name.strip(), {key: value for key, value in active.items() if value != defaults[key]},
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
        self.workflow_editor = QPlainTextEdit(page)
        self.workflow_editor.setPlainText(json.dumps(
            [asdict(item) for item in self.controller.workflows.definitions()],
            ensure_ascii=False, indent=2,
        ))
        self.workflow_editor.setPlaceholderText("Добавьте редактируемую копию шаблона")
        layout.addWidget(self.workflow_editor)
        row = QHBoxLayout()
        templates = QComboBox(page)
        for template in WORKFLOW_TEMPLATES:
            templates.addItem(template.name, template)
        row.addWidget(templates)

        def editor_definitions():
            return parse_workflows(json.loads(self.workflow_editor.toPlainText() or "[]"))

        def add_template() -> None:
            try:
                items = (*editor_definitions(), self.controller.workflows.copy_template(templates.currentData()))
                self.workflow_editor.setPlainText(json.dumps(
                    [asdict(item) for item in items], ensure_ascii=False, indent=2,
                ))
            except (ValueError, TypeError) as exc:
                self.feedback.setText(str(exc))

        def save() -> None:
            try:
                items = editor_definitions()
            except (ValueError, TypeError) as exc:
                self.feedback.setText(str(exc))
                return
            self._persist(lambda: self.controller.workflows.save(items) or self.profiles.default_id)

        def directory() -> None:
            name = QFileDialog.getExistingDirectory(self, "Локальная папка вывода")
            if name:
                self._persist(lambda: self.controller.workflows.set_directory(Path(name)) or self.profiles.default_id)

        def run() -> None:
            profile, text = self.controller.workflow_input()
            if not text.strip():
                current = self._selected()
                profile = current.id if current else self.profiles.default_id
                text = self.edit_original.toPlainText()
                if not text.strip():
                    self.feedback.setText("Добавьте текст в редактор или завершите диктовку")
                    return
            workflows = [item for item in self.controller.workflows.definitions()
                         if item.enabled and item.trigger_type == "manual"
                         and (not item.profile_id or item.profile_id == profile)]
            if not workflows:
                self.feedback.setText("Сохраните хотя бы один ручной процесс для этого профиля")
                return
            labels = [f"{item.name} · {item.id}" for item in workflows]
            selected, ok = QInputDialog.getItem(self, "Запуск процесса", "Процесс", labels, editable=False)
            if ok:
                workflow = workflows[labels.index(selected)]
                Thread(target=self.controller.workflows.dispatch, args=("manual", profile, text),
                       kwargs={"workflow_id": workflow.id}, name="voxtype-workflow", daemon=True).start()

        def show_steps() -> None:
            steps = self.controller.workflows.last_steps
            self.feedback.setText("\n".join(
                f"{step.workflow} · {step.action} · {step.status}" for step in steps
            ) or "Шаги ещё не выполнялись")

        self._button("Копировать шаблон", add_template, row)
        self._button("Сохранить", save, row)
        self._button("Папка вывода…", directory, row)
        self._button("Запуск…", run, row)
        self._button("Шаги", show_steps, row)
        layout.addLayout(row)
        self.tabs.addTab(page, "Процессы")

    def _choices(self, pairs: tuple[str, ...]) -> QComboBox:
        combo = QComboBox(self)
        for index in range(0, len(pairs), 2):
            combo.addItem(pairs[index], pairs[index + 1])
        return combo

    def _button(self, label: str, action: Callable[[], None], row: QHBoxLayout) -> QPushButton:
        button = QPushButton(label, self)
        button.clicked.connect(action)
        row.addWidget(button)
        return button

    def _selected(self) -> DictationProfile | None:
        return self.profile.currentData()

    def _reload(self, selected: str) -> None:
        self.profile.blockSignals(True)
        self.profile.clear()
        index = 0
        for profile in self.profiles.profiles():
            marker = " • по умолчанию" if profile.id == self.profiles.default_id else ""
            if profile.id == selected:
                index = self.profile.count()
            self.profile.addItem(profile.name + marker, profile)
        self.profile.setCurrentIndex(index)
        self.profile.blockSignals(False)
        self._load()

    def _load(self) -> None:
        profile = self._selected()
        if profile is None:
            return
        mutable = profile.id not in BUILTIN_IDS
        self.name.setText(profile.name)
        self.vocabulary_id.setText(profile.vocabulary_id)
        self.command_set_id.setText(profile.command_set_id)
        self.snippet_set_id.setText(profile.snippet_set_id)
        self.enabled.setChecked(profile.enabled)
        self.rules.setPlainText("\n".join(
            f"{rule.executable} | {rule.title_glob} | {rule.priority} | {int(rule.enabled)}"
            for rule in profile.app_rules
        ))
        for combo, value in (
            (self.language, profile.language), (self.model, profile.model_id),
            (self.formatting, profile.formatting_mode), (self.insertion, profile.insertion_mode),
        ):
            combo.setCurrentIndex(combo.findData(value))
            combo.setEnabled(mutable)
        for widget in (self.name, self.enabled, self.rules, self.save_button, self.remove_button,
                       self.vocabulary_id, self.command_set_id, self.snippet_set_id):
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
        if selected == "organization":
            for key, editor in self.collection_editors.items():
                editor.setPlainText(self.controller.professional_content.export_json(key))
            selected = self.profiles.default_id
        if isinstance(selected, str):
            self._reload(selected)
        self.feedback.setText(message)

    def _save(self) -> None:
        profile = self._selected()
        if profile is None or profile.id in BUILTIN_IDS:
            return
        try:
            rules = []
            for line in self.rules.toPlainText().splitlines():
                if line.strip():
                    parts = [part.strip() for part in line.split("|")]
                    if len(parts) != 4 or parts[3] not in ("0", "1"):
                        raise ValueError("Для правила нужны четыре поля; последнее 0 или 1")
                    rules.append(AppRule(parts[0], parts[1], int(parts[2]), parts[3] == "1"))
            updated = replace(
                profile, name=self.name.text().strip(), language=self.language.currentData(),
                model_id=self.model.currentData(), formatting_mode=self.formatting.currentData(),
                insertion_mode=self.insertion.currentData(), enabled=self.enabled.isChecked(),
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
        if QMessageBox.question(self, "Удаление профиля", "Удалить выбранный профиль?") != QMessageBox.StandardButton.Yes:
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

    def _add_collection(self, key: str, label: str) -> None:
        page = QWidget(self)
        self.tabs.addTab(page, label)
        self.collection_pages[key] = page
        layout = QVBoxLayout(page)
        editor = QPlainTextEdit(page)
        editor.setPlainText(self.controller.professional_content.export_json(key))
        self.collection_editors[key] = editor
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
        self._button("Сохранить", lambda: self._commit_collection(key), row)
        self._button("Импорт", lambda: self._import_collection(key), row)
        self._button("Экспорт", lambda: self._export_collection(key), row)
        if key in {"snippets", "replacements"}:
            self._button("Предпросмотр", lambda: self._preview_collection(key), row)
        layout.addLayout(row)

    def _edit_processing_options(self) -> None:
        settings = self.controller.professional_content.snapshot()
        dialog = QDialog(self)
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
            self._persist(lambda: self.controller.professional_content.save_options(value, limit)
                          or self.profiles.default_id)

    def _command_document(self) -> dict | None:
        editor = self.collection_editors["commands"]
        try:
            data = json.loads(editor.toPlainText())
            if data.get("schema_version") != 1:
                raise ValueError("Схема команды должна быть версии 1")
            self.controller.professional_content.parse("commands", data["commands"])
            return data
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self.feedback.setText(str(exc))
            return None

    def _choose_command(self, entries: list[dict]) -> int | None:
        if not entries:
            self.feedback.setText("Список команд пуст")
            return None
        labels = [f"{item['trigger']} · {item['id']}" for item in entries]
        label, selected = QInputDialog.getItem(self, "Голосовая команда", "Команда", labels, editable=False)
        return labels.index(label) if selected else None

    def _edit_command(self, *, new: bool) -> None:
        data = self._command_document()
        if data is None:
            return
        entries = data["commands"]
        index = None if new else self._choose_command(entries)
        if not new and index is None:
            return
        entry = entries[index] if index is not None else {
            "id": uuid4().hex, "action_type": "InsertLiteralText", "payload": {},
        }
        dialog = QDialog(self)
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
        enabled.setChecked(entry.get("enabled", True))
        confirm = QCheckBox(dialog)
        confirm.setChecked(entry.get("confirmation_required", False))
        for label, widget in (
            ("Фраза", trigger), ("ID набора", command_set), ("Действие", action),
            ("Имя или текст", parameter), ("Панель VoxType", panel),
            ("Включена", enabled), ("С запросом подтверждения", confirm),
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
            "id": entry["id"], "trigger": trigger.text().strip(), "action_type": kind,
            "payload": {key: value} if key else {}, "command_set_id": command_set.text().strip(),
            "enabled": enabled.isChecked(), "confirmation_required": confirm.isChecked(),
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
        self.collection_editors["commands"].setPlainText(json.dumps(data, ensure_ascii=False, indent=2))
        self._commit_collection("commands")

    def _delete_command(self) -> None:
        data = self._command_document()
        if data is None:
            return
        entries = data["commands"]
        index = self._choose_command(entries)
        if index is not None and QMessageBox.question(
            self, "Удалить голосовую команду?", entries[index]["trigger"],
            defaultButton=QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            del entries[index]
            self.collection_editors["commands"].setPlainText(json.dumps(data, ensure_ascii=False, indent=2))
            self._commit_collection("commands")

    def _edit_collection_item(self, key: str, *, new: bool) -> None:
        editor = self.collection_editors[key]
        try:
            data = json.loads(editor.toPlainText())
            entries = [asdict(item) for item in self.controller.professional_content.parse(key, data[key])]
        except (ValueError, TypeError, KeyError, AttributeError):
            self.feedback.setText("Исправьте JSON в редакторе")
            return
        fields = {
            "dictionary": (("canonical_text", "Написание"), ("spoken_variants", "Произношения, по одному на строку"),
                           ("language", "Язык: ru или en"), ("profile_id", "ID профиля"),
                           ("executable", "Имя или путь EXE"), ("vocabulary_id", "ID словаря")),
            "snippets": (("name", "Название"), ("trigger_phrase", "Фраза"), ("content", "Текст"),
                         ("category", "Категория"), ("profile_id", "ID профиля"),
                         ("snippet_set_id", "ID набора")),
            "replacements": (("pattern", "Что заменить"), ("replacement", "На что заменить"),
                             ("scope", "Область: ID профиля, app:EXE или lang:ru")),
        }[key]
        index = None
        if not new:
            labels = [f"{row + 1}. {entry[fields[0][0]]}" for row, entry in enumerate(entries)]
            if not labels:
                return
            label, accepted = QInputDialog.getItem(self, "Выберите запись", "Запись", labels, editable=False)
            if not accepted:
                return
            index = labels.index(label)
        entry = entries[index] if index is not None else ({} if key == "dictionary" else {"id": uuid4().hex})
        dialog = QDialog(self)
        dialog.setWindowTitle({"dictionary": "Словарь", "snippets": "Шаблон AutoText",
                               "replacements": "Правило замены"}[key])
        form = QFormLayout(dialog)
        widgets = {}
        for field, label in fields:
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
        enabled.setChecked(entry.get("enabled", True))
        form.addRow("Включена", enabled)
        priority = QSpinBox(dialog)
        priority.setRange(-10000, 10000)
        priority.setValue(entry.get("priority", 0))
        if key != "snippets":
            form.addRow("Приоритет", priority)
        match_mode = QComboBox(dialog)
        match_mode.addItems(("word", "case_sensitive", "literal"))
        match_mode.setCurrentText(entry.get("match_mode", "word"))
        case_policy = QComboBox(dialog)
        case_policy.addItems(("canonical", "preserve"))
        case_policy.setCurrentText(entry.get("case_policy", "canonical"))
        case_sensitive, whole_word = QCheckBox(dialog), QCheckBox(dialog)
        case_sensitive.setChecked(entry.get("case_sensitive", False))
        whole_word.setChecked(entry.get("whole_word", True))
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
                updated[field] = widget.toPlainText() if isinstance(widget, QPlainTextEdit) else widget.text().strip()
            if key == "dictionary":
                updated.update(
                    spoken_variants=tuple(part.strip() for part in updated["spoken_variants"].splitlines() if part.strip()),
                    priority=priority.value(), match_mode=match_mode.currentText(),
                    case_policy=case_policy.currentText(),
                )
            elif key == "replacements":
                updated.update(match_type="literal", priority=priority.value(),
                               case_sensitive=case_sensitive.isChecked(), whole_word=whole_word.isChecked())
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
        path, selected_filter = QFileDialog.getSaveFileName(self, "Экспорт коллекции", key + ".json", filters)
        if not path:
            return
        try:
            if key == "dictionary" and (Path(path).suffix.casefold() == ".csv" or selected_filter.startswith("CSV")):
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
            name, accepted = QInputDialog.getItem(self, "Шаблон", "Выберите шаблон", names, editable=False)
            if accepted and name in names:
                text = self.render_snippet(items[names.index(name)])
                if text is not None:
                    self.insertion_requested.emit(text)
            return
        original, accepted = QInputDialog.getMultiLineText(self, "Предпросмотр замен", "До")
        if accepted:
            pipeline = replace(self.controller.professional_content.snapshot(), replacements=items, dictionary=())
            profile = replace(profile, formatting_mode="verbatim")
            request = RecognitionRequest(0, profile.language or "ru", profile.id, "", DictationContext(), (), None)
            result = pipeline.process(request, original, profile, allow_commands=False)
            result_box = QDialog(self)
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
        text, accepted = QInputDialog.getMultiLineText(self, "Подтвердить вставку", "Текст", rendered)
        return text if accepted and text else None

    def _add_text_editor(self) -> None:
        page = QWidget(self)
        self.tabs.addTab(page, "Редактор текста")
        layout = QVBoxLayout(page)
        self.editor = TextEditService()
        self._selection: SelectedText | None = None
        self._preview_source: str | None = None
        self._preview_result: str | None = None
        self.edit_original = QPlainTextEdit(page)
        self.edit_original.setPlaceholderText("Вставьте текст сюда или запросите выделение")
        self.edit_preview = QPlainTextEdit(page)
        self.edit_preview.setPlaceholderText("Предпросмотр")
        self.edit_preview.setReadOnly(True)
        self.edit_operation = QComboBox(page)
        self.edit_operation.addItems((
            "Uppercase", "Lowercase", "Sentence case", "Remove duplicate spaces",
            "Replace literal phrase", "Insert bullet list formatting", "Normalize line breaks",
        ))
        self.edit_source = QLineEdit(page)
        self.edit_source.setPlaceholderText("Исходная фраза для замены")
        self.edit_replacement = QLineEdit(page)
        self.edit_replacement.setPlaceholderText("Новая фраза")
        for widget in (self.edit_operation, self.edit_source, self.edit_replacement,
                       self.edit_original, self.edit_preview):
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
        layout.addLayout(row)
        row = QHBoxLayout()
        self._button("Заменить выделение", self._replace_selection, row).setEnabled(selection.available)
        layout.addLayout(row)

    def _read_selection(self) -> None:
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
            self.edit_original.setPlainText(selection.text)
            self.show()
            self.raise_()
        self.feedback.setText(message)

    def _preview_edit(self) -> None:
        original = self.edit_original.toPlainText()
        try:
            value = self.editor.preview(
                original, self.edit_operation.currentText(), self.edit_source.text(),
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
        self.edit_original.setPlainText(self.editor.apply(original, self._preview_result))
        self._clear_preview()

    def _undo_edit(self) -> None:
        self.edit_original.setPlainText(self.editor.undo(self.edit_original.toPlainText()))
        self._clear_preview()

    def _replace_selection(self) -> None:
        selection, text = self._selection, self._preview_result
        if selection is None or self._preview_source != selection.text or not text:
            self.feedback.setText("Считайте выделение заново и создайте предпросмотр")
            return
        if QMessageBox.question(
            self, "Замена выделения", "Отправить подготовленный текст в исходный редактор?"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.tabs.setEnabled(False)

        def worker() -> None:
            try:
                sent = self.controller.replace_professional_selection(selection, text)
            except (OSError, RuntimeError):
                sent = False
            self.selection_finished.emit(
                None, "Команда вставки отправлена" if sent else "Выделение изменилось или цель недоступна",
            )

        self._selection = None
        Thread(target=worker, name="voxtype-replace-selection", daemon=True).start()
