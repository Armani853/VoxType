"""Optional UI Automation access limited to the current selected text."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from voxtype.windows.foreground import WindowMetadata, get_foreground_hwnd, get_window_process_id


class SelectionUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SelectedText:
    target: WindowMetadata
    text: str
    control_id: tuple[int, ...]


class SelectionProvider:
    @staticmethod
    def read(target: WindowMetadata) -> SelectedText:
        if (
            get_foreground_hwnd() != target.hwnd
            or get_window_process_id(target.hwnd) != target.process_id
        ):
            raise SelectionUnavailable("Вернитесь в исходное окно и выделите текст")
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
                pattern = control.GetCurrentPattern(types.UIA_TextPatternId).QueryInterface(
                    types.IUIAutomationTextPattern
                )
                selection = pattern.GetSelection()
                if selection.Length != 1:
                    raise SelectionUnavailable("Нужно одно непрерывное выделение")
                value = selection.GetElement(0).GetText(32_769)
                if not value or len(value) > 32_768:
                    raise SelectionUnavailable("Нет выделения или выделено более 32 768 символов")
                if get_foreground_hwnd() != target.hwnd:
                    raise SelectionUnavailable("Целевое окно сменилось")
                control_id = tuple(control.GetRuntimeId())
                if not control_id:
                    raise SelectionUnavailable("Не удалось установить идентификатор поля")
                return SelectedText(target, value, control_id)
            finally:
                comtypes.CoUninitialize()
        except SelectionUnavailable:
            raise
        except (OSError, ValueError, AttributeError, KeyError, TypeError, ImportError) as exc:
            raise SelectionUnavailable(
                "Редактор не предоставил выделение через UI Automation"
            ) from exc
