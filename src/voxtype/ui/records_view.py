"""Compact, sortable Qt models for local records."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QBrush, QColor, QContextMenuEvent, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QMenu,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QWidget,
)

from .motion import DURATIONS, AnimationCoordinator, blend
from .theme import theme_manager


class RecordsModel(QAbstractTableModel):
    def __init__(self, headings: tuple[str, ...], parent: QWidget) -> None:
        super().__init__(parent)
        self.headings = headings
        self.records: list[tuple[str, tuple[str, ...]]] = []

    def replace(self, records: Sequence[tuple[str, tuple[str, ...]]]) -> None:
        incoming = list(records)
        before = [key for key, _ in self.records]
        after = [key for key, _ in incoming]
        bounds = min(len(before), len(after))
        prefix = 0
        while prefix < bounds and before[prefix] == after[prefix]:
            prefix += 1
        suffix = 0
        while suffix < bounds - prefix and before[-1 - suffix] == after[-1 - suffix]:
            suffix += 1
        old_end = len(before) - suffix
        new_end = len(after) - suffix
        if old_end > prefix:
            self.beginRemoveRows(QModelIndex(), prefix, old_end - 1)
            del self.records[prefix:old_end]
            self.endRemoveRows()
        if new_end > prefix:
            self.beginInsertRows(QModelIndex(), prefix, new_end - 1)
            self.records[prefix:prefix] = incoming[prefix:new_end]
            self.endInsertRows()
        for row, value in enumerate(incoming):
            old = self.records[row][1]
            if old == value[1]:
                continue
            self.records[row] = value
            changed = [
                index for index, (a, b) in enumerate(zip(old, value[1], strict=True)) if a != b
            ]
            if changed:
                self.dataChanged.emit(
                    self.index(row, changed[0]),
                    self.index(row, changed[-1]),
                    [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole],
                )

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.records)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.headings)

    def data(
        self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> str | None:
        if not index.isValid() or not 0 <= index.row() < len(self.records):
            return None
        key, values = self.records[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return key
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return values[index.column()]
        return None

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> str | None:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headings[section]
        return None


class _SelectionDelegate(QStyledItemDelegate):
    def __init__(self, view: RecordsView) -> None:
        super().__init__(view)
        self.view = view

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        strength = self.view.highlight_strength(index)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = self.view.is_hovered(index)
        if strength is None and not (selected or hovered):
            super().paint(painter, option, index)
            return
        tinted = QStyleOptionViewItem(option)
        self.initStyleOption(tinted, index)
        tinted.state &= ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver)
        theme = theme_manager(QApplication.instance())
        base = (
            QColor(theme.get_token("motion_hover"))
            if hovered
            else tinted.palette.color(QPalette.ColorRole.Base)
        )
        amount = strength if strength is not None else float(selected)
        accent = QColor(theme.get_token("motion_selected"))
        tinted.backgroundBrush = QBrush(blend(base, accent, amount))
        self.view.style().drawControl(
            QStyle.ControlElement.CE_ItemViewItem, tinted, painter, self.view
        )
        if amount and index.column() == self.view.horizontalHeader().logicalIndexAt(0):
            mark = QColor(theme.get_token("motion_focus"))
            mark.setAlphaF(amount)
            painter.fillRect(
                QRect(
                    option.rect.left(), option.rect.top() + 4, 2, max(0, option.rect.height() - 8)
                ),
                mark,
            )


class RecordsView(QTableView):
    def __init__(self, headings: tuple[str, ...], parent: QWidget, *, height: int = 44) -> None:
        super().__init__(parent)
        self.records = RecordsModel(headings, self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.records)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setModel(self.proxy)
        self.setSortingEnabled(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(height)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._previous_row = QPersistentModelIndex()
        self._selected_row = QPersistentModelIndex()
        self._hovered_row = QPersistentModelIndex()
        self._selection_progress = 1.0
        self._selected_start = 0.0
        self._previous_start = 1.0
        self._row_actions: tuple[QPushButton, ...] = ()
        self.setItemDelegate(_SelectionDelegate(self))
        self.selectionModel().currentRowChanged.connect(self._selection_changed)
        self.setMouseTracking(True)
        self.entered.connect(self._hover_row)
        self.viewportEntered.connect(lambda: self._hover_row(QModelIndex()))

    def selected_key(self) -> str:
        index = self.currentIndex()
        return str(index.data(Qt.ItemDataRole.UserRole) or "") if index.isValid() else ""

    def set_row_actions(self, *buttons: QPushButton) -> None:
        self._row_actions = buttons

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        if not self._row_actions:
            super().contextMenuEvent(event)
            return
        index = (
            self.currentIndex()
            if event.reason() is QContextMenuEvent.Reason.Keyboard
            else self.indexAt(event.pos())
        )
        if not index.isValid():
            return
        self.setCurrentIndex(index)
        menu = QMenu(self)
        for button in self._row_actions:
            if button.isVisible():
                action = menu.addAction(button.icon(), button.text(), button.click)
                action.setEnabled(button.isEnabled())
        if not menu.isEmpty():
            position = (
                self.viewport().mapToGlobal(self.visualRect(index).bottomLeft())
                if event.reason() is QContextMenuEvent.Reason.Keyboard
                else event.globalPos()
            )
            menu.exec(position)
            event.accept()
        menu.deleteLater()

    def _selection_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        before = self.highlight_strength(previous)
        arriving = self.highlight_strength(current)
        self._previous_start = before if before is not None else 1.0
        self._selected_start = arriving if arriving is not None else 0.0
        self._invalidate_row(self._previous_row)
        self._previous_row = QPersistentModelIndex(previous)
        self._selected_row = QPersistentModelIndex(current)
        motion = AnimationCoordinator.instance()
        motion.animate_value(
            self,
            0.0,
            1.0,
            self._set_selection_progress,
            DURATIONS.selection,
            channel="row-selection",
            retarget=False,
        )

    def _set_selection_progress(self, progress: float) -> None:
        self._selection_progress = progress
        self._invalidate_row(self._previous_row)
        self._invalidate_row(self._selected_row)
        if progress >= 1:
            self._previous_row = QPersistentModelIndex()

    def _invalidate_row(self, index: QPersistentModelIndex) -> None:
        if index.isValid() and self.isVisible():
            row = index.row()
            region = QRect(
                0, self.rowViewportPosition(row), self.viewport().width(), self.rowHeight(row)
            )
            viewport = self.viewport()
            if viewport.rect().intersects(region):
                viewport.update(region)

    def _hover_row(self, index: QModelIndex) -> None:
        if self.is_hovered(index):
            return
        previous = self._hovered_row
        self._hovered_row = QPersistentModelIndex(index)
        self._invalidate_row(previous)
        self._invalidate_row(self._hovered_row)

    def viewportEvent(self, event: QEvent) -> bool:
        if event.type() in {QEvent.Type.Hide, QEvent.Type.Leave} and hasattr(self, "_hovered_row"):
            self._hover_row(QModelIndex())
        return super().viewportEvent(event)

    def is_hovered(self, index: QModelIndex | QPersistentModelIndex) -> bool:
        return self._hovered_row.isValid() and index.row() == self._hovered_row.row()

    def highlight_strength(self, index: QModelIndex | QPersistentModelIndex) -> float | None:
        if self._selection_progress >= 1:
            return None
        if self._selected_row.isValid() and index.row() == self._selected_row.row():
            return self._selected_start + (1.0 - self._selected_start) * self._selection_progress
        if self._previous_row.isValid() and index.row() == self._previous_row.row():
            return self._previous_start * (1.0 - self._selection_progress)
        return None
