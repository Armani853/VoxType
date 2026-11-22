"""Human-readable hardware, runtime and self-test diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from voxtype.monitoring.assistant import DiagnosticFinding

from ._optional import PYSIDE6_AVAILABLE, require_pyside6
from .models import DiagnosticReport, diagnostic_report_from

if PYSIDE6_AVAILABLE:
    from PySide6.QtCore import QTimer, Signal
    from PySide6.QtGui import QColor, QGuiApplication
    from PySide6.QtWidgets import (
        QCheckBox,
        QDialog,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

    from .theme import SPACING, state_color

    class DiagnosticsWindow(QDialog):
        refresh_requested = Signal()
        self_test_requested = Signal()
        open_log_requested = Signal()
        export_requested = Signal()
        resources_requested = Signal()
        updates_requested = Signal()
        finding_action_requested = Signal(str)

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setObjectName("diagnosticsWindow")
            self.setWindowTitle("Диагностика — VoxType")
            self.resize(880, 650)
            self.setMinimumSize(700, 500)
            self._report = DiagnosticReport()
            self._tables: dict[str, QTableWidget] = {}
            self._build_ui()
            self._connect_ui()
            self.set_report(self._report)
            self._resource_timer = QTimer(self, interval=1500)
            self._resource_timer.timeout.connect(self.resources_requested.emit)
            self.live_resources.toggled.connect(self._update_resource_timer)

        def _update_resource_timer(self) -> None:
            if self.isVisible() and self.live_resources.isChecked():
                self._resource_timer.start()
                self.resources_requested.emit()
            else:
                self._resource_timer.stop()
                self.resource_label.clear()

        def showEvent(self, event: object) -> None:
            super().showEvent(event)
            self._update_resource_timer()

        def hideEvent(self, event: object) -> None:
            self._resource_timer.stop()
            super().hideEvent(event)

        def show_resource_counters(self, summary: str) -> None:
            if summary != self.resource_label.text():
                self.resource_label.setText(summary)

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
            heading = QLabel("Диагностика", self)
            heading.setProperty("heading", True)
            self.summary_label = QLabel("Диагностика ещё не выполнялась", self)
            self.summary_label.setProperty("muted", True)
            self.summary_label.setWordWrap(True)
            heading_column.addWidget(heading)
            heading_column.addWidget(self.summary_label)
            header.addLayout(heading_column)
            header.addStretch(1)
            self.refresh_button = QPushButton("Обновить", self)
            self.refresh_button.setProperty("flat", True)
            self.self_test_button = QPushButton("Выполнить self-test", self)
            self.self_test_button.setProperty("compactPrimary", True)
            header.addWidget(self.refresh_button)
            header.addWidget(self.self_test_button)
            self.updates_button = QPushButton("Обновления", self)
            self.updates_button.clicked.connect(lambda: self.updates_requested.emit())
            header.addWidget(self.updates_button)
            root.addLayout(header)
            self.live_resources = QCheckBox("Показывать нагрузку в реальном времени", self)
            self.resource_label = QLabel(self)
            self.resource_label.setWordWrap(True)
            self.resource_label.setProperty("muted", True)
            root.addWidget(self.live_resources)
            root.addWidget(self.resource_label)

            self.progress = QProgressBar(self)
            self.progress.setRange(0, 0)
            self.progress.setTextVisible(False)
            self.progress.hide()
            root.addWidget(self.progress)
            self.tabs = QTabWidget(self)
            self.tabs.setObjectName("diagnosticsTabs")
            self.overview_tab = QWidget(self.tabs)
            overview_layout = QVBoxLayout(self.overview_tab)
            overview_layout.setContentsMargins(14, 14, 14, 14)
            self.checks_table = QTableWidget(0, 3, self.overview_tab)
            self.checks_table.setObjectName("checksTable")
            self.checks_table.setHorizontalHeaderLabels(("Проверка", "Результат", "Подробности"))
            self.checks_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.checks_table.setShowGrid(False)
            self.checks_table.verticalHeader().setVisible(False)
            self.checks_table.verticalHeader().setDefaultSectionSize(38)
            checks_header = self.checks_table.horizontalHeader()
            checks_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            checks_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            checks_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            overview_layout.addWidget(self.checks_table)
            self.findings = QVBoxLayout()
            overview_layout.addLayout(self.findings)
            self.tabs.addTab(self.overview_tab, "Проверки")
            for key, title in (
                ("hardware", "Компьютер"),
                ("audio", "Аудио"),
                ("runtime", "Производительность"),
                ("libraries", "Версии"),
            ):
                table = self._make_key_value_table()
                self._tables[key] = table
                self.tabs.addTab(table, title)
            log_tab = QWidget(self.tabs)
            log_layout = QVBoxLayout(log_tab)
            log_layout.setContentsMargins(12, 12, 12, 12)
            privacy = QLabel(
                "Фрагмент технического лога. Продиктованный текст и PCM-аудио сюда не записываются.",
                log_tab,
            )
            privacy.setProperty("muted", True)
            privacy.setWordWrap(True)
            self.log_edit = QPlainTextEdit(log_tab)
            self.log_edit.setObjectName("diagnosticLogExcerpt")
            self.log_edit.setReadOnly(True)
            self.log_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            log_layout.addWidget(privacy)
            log_layout.addWidget(self.log_edit, 1)
            self.tabs.addTab(log_tab, "Лог")
            root.addWidget(self.tabs, 1)

            actions = QHBoxLayout()
            self.open_log_button = QPushButton("Открыть полный лог", self)
            self.copy_button = QPushButton("Копировать отчёт", self)
            self.export_button = QPushButton("Экспортировать…", self)
            self.close_button = QPushButton("Закрыть", self)
            self.close_button.setProperty("flat", True)
            actions.addWidget(self.open_log_button)
            actions.addWidget(self.copy_button)
            actions.addWidget(self.export_button)
            actions.addStretch(1)
            actions.addWidget(self.close_button)
            root.addLayout(actions)

        def _make_key_value_table(self) -> QTableWidget:
            table = QTableWidget(0, 2, self.tabs)
            table.setHorizontalHeaderLabels(("Параметр", "Значение"))
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setShowGrid(False)
            table.verticalHeader().setVisible(False)
            table.verticalHeader().setDefaultSectionSize(38)
            header = table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            return table

        def _connect_ui(self) -> None:
            self.refresh_button.clicked.connect(self.refresh_requested)
            self.self_test_button.clicked.connect(self._start_self_test)
            self.open_log_button.clicked.connect(self.open_log_requested)
            self.export_button.clicked.connect(self.export_requested)
            self.copy_button.clicked.connect(self._copy_report)
            self.close_button.clicked.connect(self.close)

        def _start_self_test(self) -> None:
            self.set_self_test_running(True)
            self.self_test_requested.emit()

        def set_self_test_running(self, running: bool, message: str = "") -> None:
            self.progress.setVisible(running)
            self.self_test_button.setEnabled(not running)
            self.refresh_button.setEnabled(not running)
            if message:
                self.summary_label.setText(message)

        def set_report(self, report: DiagnosticReport | Mapping[str, Any] | object) -> None:
            parsed = diagnostic_report_from(report)
            self._report = parsed
            self.summary_label.setText(parsed.summary)
            self._fill_checks(parsed.checks)
            self._fill_table(self._tables["hardware"], parsed.hardware)
            self._fill_table(self._tables["audio"], parsed.audio)
            self._fill_table(self._tables["runtime"], parsed.runtime)
            self._fill_table(self._tables["libraries"], parsed.libraries)
            self.log_edit.setPlainText(parsed.log_excerpt)
            self.set_self_test_running(False)

        def set_findings(self, findings: tuple[DiagnosticFinding, ...]) -> None:
            while self.findings.count():
                item = self.findings.takeAt(0)
                if item is not None and (widget := item.widget()) is not None:
                    widget.deleteLater()
            for finding in findings:
                button = QPushButton(finding.label, self.overview_tab)
                button.setToolTip(finding.detail)
                button.setAccessibleDescription(finding.detail)
                button.clicked.connect(
                    lambda _checked=False, action=finding.action: (
                        self.finding_action_requested.emit(action.value)
                    )
                )
                self.findings.addWidget(button)

        def _fill_checks(self, checks: tuple[tuple[str, bool, str], ...]) -> None:
            self.checks_table.setRowCount(len(checks))
            for row, (name, ok, detail) in enumerate(checks):
                name_item = QTableWidgetItem(name)
                result_item = QTableWidgetItem("Готово" if ok else "Проблема")
                result_item.setForeground(QColor(state_color("success" if ok else "error")))
                detail_item = QTableWidgetItem(detail)
                detail_item.setToolTip(detail)
                self.checks_table.setItem(row, 0, name_item)
                self.checks_table.setItem(row, 1, result_item)
                self.checks_table.setItem(row, 2, detail_item)

        @staticmethod
        def _fill_table(table: QTableWidget, values: Mapping[str, str]) -> None:
            table.setRowCount(len(values))
            for row, (key, value) in enumerate(values.items()):
                key_item = QTableWidgetItem(key)
                value_item = QTableWidgetItem(value)
                value_item.setToolTip(value)
                table.setItem(row, 0, key_item)
                table.setItem(row, 1, value_item)

        def report_as_text(self) -> str:
            lines = ["VoxType — диагностический отчёт", self._report.summary, ""]
            sections = (
                ("Компьютер", self._report.hardware),
                ("Аудио", self._report.audio),
                ("Производительность", self._report.runtime),
                ("Версии", self._report.libraries),
            )
            for title, values in sections:
                lines.append(title)
                lines.extend(f"{key}: {value}" for key, value in values.items())
                lines.append("")
            if self._report.checks:
                lines.append("Проверки")
                lines.extend(
                    f"{'OK' if ok else 'ERROR'} — {name}: {detail}"
                    for name, ok, detail in self._report.checks
                )
            return "\n".join(lines).strip()

        def _copy_report(self) -> None:
            QGuiApplication.clipboard().setText(self.report_as_text())

else:

    class DiagnosticsWindow:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs
            require_pyside6()
