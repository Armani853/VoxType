"""Optional UI Automation access limited to the current selected text."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread
from time import monotonic

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
            comtypes.CoInitialize()
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
                return SelectedText(target, value, control_id, monotonic())
            finally:
                comtypes.CoUninitialize()
        except SelectionUnavailable:
            raise
        except (OSError, ValueError, AttributeError, KeyError, TypeError, ImportError) as exc:
            raise SelectionUnavailable(
                "Редактор не предоставил выделение через UI Automation"
            ) from exc
