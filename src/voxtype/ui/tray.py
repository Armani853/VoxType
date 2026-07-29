"""System tray presentation and command routing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from ._optional import PYSIDE6_AVAILABLE, require_pyside6
from .models import AppStatus, DictationLanguage, UIState, snapshot_from

if PYSIDE6_AVAILABLE:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QAction, QActionGroup, QIcon
    from PySide6.QtWidgets import QMenu, QSystemTrayIcon

    from .theme import create_app_icon, state_color

    class TrayController(QObject):
        dictation_toggle_requested = Signal()
        pause_toggle_requested = Signal()
        language_change_requested = Signal(str)
        open_window_requested = Signal()
        settings_requested = Signal()
        quit_requested = Signal()

        def __init__(
            self,
            controller: object | None = None,
            parent: QObject | None = None,
        ) -> None:
            super().__init__(parent)
            self._state = UIState()
            self._controller_connections: list[tuple[Any, Callable[..., Any]]] = []
            self.icon = QSystemTrayIcon(create_app_icon(), self)
            self.icon.setObjectName("voxtypeTrayIcon")
            self.icon.setToolTip("VoxType — готово")
            self.menu = QMenu()
            self.menu.setObjectName("voxtypeTrayMenu")
            self.toggle_action = QAction("Начать диктовку", self.menu)
            self.toggle_action.setObjectName("trayToggleAction")
            self.pause_action = QAction("Пауза", self.menu)
            self.pause_action.setObjectName("trayPauseAction")
            self.pause_action.setEnabled(False)
            self.menu.addAction(self.toggle_action)
            self.menu.addAction(self.pause_action)

            language_menu = self.menu.addMenu("Язык")
            language_menu.setObjectName("trayLanguageMenu")
            self.language_group = QActionGroup(self)
            self.language_group.setExclusive(True)
            self.ru_action = QAction("RU — Русский", language_menu)
            self.ru_action.setCheckable(True)
            self.ru_action.setData("ru")
            self.en_action = QAction("EN — English", language_menu)
            self.en_action.setCheckable(True)
            self.en_action.setData("en")
            self.language_group.addAction(self.ru_action)
            self.language_group.addAction(self.en_action)
            language_menu.addActions((self.ru_action, self.en_action))
            self.ru_action.setChecked(True)

            self.menu.addSeparator()
            self.open_action = QAction("Открыть окно", self.menu)
            self.open_action.setObjectName("trayOpenAction")
            self.settings_action = QAction("Настройки", self.menu)
            self.settings_action.setObjectName("traySettingsAction")
            self.menu.addAction(self.open_action)
            self.menu.addAction(self.settings_action)
            self.menu.addSeparator()
            self.quit_action = QAction("Выход", self.menu)
            self.quit_action.setObjectName("trayQuitAction")
            self.menu.addAction(self.quit_action)
            self.icon.setContextMenu(self.menu)
            self._connect_ui()
            if controller is not None:
                self.bind_controller(controller)

        def _connect_ui(self) -> None:
            self.toggle_action.triggered.connect(self.dictation_toggle_requested)
            self.pause_action.triggered.connect(self.pause_toggle_requested)
            self.language_group.triggered.connect(self._language_triggered)
            self.open_action.triggered.connect(self.open_window_requested)
            self.settings_action.triggered.connect(self.settings_requested)
            self.quit_action.triggered.connect(self.quit_requested)
            self.icon.activated.connect(self._activated)

        def bind_controller(self, controller: object) -> None:
            self.unbind_controller()
            for signal, method_name in (
                (self.dictation_toggle_requested, "toggle_dictation"),
                (self.pause_toggle_requested, "toggle_pause"),
                (self.quit_requested, "shutdown"),
            ):
                method = getattr(controller, method_name, None)
                if callable(method):
                    signal.connect(method)
                    self._controller_connections.append((signal, method))
            switch = getattr(controller, "switch_language", None)
            if callable(switch):

                def callback(_language: str) -> None:
                    switch()

                self.language_change_requested.connect(callback)
                self._controller_connections.append((self.language_change_requested, callback))

        def unbind_controller(self) -> None:
            for signal, callback in self._controller_connections:
                try:
                    signal.disconnect(callback)
                except (RuntimeError, TypeError):
                    continue
            self._controller_connections.clear()

        def _language_triggered(self, action: QAction) -> None:
            language = DictationLanguage.coerce(action.data())
            if language is not self._state.language:
                self.language_change_requested.emit(language.value)

        def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
            if reason in {
                QSystemTrayIcon.ActivationReason.Trigger,
                QSystemTrayIcon.ActivationReason.DoubleClick,
            }:
                self.open_window_requested.emit()
            elif reason is QSystemTrayIcon.ActivationReason.MiddleClick:
                self.dictation_toggle_requested.emit()

        def update_snapshot(self, snapshot: UIState | object) -> None:
            state = snapshot_from(snapshot)
            self._state = state
            active = state.state.active
            self.toggle_action.setText("Остановить диктовку" if active else "Начать диктовку")
            self.pause_action.setEnabled(active and state.state is not AppStatus.FINISHING)
            paused = state.state in {AppStatus.PAUSED, AppStatus.PAUSED_TARGET_LOST}
            self.pause_action.setText("Продолжить" if paused else "Пауза")
            self.ru_action.setChecked(state.language is DictationLanguage.RU)
            self.en_action.setChecked(state.language is DictationLanguage.EN)
            status = {
                AppStatus.IDLE: "готово",
                AppStatus.LISTENING: "слушаю",
                AppStatus.SPEECH_DETECTED: "слышу речь",
                AppStatus.TRANSCRIBING: "распознаю",
                AppStatus.PAUSED: "пауза",
                AppStatus.PAUSED_TARGET_LOST: "цель неактивна",
                AppStatus.FINISHING: "завершаю",
                AppStatus.ERROR_RECOVERABLE: "требуется внимание",
                AppStatus.ERROR_FATAL: "ошибка",
            }.get(state.state, state.state.value.lower())
            self.icon.setToolTip(f"VoxType — {status} · {state.language.value.upper()}")
            self.icon.setIcon(self._icon_for_state(state.state))

        @staticmethod
        def _icon_for_state(state: AppStatus) -> QIcon:
            if state in {AppStatus.ERROR_FATAL, AppStatus.ERROR_RECOVERABLE}:
                return cast(QIcon, create_app_icon(color=state_color("error")))
            if state in {AppStatus.PAUSED, AppStatus.PAUSED_TARGET_LOST}:
                return cast(QIcon, create_app_icon(color=state_color("warning")))
            if state.active:
                return cast(QIcon, create_app_icon(color=state_color("success")))
            return cast(QIcon, create_app_icon())

        def show(self) -> bool:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                return False
            self.icon.show()
            return True

        def hide(self) -> None:
            self.icon.hide()

        def show_message(
            self,
            title: str,
            message: str,
            *,
            critical: bool = False,
            timeout_ms: int = 5000,
        ) -> None:
            kind = (
                QSystemTrayIcon.MessageIcon.Critical
                if critical
                else QSystemTrayIcon.MessageIcon.Information
            )
            self.icon.showMessage(title, message, kind, max(1000, int(timeout_ms)))

        def close(self) -> None:
            self.unbind_controller()
            self.icon.hide()
            self.menu.close()

else:

    class TrayController:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs
            require_pyside6()
