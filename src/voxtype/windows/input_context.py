"""Technical, bounded snapshots of the Windows foreground input target."""

from __future__ import annotations

import contextlib
import ctypes
import os
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass, replace
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread
from time import monotonic
from typing import Any, cast

from voxtype.windows.foreground import (
    _GUI_THREAD_INFO,
    WindowMetadata,
    _configure_apis,
    capture_foreground,
    get_foreground_hwnd,
    get_window_process_id,
)


@dataclass(frozen=True, slots=True)
class ForegroundContext:
    window_hwnd: int
    focused_hwnd: int | None
    process_id: int
    process_name: str
    executable_path: str | None
    control_type: str | None = None
    framework_id: str | None = None
    supports_text_pattern: bool = False
    supports_text_pattern2: bool = False
    supports_value_pattern: bool = False
    is_password: bool = False
    is_read_only: bool | None = None
    captured_at_monotonic: float = 0.0
    runtime_id: tuple[int, ...] = ()
    same_session: bool = False
    caret_screen: tuple[int, int, int, int] | None = None
    target_mode: str = "locked"
    password_checked: bool = False
    native_text_control: bool = False
    uia_class_name: str | None = None

    @property
    def identity(self) -> tuple[int, int]:
        return self.window_hwnd, self.process_id


class ForegroundContextProvider:
    """One outstanding UIA query at most. A stalled provider never starts a second worker."""

    def __init__(self, timeout_s: float = 0.15) -> None:
        self.timeout_s = timeout_s
        self._query = Lock()

    @staticmethod
    def native(target: WindowMetadata, mode: str = "locked") -> ForegroundContext:
        context = ForegroundContext(
            target.hwnd,
            None,
            target.process_id,
            target.process_name,
            target.process_path,
            captured_at_monotonic=monotonic(),
            target_mode=mode,
        )
        if os.name != "nt" or get_foreground_hwnd() != target.hwnd:
            return context
        user32, kernel32, _ = _configure_apis()
        pid = wintypes.DWORD()
        thread = user32.GetWindowThreadProcessId(target.hwnd, ctypes.byref(pid))
        if not thread or pid.value != target.process_id:
            return context
        session_id = kernel32.ProcessIdToSessionId
        session_id.argtypes = (wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
        session_id.restype = wintypes.BOOL
        ours, theirs = wintypes.DWORD(), wintypes.DWORD()
        same_session = bool(
            session_id(os.getpid(), ctypes.byref(ours))
            and session_id(pid.value, ctypes.byref(theirs))
            and ours.value == theirs.value
        )
        context = replace(context, same_session=same_session)
        info = _GUI_THREAD_INFO()
        info.size = ctypes.sizeof(info)
        if not user32.GetGUIThreadInfo(thread, ctypes.byref(info)) or not info.focus:
            return context
        focus = int(info.focus)
        if get_window_process_id(focus) != pid.value:
            return context
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(info.focus, name, len(name))
        kind = name.value or None
        standard_edit = bool(
            kind and (kind.casefold() == "edit" or kind.casefold().startswith("richedit"))
        )
        style = 0
        if standard_edit:
            style_for = user32.GetWindowLongW
            style_for.argtypes = (wintypes.HWND, ctypes.c_int)
            style_for.restype = ctypes.c_long
            style = style_for(info.focus, -16)
        caret = None
        if info.caret and get_window_process_id(int(info.caret)) == pid.value:
            client_to_screen = user32.ClientToScreen
            client_to_screen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
            client_to_screen.restype = wintypes.BOOL
            rect = info.caret_rect
            origin, end = (
                wintypes.POINT(rect.left, rect.top),
                wintypes.POINT(rect.right, rect.bottom),
            )
            if client_to_screen(info.caret, ctypes.byref(origin)) and client_to_screen(
                info.caret, ctypes.byref(end)
            ):
                caret = origin.x, origin.y, end.x, end.y
        if get_foreground_hwnd() != target.hwnd:
            return context
        return replace(
            context,
            focused_hwnd=focus,
            control_type=kind,
            is_password=bool(style & 0x20),
            is_read_only=bool(style & 0x800) if standard_edit else None,
            caret_screen=caret,
            password_checked=standard_edit,
            native_text_control=standard_edit,
        )

    def capture(
        self, target: WindowMetadata | None = None, *, mode: str = "locked"
    ) -> ForegroundContext | None:
        target = target or capture_foreground()
        if target is None:
            return None
        native = self.native(target, mode)
        if (
            not native.same_session
            or native.is_password
            or native.is_read_only is True
            or not self._query.acquire(blocking=False)
        ):
            return native
        result: Queue[ForegroundContext] = Queue(maxsize=1)

        def discover() -> None:
            try:
                enriched = self._uia(native)
            except Exception:
                enriched = native
            finally:
                self._query.release()
            result.put_nowait(enriched)

        worker = Thread(target=discover, name="voxtype-focused-uia", daemon=True)
        try:
            worker.start()
        except RuntimeError:
            self._query.release()
            return native
        try:
            return result.get(timeout=self.timeout_s)
        except Empty:
            return native

    @staticmethod
    def _uia(native: ForegroundContext) -> ForegroundContext:
        import comtypes
        from comtypes.client import CreateObject, GetModule

        cast(Callable[[], None], comtypes.CoInitialize)()
        try:
            types = GetModule(str(Path(os.environ["SYSTEMROOT"]) / "System32/UIAutomationCore.dll"))
            automation = CreateObject(types.CUIAutomation, interface=types.IUIAutomation)
            element = automation.GetFocusedElement()
            if (
                element.CurrentProcessId != native.process_id
                or get_foreground_hwnd() != native.window_hwnd
            ):
                return native
            if element.CurrentIsPassword:
                return replace(native, is_password=True, password_checked=True)

            def pattern(identifier: int) -> Any:
                try:
                    return element.GetCurrentPattern(identifier)
                except OSError:
                    return None

            text = pattern(10014)
            text2 = pattern(10024)
            value = pattern(10002)
            password = native.is_password
            read_only = native.is_read_only
            if value and not password:
                with contextlib.suppress(AttributeError, OSError):
                    read_only = bool(
                        value.QueryInterface(types.IUIAutomationValuePattern).CurrentIsReadOnly
                    )
            if text and read_only is None and not password:
                try:
                    flag = text.QueryInterface(
                        types.IUIAutomationTextPattern
                    ).DocumentRange.GetAttributeValue(40015)
                    if type(flag) is bool:
                        read_only = flag
                except (AttributeError, OSError):
                    pass
            if read_only is True:
                return replace(native, is_read_only=True, password_checked=True)
            latest_hwnd = int(element.CurrentNativeWindowHandle or 0)
            if native.focused_hwnd and latest_hwnd and native.focused_hwnd != latest_hwnd:
                return native
            runtime = tuple(int(value) for value in element.GetRuntimeId())
            if get_foreground_hwnd() != native.window_hwnd:
                return native
            return replace(
                native,
                control_type={50004: "edit", 50020: "text", 50030: "document"}.get(
                    element.CurrentControlType, native.control_type
                ),
                framework_id=str(element.CurrentFrameworkId or "")[:128] or None,
                uia_class_name=str(element.CurrentClassName or "")[:128] or None,
                supports_text_pattern=bool(text),
                supports_text_pattern2=bool(text2),
                supports_value_pattern=bool(value),
                is_password=password,
                is_read_only=read_only,
                runtime_id=runtime,
                password_checked=True,
            )
        finally:
            cast(Callable[[], None], comtypes.CoUninitialize)()
