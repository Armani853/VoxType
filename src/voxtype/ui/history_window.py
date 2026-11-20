"""Local dictation history browser."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ._optional import PYSIDE6_AVAILABLE, require_pyside6
from .models import HistoryEntry, format_duration, history_entry_from

if PYSIDE6_AVAILABLE:
    from PySide6.QtCore import Qt, QTimer, Signal
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import (
        QComboBox,
        QDialog,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QSplitter,
        QStackedLayout,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    from .theme import SPACING, state_color
    from .widgets import Card
    from .obsidian import VoxEmptyState, VoxSearchField, confirm_delete
    from .records_view import RecordsView
    from .models import text_preview

    class HistoryWindow(QDialog):
        refresh_requested = Signal()
        export_requested = Signal(str)
        delete_requested = Signal(str)
        recover_requested = Signal(str)
        insert_requested = Signal(str)
        clear_requested = Signal()

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setObjectName("historyWindow")
            self.setWindowTitle("История диктовки — VoxType")
            self.resize(1000, 650)
            self.setMinimumSize(980, 520)
            self._entries: dict[str, HistoryEntry] = {}
            self._detail_session_id: str | None = None
            self._page = 0
            self._remote_search = False
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
            self.clear_button = QPushButton("Очистить историю", self)
            self.clear_button.setProperty("danger", True)
            header.addWidget(self.clear_button)
            header.addWidget(self.refresh_button)
            root.addLayout(header)

            filters = QHBoxLayout()
            self.search_edit = VoxSearchField("Поиск по тексту…", self)
            self.search_edit.setObjectName("historySearch")
            self.search_edit.setPlaceholderText("Поиск по тексту…")
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
            details = QHBoxLayout()
            for attr, placeholder in (
                ("application_filter", "Приложение"), ("profile_filter", "ID профиля"),
                ("since_filter", "С даты: ГГГГ-ММ-ДД"), ("until_filter", "По дату: ГГГГ-ММ-ДД"),
            ):
                field = QLineEdit(self)
                field.setPlaceholderText(placeholder)
                setattr(self, attr, field)
                details.addWidget(field)
            self.previous_page = QPushButton("Назад", self)
            self.next_page = QPushButton("Далее", self)
            self.page_label = QLabel("1", self)
            for widget in (self.previous_page, self.page_label, self.next_page):
                details.addWidget(widget)
            self.set_page_available(False)
            root.addLayout(details)

            splitter = self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
            left_panel = QWidget(splitter)
            left_panel.setMinimumWidth(0)
            left_stack = QStackedLayout(left_panel)
            left_stack.setContentsMargins(0, 0, 0, 0)
            self.table = RecordsView(("Начало", "Язык", "Приложение", "Текст"), left_panel)
            self.table.setObjectName("historyTable")
            self.table.setAccessibleName("Записи истории диктовок")
            empty_panel = VoxEmptyState("history", "Записей не найдено",
                                       "Измените фильтры или включите сохранение истории в настройках.", left_panel)
            empty_panel.action.setText("Сбросить фильтры")
            empty_panel.action.clicked.connect(self._reset_filters)
            empty_panel.action.show()
            left_stack.addWidget(self.table)
            left_stack.addWidget(empty_panel)
            self._history_left_stack = left_stack
            self._history_empty_panel = empty_panel

            detail = Card(splitter)
            detail.setMinimumWidth(0)
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
            self.insert_button = QPushButton("Вставить снова", detail)
            self.export_button = QPushButton("Экспорт…", detail)
            self.recover_button = QPushButton("Восстановить", detail)
            self.recover_button.setToolTip(
                "Повторно отправить сохранённый текст незавершённой сессии в её целевое окно"
            )
            self.delete_button = QPushButton("Удалить", detail)
            self.delete_button.setToolTip("Безвозвратно удалить выбранную локальную запись")
            self.delete_button.setProperty("danger", True)
            actions.addWidget(self.copy_button)
            actions.addWidget(self.insert_button)
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
            self.clear_button.clicked.connect(self._clear_history)
            self._search_timer = QTimer(self, interval=230, singleShot=True)
            self._search_timer.timeout.connect(self.refresh_requested)
            for field in (self.search_edit, self.application_filter, self.profile_filter,
                          self.since_filter, self.until_filter):
                field.textChanged.connect(self._search_changed)
            self.previous_page.clicked.connect(lambda: self._change_page(-1))
            self.next_page.clicked.connect(lambda: self._change_page(1))
            self.language_filter.currentIndexChanged.connect(self._search_changed)
            self.status_filter.currentIndexChanged.connect(self._search_changed)
            self.table.selectionModel().selectionChanged.connect(self._selection_changed)
            self.copy_button.clicked.connect(self._copy_selected)
            self.insert_button.clicked.connect(self._insert_selected)
            self.export_button.clicked.connect(self._export_selected)
            self.delete_button.clicked.connect(self._delete_selected)
            self.recover_button.clicked.connect(self._recover_selected)

        def enable_repository_search(self) -> None:
            self._remote_search = True

        def _search_changed(self) -> None:
            if not self._remote_search:
                self._apply_filters()
                return
            self._page = 0
            self._search_timer.start()

        def _change_page(self, step: int) -> None:
            self._page = max(0, self._page + step)
            self.refresh_requested.emit()

        def set_page_available(self, more: bool) -> None:
            self.page_label.setText(str(self._page + 1))
            self.previous_page.setEnabled(self._page > 0)
            self.next_page.setEnabled(more)

        def search_options(self) -> dict[str, Any]:
            from datetime import timedelta

            def date_value(field: QLineEdit, *, inclusive_end: bool = False) -> float | None:
                try:
                    day = datetime.strptime(field.text().strip(), "%Y-%m-%d")
                    return (day + timedelta(days=int(inclusive_end))).timestamp()
                except (ValueError, OverflowError, OSError):
                    return None

            return {
                "text": self.search_edit.text(), "profile": self.profile_filter.text().strip(),
                "application": self.application_filter.text().strip(), "page": self._page,
                "language": self.language_filter.currentData(), "status": self.status_filter.currentData(),
                "since": date_value(self.since_filter),
                "until": date_value(self.until_filter, inclusive_end=True),
            }

        def set_loading(self, loading: bool) -> None:
            self.refresh_button.setText("Загружаю…" if loading else "Обновить")
            self.refresh_button.setEnabled(not loading)

        def resizeEvent(self, event: object) -> None:
            super().resizeEvent(event)
            orientation = Qt.Orientation.Vertical if self.width() < 800 else Qt.Orientation.Horizontal
            if orientation != self.splitter.orientation():
                self.splitter.setOrientation(orientation)
                self.splitter.setSizes((280, 360))

        def _reset_filters(self) -> None:
            for field in (self.search_edit, self.application_filter, self.profile_filter,
                          self.since_filter, self.until_filter):
                field.clear()
            self.language_filter.setCurrentIndex(0)
            self.status_filter.setCurrentIndex(0)
            self._search_changed()

        def set_sessions(self, sessions: list[HistoryEntry | object]) -> None:
            entries = [history_entry_from(session) for session in sessions]
            selected = self.selected_session_id()
            ordered = sorted(entries, key=lambda entry: _datetime_sort_key(entry.started_at), reverse=True)
            self._entries = {entry.session_id: entry for entry in ordered if entry.session_id}
            self._apply_filters(preferred_session_id=selected)

        def selected_session_id(self) -> str | None:
            return self.table.selected_key() or None

        def selected_entry(self) -> HistoryEntry | None:
            session_id = self.selected_session_id()
            return self._entries.get(session_id) if session_id else None

        def _selection_changed(self) -> None:
            self._set_action_state(self.selected_entry())

        def _set_action_state(self, entry: HistoryEntry | None) -> None:
            self._detail_session_id = entry.session_id if entry is not None else None
            enabled = entry is not None
            self.copy_button.setEnabled(enabled and bool(entry and entry.confirmed_text))
            self.insert_button.setEnabled(enabled and bool(entry and entry.confirmed_text))
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
                f"{entry.profile_id} · {self._profile_text(entry)} · {entry.model or 'модель не указана'} · "
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
            needle = "" if self._remote_search else self.search_edit.text().strip().casefold()
            language = str(self.language_filter.currentData() or "")
            status = str(self.status_filter.currentData() or "")
            selected = preferred_session_id or self.selected_session_id()
            rows = []
            for entry in self._entries.values():
                if language and language != entry.language.value:
                    continue
                if needle and needle not in f"{entry.confirmed_text} {entry.target_application}".casefold():
                    continue
                normalized = entry.completion_status.lower()
                if status == "incomplete":
                    if normalized in {"completed", "complete", "success", "recovered"}:
                        continue
                elif status and normalized != status:
                    continue
                rows.append((entry.session_id, (
                    _format_datetime(entry.started_at), entry.language.value.upper(),
                    entry.target_application or ("Аудиофайл" if entry.source_type == "file" else "—"),
                    text_preview(entry.confirmed_text, 110) or _status_text(entry.completion_status),
                )))
            self.table.records.replace(rows)
            self._history_left_stack.setCurrentWidget(self.table if rows else self._history_empty_panel)
            if rows:
                row = self._row_for_session(selected) if selected else -1
                self.table.selectRow(max(0, row))
            self._set_action_state(self.selected_entry())

        @staticmethod
        def _profile_text(entry: HistoryEntry) -> str:
            return {
                "fast": "Быстро",
                "balanced": "Баланс",
                "accurate": "Максимальная точность",
            }.get(entry.profile.value, entry.profile.value)

        def _row_for_session(self, session_id: str) -> int:
            for row in range(self.table.proxy.rowCount()):
                if self.table.proxy.index(row, 0).data(Qt.ItemDataRole.UserRole) == session_id:
                    return row
            return -1

        @property
        def detail_session_id(self) -> str | None:
            return self._detail_session_id

        @property
        def visible_session_ids(self) -> set[str]:
            return {identifier for identifier, _cells in self.table.records.records}

        def _copy_selected(self) -> None:
            entry = self.selected_entry()
            if entry is not None and entry.confirmed_text:
                QGuiApplication.clipboard().setText(entry.confirmed_text)

        def _insert_selected(self) -> None:
            entry = self.selected_entry()
            if entry is not None and entry.confirmed_text:
                self.insert_requested.emit(entry.confirmed_text)

        def _clear_history(self) -> None:
            if QMessageBox.question(
                self, "Очистить историю?", "Удалить завершённые записи и их текст?",
                defaultButton=QMessageBox.StandardButton.No,
            ) == QMessageBox.StandardButton.Yes:
                self._page = 0
                self.clear_requested.emit()

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
            if confirm_delete(
                self,
                "Удалить запись?",
                "Текст и связанные с этой сессией локальные данные будут удалены.",
            ):
                self.delete_requested.emit(session_id)

        def remove_session(self, session_id: str) -> None:
            self._entries.pop(session_id, None)
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
