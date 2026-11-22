"""Safety and compatibility decisions for the existing Windows insertion FIFO."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from voxtype.core.profiles import AppRule
from voxtype.windows.input_context import ForegroundContext


class InputTargetKind(StrEnum):
    STANDARD_EDIT = "standard_edit"
    RICH_TEXT = "rich_text"
    BROWSER_EDITABLE = "browser_editable"
    CODE_EDITOR = "code_editor"
    TERMINAL = "terminal"
    CHAT_INPUT = "chat_input"
    UNKNOWN = "unknown"
    PASSWORD = "password"
    READ_ONLY = "read_only"


class InputStrategy(StrEnum):
    CLIPBOARD_PASTE = "clipboard_paste"
    UNICODE_SENDINPUT = "unicode_input"
    LOCAL_PREVIEW = "local_preview"
    COPY_ONLY = "copy_only"


@dataclass(frozen=True, slots=True)
class InputPlan:
    strategy: InputStrategy
    target_identity: tuple[int, int] | None
    expected_format: str
    confirmation_required: bool
    clipboard_policy: str
    fallback_allowed: bool
    reason: str


class InputTargetClassifier:
    @staticmethod
    def is_possible_terminal(context: ForegroundContext, kind: InputTargetKind) -> bool:
        return kind is InputTargetKind.TERMINAL or context.process_name.casefold() in {
            "windowsterminal.exe",
            "powershell.exe",
            "pwsh.exe",
            "cmd.exe",
            "conhost.exe",
            "openconsole.exe",
        }

    @staticmethod
    def classify(context: ForegroundContext) -> InputTargetKind:
        if context.is_password:
            return InputTargetKind.PASSWORD
        if context.is_read_only is True:
            return InputTargetKind.READ_ONLY
        control = (context.control_type or "").casefold()
        exe = context.process_name.casefold()
        framework = (context.framework_id or "").casefold()
        automation_class = (context.uia_class_name or "").casefold()
        embedded_terminal = any(
            marker in automation_class.split()
            for marker in (
                "xterm-helper-textarea",
                "xterm-accessibility",
                "jediterm",
                "terminalcontrol",
            )
        )
        if (
            embedded_terminal
            or control in {"consolewindowclass", "cascadia_hosting_window_class", "terminalcontrol"}
            or (
                exe in {"windowsterminal.exe", "conhost.exe", "openconsole.exe"}
                and control in {"edit", "document"}
                and (context.supports_text_pattern or context.supports_text_pattern2)
            )
        ):
            return InputTargetKind.TERMINAL
        if context.is_read_only is not False:
            return InputTargetKind.UNKNOWN
        if control.startswith("richedit"):
            return InputTargetKind.RICH_TEXT
        if control not in {"edit", "document"}:
            return InputTargetKind.UNKNOWN
        if exe in {"chrome.exe", "msedge.exe", "firefox.exe"} and (
            context.supports_text_pattern or context.supports_value_pattern
        ):
            return InputTargetKind.BROWSER_EDITABLE
        if (
            exe in {"code.exe", "code - insiders.exe", "pycharm64.exe", "idea64.exe"}
            or framework in {"swing", "java"}
        ) and context.supports_text_pattern:
            return InputTargetKind.CODE_EDITOR
        if exe in {"telegram.exe", "slack.exe", "discord.exe", "ms-teams.exe"} and (
            context.supports_value_pattern or context.supports_text_pattern
        ):
            return InputTargetKind.CHAT_INPUT
        return InputTargetKind.RICH_TEXT if control == "document" else InputTargetKind.STANDARD_EDIT


class FocusIdentityGuard:
    @staticmethod
    def matches(
        anchor: ForegroundContext | None, current: ForegroundContext, *, strict: bool
    ) -> bool:
        if anchor is None or anchor.identity != current.identity or not current.same_session:
            return False
        if anchor.focused_hwnd is not None and anchor.focused_hwnd != current.focused_hwnd:
            return False
        if not strict:
            return True
        if anchor.runtime_id and current.runtime_id:
            return anchor.runtime_id == current.runtime_id
        return bool(
            anchor.focused_hwnd and anchor.native_text_control and current.native_text_control
        )


class InputSafetyPolicy:
    @staticmethod
    def refusal(
        context: ForegroundContext,
        kind: InputTargetKind,
        anchor: ForegroundContext | None,
        *,
        strict: bool,
        manual: bool,
    ) -> str:
        if kind is InputTargetKind.PASSWORD:
            return "Парольное поле. Текст доступен в VoxType."
        if kind is InputTargetKind.READ_ONLY:
            return "Активное поле доступно только для чтения."
        if not context.same_session or not (context.focused_hwnd or context.runtime_id):
            return "Windows не подтвердила активное поле ввода."
        if not context.password_checked:
            return "Защиту активного поля не удалось проверить. Скопируйте текст вручную."
        if context.target_mode == "locked" and (
            anchor is None or anchor.identity != context.identity
        ):
            return "Целевое окно изменилось. Выберите цель заново."
        if strict and not FocusIdentityGuard.matches(anchor, context, strict=True):
            return "Поле ввода изменилось. Выберите цель заново."
        if kind is InputTargetKind.UNKNOWN and not manual:
            return "Тип поля не подтверждён. Выберите окно и повторите вручную."
        return ""


class InputStrategyResolver:
    @staticmethod
    def resolve(
        context: ForegroundContext,
        text: str,
        profile: AppRule | None,
        *,
        anchor: ForegroundContext | None,
        preferred: str = "auto",
        manual: bool = False,
        terminal_safe: bool = True,
        multiline_confirmed: bool = False,
    ) -> InputPlan:
        kind = InputTargetClassifier.classify(context)
        strict = kind is InputTargetKind.TERMINAL or (
            profile.strict_focus
            if profile is not None
            else context.target_mode == "locked"
            or kind in {InputTargetKind.BROWSER_EDITABLE, InputTargetKind.CODE_EDITOR}
        )
        exact = kind in {InputTargetKind.CODE_EDITOR, InputTargetKind.TERMINAL}
        if profile and profile.formatting_policy == "preserve":
            exact = True
        policy = profile.clipboard_restore_policy if profile else "preserve"
        reason = InputSafetyPolicy.refusal(context, kind, anchor, strict=strict, manual=manual)
        if not reason and profile and not profile.allow_auto_insert and not manual:
            reason = "Для этого приложения включена ручная вставка."
        multiline = any(chr(code) in text for code in (10, 13, 8232, 8233))
        possible_terminal = InputTargetClassifier.is_possible_terminal(context, kind)
        ambiguous_editor = kind is InputTargetKind.CODE_EDITOR and not context.native_text_control
        needs_clipboard_for_multiline = (
            possible_terminal or ambiguous_editor or kind is InputTargetKind.UNKNOWN
        )
        confirm = multiline and (
            needs_clipboard_for_multiline
            or bool(profile and profile.multiline_requires_confirmation)
        )
        if any(ch == chr(0) for ch in text) or (
            (possible_terminal or kind is InputTargetKind.UNKNOWN)
            and any((ord(ch) < 32 and ord(ch) not in (10, 13)) or ord(ch) == 127 for ch in text)
        ):
            reason = "В тексте есть управляющие символы. Скопируйте текст вручную."
        if not reason and confirm and not (manual and multiline_confirmed):
            reason = "Для многострочной вставки требуется отдельное подтверждение."
        protected = kind in {InputTargetKind.PASSWORD, InputTargetKind.READ_ONLY}
        requested = (
            profile.input_strategy if profile and profile.input_strategy != "auto" else preferred
        )
        if reason or requested in {"safe_clipboard", "local_preview"}:
            strategy = (
                InputStrategy.COPY_ONLY
                if protected or requested == "safe_clipboard"
                else InputStrategy.LOCAL_PREVIEW
            )
            return InputPlan(
                strategy,
                context.identity,
                "exact" if exact else "text",
                confirm and not protected,
                policy,
                False,
                reason or "Включена ручная доставка.",
            )
        unicode = (
            requested == "unicode_input"
            or policy == "avoid_clipboard"
            or (
                profile is not None
                and profile.unicode_preference
                and requested != "clipboard_paste"
            )
            or (kind is InputTargetKind.TERMINAL and terminal_safe and not multiline)
        )
        if needs_clipboard_for_multiline and multiline and policy == "avoid_clipboard":
            return InputPlan(
                InputStrategy.LOCAL_PREVIEW,
                context.identity,
                "exact",
                False,
                policy,
                False,
                "Многострочный текст можно перенести вручную через буфер обмена.",
            )
        if needs_clipboard_for_multiline and multiline:
            unicode = False
        return InputPlan(
            InputStrategy.UNICODE_SENDINPUT if unicode else InputStrategy.CLIPBOARD_PASTE,
            context.identity,
            "exact" if exact else "text",
            False,
            policy,
            requested == "auto"
            and not (possible_terminal and multiline)
            and kind in {InputTargetKind.STANDARD_EDIT, InputTargetKind.RICH_TEXT},
            "",
        )
