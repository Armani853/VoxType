"""Compact, sortable Qt models for local records."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView, QWidget


class RecordsModel(QAbstractTableModel):
    def __init__(self, headings: tuple[str, ...], parent: QWidget) -> None:
        super().__init__(parent)
        self.headings = headings
        self.records: list[tuple[str, tuple[str, ...]]] = []

    def replace(self, records: Sequence[tuple[str, tuple[str, ...]]]) -> None:
        self.beginResetModel()
        self.records = list(records)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headings)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> str | None:
        if not index.isValid() or not 0 <= index.row() < len(self.records):
            return None
        key, values = self.records[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return key
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return values[index.column()]
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> str | None:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headings[section]
        return None


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

    def selected_key(self) -> str:
        index = self.currentIndex()
        return str(index.data(Qt.ItemDataRole.UserRole) or "") if index.isValid() else ""
