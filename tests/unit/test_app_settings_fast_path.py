"""Unrelated settings must not rebuild GUI styles or Windows hooks."""

import time
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QWidget

from voxtype.app import DesktopApplication, _WorkerSignals
from voxtype.settings.schema import AppSettings


@pytest.mark.parametrize("change_theme", [False, True])
def test_settings_save_only_reconfigures_changed_services(monkeypatch, change_theme: bool) -> None:  # type: ignore[no-untyped-def]
    previous = AppSettings()
    updated = deepcopy(previous)
    updated.general.minimize_to_tray = not previous.general.minimize_to_tray
    if change_theme:
        updated.general.theme = "dark" if previous.general.theme != "dark" else "light"

    repository = SimpleNamespace(update=MagicMock(return_value=updated), save=MagicMock())
    runtime = SimpleNamespace(
        settings=previous,
        settings_repository=repository,
        startup=MagicMock(),
    )
    controller = SimpleNamespace(
        settings=previous,
        vocabulary=None,
        target_manager=MagicMock(),
        injection_service=MagicMock(),
    )
    app = object.__new__(DesktopApplication)
    app.runtime = runtime
    app.controller = controller
    app.main = MagicMock()
    app.application = object()
    app._configure_capture_factory = MagicMock()
    app._configure_segmenter_factory = MagicMock()
    app._configure_hotkeys = MagicMock(return_value=True)
    theme = MagicMock()
    monkeypatch.setattr("voxtype.app.apply_theme", theme)

    app._save_settings({"general": {"minimize_to_tray": updated.general.minimize_to_tray}})

    assert app.runtime.settings is updated
    app.main.set_minimize_to_tray.assert_called_once_with(updated.general.minimize_to_tray)
    app.main.set_interface_language.assert_not_called()
    app.main.set_toggle_hotkey.assert_not_called()
    app._configure_capture_factory.assert_not_called()
    app._configure_segmenter_factory.assert_not_called()
    app._configure_hotkeys.assert_not_called()
    assert theme.call_count == int(change_theme)


def test_identical_snapshots_do_not_rewrite_all_views() -> None:
    app = object.__new__(DesktopApplication)
    app._last_rendered_snapshot = None
    app.main = MagicMock()
    app.tray = MagicMock()
    app._overlay = MagicMock()

    app._update_views_if_changed(("IDLE", "RU", 0.0))
    app._update_views_if_changed(("IDLE", "RU", 0.0))
    app._update_views_if_changed(("LISTENING", "RU", 0.05))

    for view in (app.main, app.tray, app._overlay):
        assert view.update_snapshot.call_count == 2
        assert view.update_snapshot.call_args_list[1].args[0] == (
            "LISTENING",
            "RU",
            0.05,
        )


def test_idle_snapshot_does_not_construct_overlay() -> None:
    app = object.__new__(DesktopApplication)
    app._last_rendered_snapshot = None
    app._overlay = None
    app.main = MagicMock()
    app.tray = MagicMock()

    app._update_views_if_changed(("IDLE", "RU", 0.0))

    assert app._overlay is None
    app.main.update_snapshot.assert_called_once()
    app.tray.update_snapshot.assert_called_once()


def test_startup_hotkey_conflict_keeps_other_hotkeys_registered() -> None:
    app = object.__new__(DesktopApplication)
    app.runtime = SimpleNamespace(settings=AppSettings())
    app.controller = MagicMock()
    app.hotkeys = MagicMock()
    app.tray = MagicMock()

    def register(hotkey, _callback) -> None:  # type: ignore[no-untyped-def]
        if hotkey.identifier == 2:
            raise RuntimeError("combination is occupied")

    app.hotkeys.register.side_effect = register

    assert not app._configure_hotkeys(strict=False)
    assert [call.args[0].identifier for call in app.hotkeys.register.call_args_list] == [
        1,
        2,
        3,
    ]
    app.hotkeys.unregister_all.assert_called_once()
    app.tray.show_message.assert_called_once()


def test_settings_hotkey_conflict_rolls_back_partial_registration() -> None:
    app = object.__new__(DesktopApplication)
    app.runtime = SimpleNamespace(settings=AppSettings())
    app.controller = MagicMock()
    app.hotkeys = MagicMock()
    app.tray = MagicMock()
    app.hotkeys.register.side_effect = [None, RuntimeError("combination is occupied")]

    assert not app._configure_hotkeys()
    assert app.hotkeys.register.call_count == 2
    assert app.hotkeys.unregister_all.call_count == 2


def test_onboarding_autostart_choice_reaches_settings_backend() -> None:
    app = object.__new__(DesktopApplication)
    app.runtime = SimpleNamespace(settings=AppSettings())
    app._save_settings = MagicMock()

    app._complete_onboarding({"start_with_windows": True})

    changes = app._save_settings.call_args.args[0]
    assert changes["general"]["start_with_windows"] is True
    assert changes["general"]["onboarding_completed"] is True


def test_completed_session_retries_retained_text_without_blocking_ui(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = object.__new__(DesktopApplication)
    app.controller = MagicMock()
    app.controller.state.name = "COMPLETED"
    app.controller.injection_service.pending_fragments = 1
    app.controller.snapshot.return_value = SimpleNamespace(
        state="COMPLETED", error_message="", status_message=""
    )
    app.runtime = SimpleNamespace(
        settings=SimpleNamespace(
            general=SimpleNamespace(show_overlay=False, play_sounds=False),
            dictation=SimpleNamespace(auto_hide_completed_result=False),
        )
    )
    app.main = MagicMock()
    app.tray = MagicMock()
    app.signals = MagicMock()
    app._overlay = None
    app._last_rendered_snapshot = None
    app._last_state = "COMPLETED"
    app._last_pending_flush = 0.0
    app._last_resource_sample = time.monotonic()
    app._pending_flush_running = False

    class DeferredThread:
        def __init__(self, *, target, **_kwargs):  # type: ignore[no-untyped-def]
            self.target = target

        def start(self) -> None:
            return None

    launched: list[DeferredThread] = []

    def make_thread(*, target, **kwargs):  # type: ignore[no-untyped-def]
        thread = DeferredThread(target=target, **kwargs)
        launched.append(thread)
        return thread

    monkeypatch.setattr("voxtype.app.threading.Thread", make_thread)
    app._poll()

    assert len(launched) == 1
    assert app._pending_flush_running
    app.controller.retry_completed_insertion.assert_not_called()
    launched[0].target()
    app.controller.retry_completed_insertion.assert_called_once()
    app.signals.pending_flush_ready.emit.assert_called_once()


def test_secondary_windows_are_created_once_on_first_access(qtbot) -> None:  # type: ignore[no-untyped-def]
    app = object.__new__(DesktopApplication)
    app.main = QWidget()
    qtbot.addWidget(app.main)
    app.signals = _WorkerSignals()
    app._settings_window = None
    app._history_window = None
    app._diagnostics_window = None

    assert app.settings_window is app.settings_window
    assert app.history_window is app.history_window
    assert app.diagnostics_window is app.diagnostics_window
    assert app.settings_window.parent() is app.main
    assert app.history_window.parent() is app.main
    assert app.diagnostics_window.parent() is app.main
