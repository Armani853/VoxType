"""Local dictation history browser."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ._optional import PYSIDE6_AVAILABLE, require_pyside6
from .models import HistoryEntry, format_duration, history_entry_from

if PYSIDE6_AVAILABLE:
    from PySide6.QtCore import QSignalBlocker, Qt, Signal
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import (
        QComboBox,
        QDialog,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QSplitter,
        QStackedLayout,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    from .theme import SPACING, state_color
    from .widgets import Card

    class HistoryWindow(QDialog):
        refresh_requested = Signal()
        export_requested = Signal(str)
        delete_requested = Signal(str)
        recover_requested = Signal(str)

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setObjectName("historyWindow")
            self.setWindowTitle("История диктовки — VoxType")
            self.resize(1000, 650)
            self.setMinimumSize(980, 520)
            self._entries: dict[str, HistoryEntry] = {}
            self._detail_session_id: str | None = None
            self._build_ui()
            self._connect_ui()

        def _build_ui(self) -> None:
            root = QVBoxLayout(self)
            root.setContentsMargins(
                SPACING.space_5,
                SPACING.space_5,
                SPACING.space_5,
                SPACING.space_4,
            )
            root.setSpacing(SPACING.space_3)
            header = QHBoxLayout()
            heading_column = QVBoxLayout()
            heading = QLabel("История диктовки", self)
            heading.setProperty("heading", True)
            hint = QLabel("Текст хранится только на этом компьютере", self)
            hint.setProperty("muted", True)
            heading_column.addWidget(heading)
            heading_column.addWidget(hint)
            header.addLayout(heading_column)
            header.addStretch(1)
            self.refresh_button = QPushButton("Обновить", self)
            self.refresh_button.setProperty("flat", True)
            header.addWidget(self.refresh_button)
            root.addLayout(header)

            filters = QHBoxLayout()
            self.search_edit = QLineEdit(self)
            self.search_edit.setObjectName("historySearch")
            self.search_edit.setPlaceholderText("Поиск по тексту или приложению…")
            self.search_edit.setClearButtonEnabled(True)
            self.language_filter = QComboBox(self)
            self.language_filter.addItem("Все языки", "")
            self.language_filter.addItem("RU", "ru")
            self.language_filter.addItem("EN", "en")
            self.status_filter = QComboBox(self)
            self.status_filter.addItem("Все статусы", "")
            self.status_filter.addItem("Завершено", "completed")
            self.status_filter.addItem("Не завершено", "incomplete")
            self.status_filter.addItem("Восстановлено", "recovered")
            filters.addWidget(self.search_edit, 1)
            filters.addWidget(self.language_filter)
            filters.addWidget(self.status_filter)
            root.addLayout(filters)

            splitter = QSplitter(Qt.Orientation.Horizontal, self)
            left_panel = QWidget(splitter)
            left_panel.setMinimumWidth(520)
            left_stack = QStackedLayout(left_panel)
            left_stack.setContentsMargins(0, 0, 0, 0)
            self.table = QTableWidget(0, 5, left_panel)
            self.table.setObjectName("historyTable")
            self.table.setHorizontalHeaderLabels(
                ("Начало", "Язык", "Длительность", "Приложение", "Статус")
            )
            self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
            self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.table.setWordWrap(False)
            self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.table.setSortingEnabled(True)
            self.table.setShowGrid(False)
            self.table.verticalHeader().setVisible(False)
            self.table.verticalHeader().setDefaultSectionSize(38)
            table_header = self.table.horizontalHeader()
            table_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            table_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            table_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            table_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            table_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
            table_header.resizeSection(1, 68)
            table_header.resizeSection(2, 108)
            table_header.resizeSection(4, 108)

            empty_panel = Card(left_panel)
            empty_panel.content_layout.addStretch(1)
            self.empty_label = QLabel("Записей не найдено", empty_panel)
            self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.empty_label.setProperty("sectionHeading", True)
            self.empty_hint = QLabel("Попробуйте изменить фильтры.", empty_panel)
            self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.empty_hint.setProperty("muted", True)
            empty_panel.content_layout.addWidget(self.empty_label)
            empty_panel.content_layout.addWidget(self.empty_hint)
            empty_panel.content_layout.addStretch(1)
            left_stack.addWidget(self.table)
            left_stack.addWidget(empty_panel)
            self._history_left_stack = left_stack
            self._history_empty_panel = empty_panel

            detail = Card(splitter)
            detail.setMinimumWidth(400)
            detail.setObjectName("historyDetail")
            detail_title = QLabel("Расшифровка", detail)
            detail_title.setProperty("sectionHeading", True)
            self.metadata_label = QLabel("Выберите сессию слева", detail)
            self.metadata_label.setProperty("caption", True)
            self.metadata_label.setWordWrap(True)
            self.text_edit = QTextEdit(detail)
            self.text_edit.setObjectName("historyText")
            self.text_edit.setReadOnly(True)
            self.text_edit.setProperty("contentSurface", True)
            self.text_edit.setPlaceholderText("Текст выбранной сессии")
            self.warning_label = QLabel("", detail)
            self.warning_label.setWordWrap(True)
            self.warning_label.setStyleSheet(f"color: {state_color('warning')};")
            actions = QHBoxLayout()
            self.copy_button = QPushButton("Копировать", detail)
            self.export_button = QPushButton("Экспорт…", detail)
            self.recover_button = QPushButton("Восстановить", detail)
            self.recover_button.setToolTip(
                "Повторно отправить сохранённый текст незавершённой сессии в её целевое окно"
            )
            self.delete_button = QPushButton("Удалить", detail)
            self.delete_button.setToolTip("Безвозвратно удалить выбранную локальную запись")
            self.delete_button.setProperty("danger", True)
            actions.addWidget(self.copy_button)
            actions.addWidget(self.export_button)
            actions.addWidget(self.recover_button)
            actions.addStretch(1)
            actions.addWidget(self.delete_button)
            detail.content_layout.addWidget(detail_title)
            detail.content_layout.addWidget(self.metadata_label)
            detail.content_layout.addWidget(self.text_edit, 1)
            detail.content_layout.addWidget(self.warning_label)
            detail.content_layout.addLayout(actions)
            splitter.addWidget(left_panel)
            splitter.addWidget(detail)
            splitter.setStretchFactor(0, 3)
            splitter.setStretchFactor(1, 4)
            splitter.setSizes((440, 540))
            root.addWidget(splitter, 1)
            self._set_action_state(None)

        def _connect_ui(self) -> None:
            self.refresh_button.clicked.connect(self.refresh_requested)
            self.search_edit.textChanged.connect(self._apply_filters)
            self.language_filter.currentIndexChanged.connect(self._apply_filters)
            self.status_filter.currentIndexChanged.connect(self._apply_filters)
            self.table.itemSelectionChanged.connect(self._selection_changed)
            self.copy_button.clicked.connect(self._copy_selected)
            self.export_button.clicked.connect(self._export_selected)
            self.delete_button.clicked.connect(self._delete_selected)
            self.recover_button.clicked.connect(self._recover_selected)

        def set_loading(self, loading: bool) -> None:
            self.refresh_button.setText("Загружаю…" if loading else "Обновить")
            self.refresh_button.setEnabled(not loading)

        def set_sessions(self, sessions: list[HistoryEntry | object]) -> None:
            entries = [history_entry_from(session) for session in sessions]
            by_id = {entry.session_id: entry for entry in entries if entry.session_id}
            if by_id == self._entries and self.table.rowCount():
                return
            previous_session_id = self.selected_session_id() or self._detail_session_id
            ordered = sorted(
                by_id.values(), key=lambda entry: _datetime_sort_key(entry.started_at), reverse=True
            )
            self._entries = by_id
            with QSignalBlocker(self.table):
                self.table.setSortingEnabled(False)
                try:
                    self.table.setRowCount(len(ordered))
                    for row, entry in enumerate(ordered):
                        values = (
                            _format_datetime(entry.started_at),
                            entry.language.value.upper(),
                            format_duration(entry.duration_s),
                            entry.target_application or "—",
                            _status_text(entry.completion_status),
                        )
                        for column, value in enumerate(values):
                            item = QTableWidgetItem(value)
                            item.setData(Qt.ItemDataRole.UserRole, entry.session_id)
                            item.setToolTip(value)
                            self.table.setItem(row, column, item)
                finally:
                    self.table.setSortingEnabled(True)
            self._apply_filters(preferred_session_id=previous_session_id)

        def selected_session_id(self) -> str | None:
            row = self.table.currentRow()
            item = self.table.item(row, 0) if row >= 0 else None
            value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            return str(value) if value else None

        def selected_entry(self) -> HistoryEntry | None:
            session_id = self.selected_session_id()
            return self._entries.get(session_id) if session_id else None

        def _selection_changed(self) -> None:
            self._set_action_state(self.selected_entry())

        def _set_action_state(self, entry: HistoryEntry | None) -> None:
            self._detail_session_id = entry.session_id if entry is not None else None
            enabled = entry is not None
            self.copy_button.setEnabled(enabled and bool(entry and entry.confirmed_text))
            self.export_button.setEnabled(enabled)
            self.delete_button.setEnabled(enabled)
            recoverable = bool(
                entry
                and entry.completion_status.lower() not in {"completed", "complete", "success"}
            )
            self.recover_button.setVisible(recoverable)
            self.recover_button.setEnabled(recoverable)
            if entry is None:
                self.metadata_label.setText("Выберите запись в истории")
                self.text_edit.clear()
                self.warning_label.clear()
                return
            self.metadata_label.setText(
                f"{_format_datetime(entry.started_at)} · {entry.language.value.upper()} · "
                f"{self._profile_text(entry)} · {entry.model or 'модель не указана'} · "
                f"{format_duration(entry.duration_s)} · "
                f"{entry.target_application or 'приложение не указано'} · "
                f"{_status_text(entry.completion_status)}"
            )
            self.text_edit.setPlainText(entry.confirmed_text)
            messages = list(entry.warnings)
            if entry.dropped_frames:
                messages.append(f"Потеряно кадров: {entry.dropped_frames}")
            self.warning_label.setText(" · ".join(messages))

        def _apply_filters(self, *_args: object, preferred_session_id: str | None = None) -> None:
            needle = self.search_edit.text().strip().casefold()
            language = str(self.language_filter.currentData() or "")
            status = str(self.status_filter.currentData() or "")
            visible_rows: list[int] = []
            visible_session_ids: set[str] = set()
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                session_id = str(item.data(Qt.ItemDataRole.UserRole)) if item else ""
                entry = self._entries.get(session_id)
                visible = bool(entry)
                if entry is not None:
                    haystack = f"{entry.confirmed_text} {entry.target_application}".casefold()
                    visible = (not needle or needle in haystack) and (
                        not language or entry.language.value == language
                    )
                    if status:
                        normalized = entry.completion_status.lower()
                        if status == "incomplete":
                            visible = visible and normalized not in {
                                "completed",
                                "complete",
                                "success",
                                "recovered",
                            }
                        else:
                            visible = visible and normalized == status
                self.table.setRowHidden(row, not visible)
                if visible:
                    visible_rows.append(row)
                    visible_session_ids.add(session_id)
            has_visible = bool(visible_rows)
            self._history_left_stack.setCurrentWidget(
                self.table if has_visible else self._history_empty_panel
            )

            selected_id = preferred_session_id or self.selected_session_id()
            if selected_id in visible_session_ids:
                selected_row = self._row_for_session(selected_id)
            else:
                selected_row = visible_rows[0] if visible_rows else -1
            self.table.blockSignals(True)
            if selected_row >= 0:
                self.table.selectRow(selected_row)
            else:
                self.table.clearSelection()
                self.table.setCurrentCell(-1, -1)
            self.table.blockSignals(False)
            self._set_action_state(self.selected_entry() if selected_row >= 0 else None)
            assert (
                self._detail_session_id is None or self._detail_session_id in visible_session_ids
            ), "history detail must belong to the filtered dataset"

        @staticmethod
        def _profile_text(entry: HistoryEntry) -> str:
            return {
                "fast": "Быстро",
                "balanced": "Баланс",
                "accurate": "Максимальная точность",
            }.get(entry.profile.value, entry.profile.value)

        def _row_for_session(self, session_id: str) -> int:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == session_id:
                    return row
            return -1

        @property
        def detail_session_id(self) -> str | None:
            return self._detail_session_id

        @property
        def visible_session_ids(self) -> set[str]:
            identifiers: set[str] = set()
            for row in range(self.table.rowCount()):
                if self.table.isRowHidden(row):
                    continue
                item = self.table.item(row, 0)
                value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
                if value:
                    identifiers.add(str(value))
            return identifiers

        def _copy_selected(self) -> None:
            entry = self.selected_entry()
            if entry is not None and entry.confirmed_text:
                QGuiApplication.clipboard().setText(entry.confirmed_text)

        def _export_selected(self) -> None:
            session_id = self.selected_session_id()
            if session_id:
                self.export_requested.emit(session_id)

        def _recover_selected(self) -> None:
            session_id = self.selected_session_id()
            if session_id:
                self.recover_requested.emit(session_id)

        def _delete_selected(self) -> None:
            session_id = self.selected_session_id()
            if not session_id:
                return
            answer = QMessageBox.question(
                self,
                "Удалить запись?",
                "Текст и связанные с этой сессией локальные данные будут удалены.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer is QMessageBox.StandardButton.Yes:
                self.delete_requested.emit(session_id)

        def remove_session(self, session_id: str) -> None:
            self._entries.pop(session_id, None)
            for row in range(self.table.rowCount() - 1, -1, -1):
                item = self.table.item(row, 0)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == session_id:
                    self.table.removeRow(row)
            self._apply_filters()

    def _datetime_sort_key(value: datetime | str | float) -> float:
        parsed = _parse_datetime(value)
        if parsed is None:
            return float("-inf")
        try:
            return parsed.timestamp()
        except (OSError, OverflowError, ValueError):
            return float("-inf")

    def _parse_datetime(value: datetime | str | float) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value)).astimezone()
            except (OSError, OverflowError, ValueError):
                return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    def _format_datetime(value: datetime | str | float) -> str:
        parsed = _parse_datetime(value)
        if parsed is None:
            return str(value) or "—"
        return (
            parsed.astimezone().strftime("%d.%m.%Y %H:%M")
            if parsed.tzinfo
            else parsed.strftime("%d.%m.%Y %H:%M")
        )

    def _status_text(value: str) -> str:
        return {
            "completed": "Завершено",
            "complete": "Завершено",
            "success": "Завершено",
            "incomplete": "Не завершено",
            "interrupted": "Прервано",
            "recovered": "Восстановлено",
            "error": "Ошибка",
        }.get(value.lower(), value or "—")

else:

    class HistoryWindow:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs
            require_pyside6()
