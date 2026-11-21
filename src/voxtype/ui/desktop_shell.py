"""Sidebar, page header and keyboard command palette for the native desktop shell."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .motion import AnimationCoordinator
from .obsidian import (
    IconProvider,
    NotificationCenter,
    VoxButton,
    VoxEmptyState,
    VoxIconButton,
    VoxSearchField,
    VoxSectionHeader,
    VoxSeparator,
    VoxStatusBadge,
)
from .theme import theme_manager

PAGES = {
    "home": ("Главная", "Home", "Локальная голосовая диктовка", "Local voice dictation", "home"),
    "dictation": (
        "Диктовка",
        "Dictation",
        "Запись и текущий результат",
        "Recording and current result",
        "mic",
    ),
    "history": (
        "История",
        "History",
        "Поиск и повторное использование текста",
        "Search and reuse transcripts",
        "history",
    ),
    "files": (
        "Транскрипции",
        "Transcriptions",
        "Очередь аудиофайлов и редактор",
        "Audio queue and editor",
        "files",
    ),
    "profiles": (
        "Профили",
        "Profiles",
        "Настройки диктовки по приложениям",
        "App-specific dictation preferences",
        "profiles",
    ),
    "dictionary": (
        "Словарь",
        "Vocabulary",
        "Произношения и написание терминов",
        "Pronunciation and spelling",
        "dictionary",
    ),
    "snippets": (
        "Шаблоны",
        "Snippets",
        "Многоразовые тексты и параметры",
        "Reusable text and placeholders",
        "snippets",
    ),
    "workflows": (
        "Процессы",
        "Workflows",
        "Локальные действия с результатом",
        "Local actions for finished transcripts",
        "workflow",
    ),
    "settings": (
        "Настройки",
        "Settings",
        "Устройства, распознавание и интерфейс",
        "Devices, recognition and appearance",
        "settings",
    ),
    "models": (
        "Модели",
        "Models",
        "Установленные модели распознавания",
        "Installed recognition models",
        "files",
    ),
}


class DesktopShell(QWidget):
    page_requested = Signal(str)
    about_requested = Signal()
    palette_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.current = "home"
        self.locale = "ru"
        self._manual_collapse: bool | None = None
        self._collapsed: bool | None = None
        self._sidebar_destination: bool | None = None
        self._transition_view: QWidget | None = None
        self._selection_target: QPushButton | None = None
        self.pages: dict[str, QWidget] = {}
        self.actions: dict[str, tuple[str, Callable[[], None]]] = {}
        self.buttons: dict[str, QPushButton] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = self.sidebar = QFrame(self)
        sidebar.setObjectName("obsidianSidebar")
        navigation = QVBoxLayout(sidebar)
        navigation.setContentsMargins(9, 12, 9, 12)
        navigation.setSpacing(3)
        masthead = QHBoxLayout()
        fold = VoxIconButton("menu", "Свернуть или развернуть навигацию", sidebar)
        fold.clicked.connect(self.toggle_navigation)
        masthead.addWidget(fold)
        self.brand = VoxSectionHeader("VoxType", sidebar)
        self.brand.setWordWrap(False)
        self.brand.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        masthead.addWidget(self.brand, 1)
        navigation.addLayout(masthead)
        navigation.addSpacing(9)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key in PAGES:
            if key == "models":
                continue
            button = VoxButton("", sidebar)
            button.setFixedHeight(40)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.setCheckable(True)
            button.setProperty("navRow", True)
            button.clicked.connect(lambda _checked=False, page=key: self.page_requested.emit(page))
            group.addButton(button)
            self.buttons[key] = button
            navigation.addWidget(button)
        navigation.addStretch()
        navigation.addWidget(VoxSeparator(sidebar))
        self.model = QLabel("—", sidebar)
        self.model.setWordWrap(True)
        self.model.setProperty("muted", True)
        self.privacy = VoxStatusBadge("Обработка на устройстве", sidebar)
        self.about = VoxIconButton("about", "Диагностика и сведения о VoxType", sidebar)
        self.about.clicked.connect(self.about_requested)
        for widget in (self.model, self.privacy, self.about):
            navigation.addWidget(widget)
        self._selection = QFrame(sidebar)
        self._selection.setFixedSize(3, 24)
        self._selection.setObjectName("navIndicator")
        self._selection.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._selection.hide()
        layout.addWidget(sidebar)

        body = QWidget(self)
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        header = QFrame(body)
        header.setObjectName("obsidianHeader")
        header.setFixedHeight(68)
        line = QHBoxLayout(header)
        line.setContentsMargins(28, 0, 20, 0)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.title = VoxSectionHeader("", header, role="page")
        self.subtitle = QLabel(header)
        self.subtitle.setProperty("muted", True)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        line.addLayout(titles, 1)
        self.engine = VoxStatusBadge("—", header)
        self.profile = QLabel(header)
        self.profile.setProperty("muted", True)
        self.context_action = VoxButton("", header)
        self.context_action.clicked.connect(self._run_context_action)
        search = VoxIconButton("search", "Палитра команд", header)
        search.clicked.connect(self.palette_requested)
        settings = VoxIconButton("settings", "Настройки", header)
        settings.clicked.connect(lambda: self.page_requested.emit("settings"))
        for widget in (self.engine, self.profile, self.context_action, search, settings):
            line.addWidget(widget)
        column.addWidget(header)
        self.stack = QStackedWidget(body)
        column.addWidget(self.stack, 1)
        layout.addWidget(body, 1)
        self.notifications = NotificationCenter(self)
        theme_manager(QApplication.instance()).theme_changed.connect(self._refresh_icons)
        self._apply_density(True)
        self._labels()

    def install_page(self, key: str, widget: QWidget) -> None:
        previous = self.pages.get(key)
        self.pages[key] = widget
        if self.stack.indexOf(widget) == -1:
            self.stack.addWidget(widget)
        if previous is not None and previous is not widget and previous not in self.pages.values():
            if self._transition_view is previous:
                AnimationCoordinator.instance().cancel(previous, "opacity")
                self._transition_view = None
            self.stack.removeWidget(previous)
            if previous.property("voxtypeUnavailable"):
                previous.deleteLater()

    def show_page(self, key: str) -> None:
        widget = self.pages.get(key)
        if widget is None:
            return
        previous = self.stack.currentWidget()
        previous_key = self.current
        coordinator = AnimationCoordinator.instance()
        if self._transition_view is not None:
            coordinator.cancel(self._transition_view, "opacity")
            self._transition_view = None
        self.current = key
        self.stack.setCurrentWidget(widget)
        widget.show()
        self._labels()
        if previous_key != key:
            heavy = key in {
                "history",
                "files",
                "settings",
                "models",
                "profiles",
                "dictionary",
                "snippets",
                "workflows",
            }
            transition_view = self.title if heavy or previous is widget else widget
            self._transition_view = transition_view

            def finished() -> None:
                if self._transition_view is transition_view:
                    self._transition_view = None

            coordinator.animate_opacity(
                transition_view, 0.65, 1.0, coordinator.PAGE, finished=finished
            )

    def unavailable(self, key: str, title: str, detail: str) -> None:
        if key not in self.pages:
            placeholder = VoxEmptyState(PAGES[key][4], title, detail, self.stack)
            placeholder.setProperty("voxtypeUnavailable", True)
            self.install_page(key, placeholder)
        self.show_page(key)

    def set_locale(self, locale: str) -> None:
        self.locale = locale
        self._labels()

    def set_context_action(self, page: str, label: str, action: Callable[[], None]) -> None:
        self.actions[page] = (label, action)
        if page == self.current:
            self._labels()

    def _run_context_action(self) -> None:
        action = self.actions.get(self.current)
        if action is not None:
            action[1]()

    def _labels(self) -> None:
        locale = int(self.locale == "en")
        metadata = PAGES[self.current]
        self.title.setText(metadata[locale])
        self.subtitle.setText(metadata[2 + locale])
        self.privacy.setText("On-device processing" if locale else "Обработка на устройстве")
        action = self.actions.get(self.current)
        self.context_action.setVisible(action is not None and self.width() >= 800)
        if action is not None:
            self.context_action.setText(action[0])
            self.context_action.setAccessibleName(action[0])
        selected_key = "settings" if self.current == "models" else self.current
        for key, button in self.buttons.items():
            text = PAGES[key][locale]
            button.setText("" if self._collapsed else text)
            button.setToolTip(text)
            button.setAccessibleName(text)
            button.setChecked(key == selected_key)
        self._move_selection(self.buttons[selected_key])

    def _move_selection(self, button: QPushButton, *, immediate: bool = False) -> None:
        target = button.y() + (button.height() - self._selection.height()) // 2
        same = button is self._selection_target
        if same and not immediate:
            return
        self._selection_target = button
        coordinator = AnimationCoordinator.instance()
        if immediate or self._selection.isHidden():
            coordinator.cancel(self._selection, "selection")
            self._selection.move(9, target)
            self._selection.show()
        else:
            coordinator.animate_value(
                self._selection,
                self._selection.y(),
                target,
                lambda y: self._selection.move(9, int(y)),
                coordinator.SELECTION,
                channel="selection",
            )
        self._selection.raise_()

    def _refresh_icons(self, _theme: object = None) -> None:
        for key, button in self.buttons.items():
            button.setIcon(IconProvider.icon(PAGES[key][4], 18, button.devicePixelRatioF()))

    def toggle_navigation(self) -> None:
        self._manual_collapse = not bool(self._sidebar_destination)
        self._apply_density()

    def _apply_density(self, force: bool = False) -> None:
        collapsed = self.width() < 900 if self._manual_collapse is None else self._manual_collapse
        if force or self._sidebar_destination != collapsed:
            self._sidebar_destination = collapsed
            width = 64 if collapsed else 224
            coordinator = AnimationCoordinator.instance()

            def apply_width(value: int) -> None:
                self.sidebar.setFixedWidth(value)

            def finish() -> None:
                self._collapsed = collapsed
                for widget in (self.brand, self.privacy, self.model):
                    widget.setVisible(not collapsed)
                self._labels()

            coordinator.animate_value(
                self.sidebar,
                self.sidebar.width(),
                width,
                apply_width,
                0 if force else coordinator.SIDEBAR,
                channel="sidebar-width",
                finished=finish,
            )
        self.profile.setVisible(self.width() >= 900)
        self.subtitle.setVisible(self.width() >= 800)
        self.context_action.setVisible(self.current in self.actions and self.width() >= 800)

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        self._apply_density()
        self.notifications.reposition()

    def showEvent(self, event: object) -> None:
        super().showEvent(event)
        selected = self.buttons["settings" if self.current == "models" else self.current]
        self._move_selection(selected, immediate=True)


class CommandPalette(QDialog):
    def __init__(self, entries: list[tuple[str, Callable[[], None]]], parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Команды VoxType")
        self.resize(580, 420)
        self.entries = entries
        self.search = VoxSearchField("Команда или раздел…", self)
        self.results = QListWidget(self)
        self.results.setAccessibleName("Доступные команды")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.addWidget(self.search)
        layout.addWidget(self.results)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._run)
        self.search.installEventFilter(self)
        self.results.itemActivated.connect(self._run)
        self._filter("")
        self.search.setFocus()

    def showEvent(self, event: object) -> None:
        super().showEvent(event)
        self.search.setFocus(Qt.FocusReason.OtherFocusReason)
        coordinator = AnimationCoordinator.instance()
        coordinator.animate_opacity(self, 0.55, 1, coordinator.PALETTE)

    def _filter(self, text: str) -> None:
        query = text.strip().casefold()
        matches = [
            (not label.casefold().startswith(query), index, label)
            for index, (label, _action) in enumerate(self.entries)
            if query in label.casefold()
        ]
        self.results.clear()
        for _rank, index, label in sorted(matches):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.results.addItem(item)
        if not matches:
            empty = QListWidgetItem("Команды не найдены")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.results.addItem(empty)
        else:
            self.results.setCurrentRow(0)

    def eventFilter(self, watched: object, event: object) -> bool:
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            step = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1}.get(event.key())
            if step is not None:
                self.results.setCurrentRow(
                    max(0, min(self.results.count() - 1, self.results.currentRow() + step))
                )
                return True
        return super().eventFilter(watched, event)

    def _run(self, _item: QListWidgetItem | None = None) -> None:
        item = self.results.currentItem()
        if item is not None and item.data(Qt.ItemDataRole.UserRole) is not None:
            action = self.entries[item.data(Qt.ItemDataRole.UserRole)][1]
            self.accept()
            action()
