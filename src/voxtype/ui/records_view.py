"""Compact, sortable Qt models for local records."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QBrush, QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QWidget,
)

from .motion import AnimationCoordinator
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

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.records)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.headings)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> str | None:
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

    def paint(self, painter, option, index) -> None:
        strength = self.view.highlight_strength(index)
        if strength is None:
            super().paint(painter, option, index)
            return
        tinted = QStyleOptionViewItem(option)
        self.initStyleOption(tinted, index)
        tinted.state &= ~QStyle.StateFlag.State_Selected
        theme = theme_manager(QApplication.instance())
        base = tinted.palette.color(QPalette.ColorRole.Base)
        accent = QColor(theme.get_token("accent_soft"))
        tinted.backgroundBrush = QBrush(
            QColor.fromRgbF(
                base.redF() + (accent.redF() - base.redF()) * strength,
                base.greenF() + (accent.greenF() - base.greenF()) * strength,
                base.blueF() + (accent.blueF() - base.blueF()) * strength,
            )
        )
        self.view.style().drawControl(
            QStyle.ControlElement.CE_ItemViewItem, tinted, painter, self.view
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
        self._selection_progress = 1.0
        self.setItemDelegate(_SelectionDelegate(self))
        self.selectionModel().currentRowChanged.connect(self._selection_changed)

    def selected_key(self) -> str:
        index = self.currentIndex()
        return str(index.data(Qt.ItemDataRole.UserRole) or "") if index.isValid() else ""

    def _selection_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        # Clear a previous retreating row if another selection interrupts the transition.
        self._invalidate_row(self._previous_row)
        self._previous_row = QPersistentModelIndex(previous)
        self._selected_row = QPersistentModelIndex(current)
        motion = AnimationCoordinator.instance()
        motion.animate_value(
            self,
            0.0,
            1.0,
            self._set_selection_progress,
            motion.HOVER,
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
        if index.isValid():
            row = index.row()
            self.viewport().update(
                QRect(
                    0, self.rowViewportPosition(row), self.viewport().width(), self.rowHeight(row)
                )
            )

    def highlight_strength(self, index: QModelIndex) -> float | None:
        if self._selection_progress >= 1:
            return None
        if self._selected_row.isValid() and index.row() == self._selected_row.row():
            return self._selection_progress
        if self._previous_row.isValid() and index.row() == self._previous_row.row():
            return 1.0 - self._selection_progress
        return None
