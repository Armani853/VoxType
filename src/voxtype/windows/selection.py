"""Optional UI Automation access limited to the current selected text."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread
from time import monotonic
from typing import cast

from voxtype.windows._win32 import load_library
from voxtype.windows.foreground import WindowMetadata, get_foreground_hwnd, get_window_process_id
from voxtype.windows.input_context import ForegroundContextProvider


class SelectionUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SelectedText:
    target: WindowMetadata
    text: str
    control_id: tuple[int, ...]
    captured_at_monotonic: float
    native_range: tuple[int, int, int] | None


def _native_selection(hwnd: int) -> tuple[int, int, int] | None:
    send = load_library("user32").SendMessageTimeoutW
    send.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.POINTER(ctypes.c_size_t),
    )
    send.restype = ctypes.c_ssize_t
    result = ctypes.c_size_t()
    # EM_GETSEL encodes positions for classic edit controls without returning text.
    if not send(hwnd, 0x00B0, 0, 0, 0x0002, 100, ctypes.byref(result)):
        return None
    packed = result.value & 0xFFFFFFFF
    start, end = packed & 0xFFFF, packed >> 16
    return (hwnd, start, end) if packed != 0xFFFFFFFF and start < end else None


class SelectionProvider:
    def __init__(self, timeout_s: float = 0.6) -> None:
        self._timeout_s = timeout_s
        self._query = Lock()

    def read(self, target: WindowMetadata) -> SelectedText:
        if not self._query.acquire(blocking=False):
            raise SelectionUnavailable("Редактор не ответил. Вставьте выделение вручную.")
        result: Queue[SelectedText | Exception] = Queue(maxsize=1)

        def query() -> None:
            try:
                result.put_nowait(self._read(target))
            except Exception as exc:
                result.put_nowait(exc)
            finally:
                self._query.release()

        try:
            Thread(target=query, name="voxtype-selection-uia", daemon=True).start()
        except RuntimeError:
            self._query.release()
            raise
        try:
            selected = result.get(timeout=self._timeout_s)
        except Empty:
            raise SelectionUnavailable("Редактор не ответил. Вставьте выделение вручную.") from None
        if isinstance(selected, Exception):
            raise selected
        return selected

    @staticmethod
    def _read(target: WindowMetadata) -> SelectedText:
        native = ForegroundContextProvider.native(target)
        if (
            not native.same_session
            or native.is_password
            or native.is_read_only is True
            or target.process_id == os.getpid()
        ):
            raise SelectionUnavailable("Выделение из этого поля недоступно")
        try:
            import comtypes
            from comtypes.client import CreateObject, GetModule
        except ImportError as exc:
            raise SelectionUnavailable(
                "UI Automation недоступна. Вставьте выделение вручную."
            ) from exc
        try:
            cast(Callable[[], None], comtypes.CoInitialize)()
            try:
                types = GetModule(
                    str(Path(os.environ["SYSTEMROOT"]) / "System32/UIAutomationCore.dll")
                )
                automation = CreateObject(types.CUIAutomation, interface=types.IUIAutomation)
                control = automation.GetFocusedElement()
                if control.CurrentProcessId != target.process_id or control.CurrentIsPassword:
                    raise SelectionUnavailable("Выделение из этого поля недоступно")
                control_id = tuple(int(value) for value in control.GetRuntimeId())
                if not control_id:
                    raise SelectionUnavailable("Не удалось установить идентификатор поля")
                pattern = control.GetCurrentPattern(types.UIA_TextPatternId).QueryInterface(
                    types.IUIAutomationTextPattern
                )
                selection = pattern.GetSelection()
                if selection.Length != 1:
                    raise SelectionUnavailable("Нужно одно непрерывное выделение")
                text_range = selection.GetElement(0)
                read_only = text_range.GetAttributeValue(types.UIA_IsReadOnlyAttributeId)
                if read_only is not False and not (
                    native.is_read_only is False
                    and native.focused_hwnd == int(control.CurrentNativeWindowHandle or 0)
                    and read_only is not True
                ):
                    raise SelectionUnavailable("Не удалось подтвердить, что поле редактируемое")
                if (
                    get_foreground_hwnd() != target.hwnd
                    or get_window_process_id(target.hwnd) != target.process_id
                ):
                    raise SelectionUnavailable("Целевое окно сменилось")
                focus = native.focused_hwnd
                before = (
                    _native_selection(focus)
                    if focus
                    and native.native_text_control
                    and focus == int(control.CurrentNativeWindowHandle or 0)
                    else None
                )
                value = text_range.GetText(32_769)
                if not value:
                    raise SelectionUnavailable(
                        "Нет выделения. Начните диктовку или выделите текст."
                    )
                if len(value) > 32_768:
                    raise SelectionUnavailable("Выделено более 32 768 символов")
                focused = automation.GetFocusedElement()
                if (
                    focused.CurrentIsPassword
                    or tuple(focused.GetRuntimeId()) != control_id
                    or get_foreground_hwnd() != target.hwnd
                ):
                    raise SelectionUnavailable("Поле ввода сменилось")
                current = ForegroundContextProvider.native(target)
                native_range = (
                    _native_selection(current.focused_hwnd)
                    if current.native_text_control
                    and current.focused_hwnd
                    and current.focused_hwnd == native.focused_hwnd
                    and current.focused_hwnd == int(control.CurrentNativeWindowHandle or 0)
                    and not current.is_password
                    and current.is_read_only is False
                    else None
                )
                return SelectedText(
                    target,
                    value,
                    control_id,
                    monotonic(),
                    native_range if native_range == before else None,
                )
            finally:
                cast(Callable[[], None], comtypes.CoUninitialize)()
        except SelectionUnavailable:
            raise
        except (OSError, ValueError, AttributeError, KeyError, TypeError, ImportError) as exc:
            raise SelectionUnavailable(
                "Редактор не предоставил выделение через UI Automation"
            ) from exc
