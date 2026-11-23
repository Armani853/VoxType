"""Unrelated settings must not rebuild GUI styles or Windows hooks."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QWidget

from voxtype.app import DesktopApplication, _WorkerSignals
from voxtype.core.compute_budget import ResourcePhase
from voxtype.core.models import AppPhase
from voxtype.settings.schema import AppSettings
from voxtype.windows.hotkeys import GlobalHotkeyManager, parse_hotkey


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
        can_change_runtime_settings=lambda: True,
        policy=SimpleNamespace(disable_history=False, disable_audio_storage=False),
        set_language=MagicMock(),
        resource_phase=ResourcePhase.READY,
        target_manager=MagicMock(),
        injection_service=MagicMock(),
    )
    app = object.__new__(DesktopApplication)
    app.runtime = runtime
    app.controller = controller
    app.main = MagicMock()
    app.application = object()
    app._closing = False
    app._settings_write_running = False
    app._update_idle_model_timer = MagicMock()
    app.signals = MagicMock()
    callbacks = []
    app.signals.settings_saved.emit.side_effect = callbacks.append
    app._configure_capture_factory = MagicMock()
    app._configure_segmenter_factory = MagicMock()
    app._configure_hotkeys = MagicMock(return_value=True)
    theme = MagicMock()
    monkeypatch.setattr("voxtype.app.apply_theme", theme)

    app._save_settings({"general": {"minimize_to_tray": updated.general.minimize_to_tray}})
    app._settings_writer.join(timeout=2)
    assert app.runtime.settings is previous
    assert len(callbacks) == 1
    app._finish_settings_write(callbacks[0])

    assert app.runtime.settings is updated
    app.main.set_minimize_to_tray.assert_called_once_with(updated.general.minimize_to_tray)
    app.main.set_interface_language.assert_not_called()
    app.main.set_toggle_hotkey.assert_not_called()
    app._configure_capture_factory.assert_not_called()
    app._configure_segmenter_factory.assert_not_called()
    app._configure_hotkeys.assert_not_called()
    assert theme.call_count == int(change_theme)


def _view_app() -> DesktopApplication:
    app = object.__new__(DesktopApplication)
    app.runtime = SimpleNamespace(settings=AppSettings())
    app._last_rendered_snapshot = None
    app._header_profile_id = None
    app._settings_window = None
    app._practice = None
    app.controller = MagicMock()
    app.controller.profiles.default_id = "general"
    app.controller._session_profile = SimpleNamespace(id="general", name="General")
    app.main = MagicMock()
    app.tray = MagicMock()
    app._overlay = None
    app._overlay_requested = False
    app._overlay_dismissed = False
    return app


def test_identical_snapshots_do_not_rewrite_all_views() -> None:
    app = _view_app()
    app._overlay = MagicMock()

    idle = SimpleNamespace(state="IDLE")
    active = SimpleNamespace(state="LISTENING", capture_open=True, session_elapsed_s=0.05)
    app._update_views_if_changed(idle)
    app._update_views_if_changed(idle)
    app._update_views_if_changed(active)

    expected = app.main.update_snapshot.call_args.args[1]
    assert expected.capture.value == "recording"
    for view in (app.main, app.tray, app._overlay):
        assert view.update_snapshot.call_count == 2
        assert view.update_snapshot.call_args.args == (active, expected)
    app.main.set_profile_name.assert_called_once()


def test_idle_snapshot_does_not_construct_overlay() -> None:
    app = _view_app()

    app._update_views_if_changed(("IDLE", "RU", 0.0))

    assert app._overlay is None
    app.main.update_snapshot.assert_called_once()
    app.tray.update_snapshot.assert_called_once()


def _hotkey_app() -> DesktopApplication:
    app = object.__new__(DesktopApplication)
    app.runtime = SimpleNamespace(settings=AppSettings())
    app.controller = MagicMock()
    app.signals = MagicMock()
    app._open_palette_shortcut = MagicMock()
    app._notify_desktop = MagicMock()
    active: set[int] = set()
    conflict = parse_hotkey(
        app.runtime.settings.hotkeys.language, identifier=2, name="toggle_language"
    )

    def register(_hwnd: int, identifier: int, modifiers: int, key: int) -> tuple[bool, int]:
        if (modifiers, key) == (conflict.native_modifiers, conflict.virtual_key):
            return False, 1409
        active.add(identifier)
        return True, 0

    def unregister(_hwnd: int, identifier: int) -> bool:
        active.discard(identifier)
        return True

    backend = SimpleNamespace(register=register, unregister=unregister)
    app.hotkeys = GlobalHotkeyManager(backend=backend)
    return app


def test_startup_hotkey_conflict_keeps_other_hotkeys_registered() -> None:
    app = _hotkey_app()

    app._configure_hotkeys(strict=False)

    assert {hotkey.name for hotkey in app.hotkeys.registered} == {
        "toggle_dictation",
        "toggle_pause",
    }
    app._notify_desktop.assert_called_once()


def test_settings_hotkey_conflict_rolls_back_partial_registration() -> None:
    app = _hotkey_app()

    assert not app._configure_hotkeys()
    assert app.hotkeys.registered == ()


def test_onboarding_autostart_choice_reaches_settings_backend() -> None:
    app = object.__new__(DesktopApplication)
    app.runtime = SimpleNamespace(settings=AppSettings())
    app._save_settings = MagicMock()

    app._complete_onboarding({"start_with_windows": True})

    changes = app._save_settings.call_args.args[0]
    assert changes["general"]["start_with_windows"] is True
    assert changes["general"]["onboarding_completed"] is True


def test_poll_does_not_retry_completed_text_without_user_action(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = _view_app()
    app._closing = app._closed = False
    app._foreground_events = SimpleNamespace(active=True)
    app._deferred_settings = {}
    app._system_locked = app._system_sleeping = False
    app._last_state = "COMPLETED"
    app._last_pending_flush = 0.0
    app._pending_flush_running = False
    app._result_dismissed = False
    app.runtime = SimpleNamespace(settings=AppSettings())
    app.runtime.settings.general.show_overlay = False
    app.timer = MagicMock()
    app.signals = MagicMock()
    app._update_idle_model_timer = MagicMock()
    app.controller.resource_phase = ResourcePhase.READY
    app.controller.injection_service.pending_fragments = 1
    app.controller.snapshot.return_value = SimpleNamespace(
        state="COMPLETED",
        delivery_status="queued_target_inactive",
        readiness=SimpleNamespace(phase=AppPhase.READY),
    )
    worker = MagicMock()
    monkeypatch.setattr("voxtype.app.threading.Thread", worker)

    app._poll()

    worker.assert_not_called()
    app.controller.retry_completed_insertion.assert_not_called()


def test_delayed_manual_insertion_is_discarded_after_system_lock(monkeypatch) -> None:
    app = _view_app()
    app._closing = app._system_locked = app._system_sleeping = False
    app._dashboard_delivery_pending = False
    app._manual_delivery_epoch = 0
    app._notify_desktop = MagicMock()
    timer = MagicMock()
    monkeypatch.setattr("voxtype.app.QTimer.singleShot", timer)
    send = MagicMock()

    app._schedule_manual_delivery(send)
    delay, owner, deliver = timer.call_args.args
    assert delay == 3000
    assert owner is app.main
    assert app._dashboard_delivery_pending

    app._manual_delivery_epoch += 1
    deliver()
    send.assert_not_called()
    assert not app._dashboard_delivery_pending


def test_secondary_windows_are_created_once_on_first_access(qtbot, tmp_path) -> None:  # type: ignore[no-untyped-def]
    app = object.__new__(DesktopApplication)
    app.main = QWidget()
    qtbot.addWidget(app.main)
    app.signals = _WorkerSignals()
    app._view_preferences = QSettings(str(tmp_path / "views.ini"), QSettings.Format.IniFormat)
    app.controller = MagicMock()
    app._settings_window = None
    app._history_window = None
    app._diagnostics_window = None

    assert app.settings_window is app.settings_window
    assert app.history_window is app.history_window
    assert app.diagnostics_window is app.diagnostics_window
    assert app.settings_window.parent() is app.main
    assert app.history_window.parent() is app.main
    assert app.diagnostics_window.parent() is app.main
