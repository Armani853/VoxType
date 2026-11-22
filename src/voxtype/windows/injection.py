"""Ordered, target-safe text delivery with clipboard and Unicode fallbacks."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from voxtype.core.profiles import AppRule

from .adaptive_input import FocusIdentityGuard, InputPlan, InputStrategy, InputStrategyResolver
from .clipboard import ClipboardError, ClipboardManager
from .foreground import WindowMetadata
from .input_context import ForegroundContext, ForegroundContextProvider
from .send_input import ModifierStillPressedError, SendInputController, SendInputError
from .target_window import TargetDecision, TargetStatus, TargetWindowManager


class InjectionStrategy(StrEnum):
    AUTO = "auto"
    CLIPBOARD = "clipboard_paste"
    CLIPBOARD_PASTE = "clipboard_paste"
    UNICODE = "unicode_input"
    UNICODE_INPUT = "unicode_input"
    SAFE_COPY = "safe_clipboard"
    SAFE_CLIPBOARD = "safe_clipboard"


class InjectionStatus(StrEnum):
    INSERTED = "inserted"
    COPIED = "copied"
    QUEUED = "queued"
    BLOCKED = "blocked"
    FAILED = "failed"
    EMPTY = "empty"
    DUPLICATE = "duplicate"
    UNKNOWN = "unknown"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    QUEUED_TARGET_INACTIVE = "queued_target_inactive"
    UNKNOWN = "unknown"


class InsertionAttemptStatus(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    DISPATCHED = "dispatched"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    TARGET_LOST = "target_lost"


@dataclass(frozen=True, slots=True)
class InjectionResult:
    status: InjectionStatus
    submitted_text: str
    delivered_text: str
    strategy: InjectionStrategy | None
    pending_fragments: int
    target: WindowMetadata | None
    target_status: TargetStatus | None
    clipboard_restored: bool | None = None
    detail: str = ""
    attempt_id: str = field(default_factory=lambda: uuid4().hex, compare=False)

    @property
    def attempt_status(self) -> InsertionAttemptStatus:
        if self.status in {InjectionStatus.INSERTED, InjectionStatus.UNKNOWN}:
            return InsertionAttemptStatus.DISPATCHED
        if (
            self.status in {InjectionStatus.QUEUED, InjectionStatus.BLOCKED}
            and self.target_status is not TargetStatus.READY
        ):
            return InsertionAttemptStatus.TARGET_LOST
        if self.status is InjectionStatus.FAILED:
            return InsertionAttemptStatus.FAILED
        return InsertionAttemptStatus.NOT_ATTEMPTED

    @property
    def retry_available(self) -> bool:
        return self.pending_fragments > 0 and self.attempt_status in {
            InsertionAttemptStatus.FAILED,
            InsertionAttemptStatus.TARGET_LOST,
        }

    @property
    def error_code(self) -> str:
        if self.attempt_status in {
            InsertionAttemptStatus.FAILED,
            InsertionAttemptStatus.TARGET_LOST,
        }:
            return (
                self.target_status.value
                if self.target_status is not None and self.target_status is not TargetStatus.READY
                else "insertion_failed"
            )
        return "delivery_uncertain" if self.status is InjectionStatus.UNKNOWN else ""

    @property
    def accepted(self) -> bool:
        """True means text was delivered or remains retained in the queue."""

        return self.status is not InjectionStatus.EMPTY

    @property
    def delivered(self) -> bool:
        return self.status in {InjectionStatus.INSERTED, InjectionStatus.COPIED}

    @property
    def requires_safe_copy(self) -> bool:
        return self.status is InjectionStatus.BLOCKED


class TargetResolver(Protocol):
    def resolve(self, *, foreground: WindowMetadata | None = None) -> TargetDecision: ...


@dataclass(frozen=True, slots=True)
class PendingFragment:
    text: str
    target_identity: tuple[int, int] | None
    fragment_id: str = ""
    exact_text: bool = False
    strategy: str = ""


class PendingTextQueue:
    """FIFO that automatically spills sensitive pending text out of RAM.

    The OS removes the anonymous temporary file when it is closed. Durable crash
    recovery remains the responsibility of SessionJournal.
    """

    def __init__(self, *, memory_limit_bytes: int = 256 * 1024, allow_disk: bool = True) -> None:
        if memory_limit_bytes < 1:
            raise ValueError("memory_limit_bytes must be positive")
        self._memory_limit_bytes = memory_limit_bytes
        self._allow_disk = allow_disk
        self._stream = self._new_stream()
        self._read_offset = 0
        self._count = 0
        self._characters = 0
        self._lock = threading.RLock()

    def __len__(self) -> int:
        with self._lock:
            return self._count

    @property
    def characters(self) -> int:
        with self._lock:
            return self._characters

    def append(
        self,
        text: str,
        *,
        target_identity: tuple[int, int] | None = None,
        fragment_id: str = "",
        exact_text: bool = False,
        strategy: str = "",
    ) -> None:
        if not text:
            return
        payload = {
            "target": None if target_identity is None else list(target_identity),
            "text": text,
            "fragment_id": fragment_id,
            "exact_text": exact_text,
            "strategy": strategy,
        }
        record = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        with self._lock:
            self._stream.seek(0, 2)
            if (
                not self._allow_disk
                and self._stream.tell() + len(record) > self._memory_limit_bytes
            ):
                self._compact_memory()
                if self._stream.tell() + len(record) > self._memory_limit_bytes:
                    raise BufferError("Pending text reached the memory limit")
            self._stream.write(record)
            self._stream.flush()
            self._count += 1
            self._characters += len(text)

    @staticmethod
    def _decode_record(line: bytes) -> PendingFragment:
        value = json.loads(line.decode("utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("text"), str):
            raise RuntimeError("pending-text spool contains an invalid record")
        raw_target = value.get("target")
        if raw_target is None:
            target = None
        elif (
            isinstance(raw_target, list)
            and len(raw_target) == 2
            and all(isinstance(part, int) for part in raw_target)
        ):
            target = (raw_target[0], raw_target[1])
        else:
            raise RuntimeError("pending-text spool contains an invalid target identity")
        return PendingFragment(
            value["text"],
            target,
            str(value.get("fragment_id", "")),
            bool(value.get("exact_text")),
            str(value.get("strategy", "")),
        )

    def peek_record(self) -> PendingFragment | None:
        with self._lock:
            if not self._count:
                return None
            self._stream.seek(self._read_offset)
            line = self._stream.readline()
            if not line:
                raise RuntimeError("pending-text spool ended before its index")
            return self._decode_record(line)

    def peek(self) -> str | None:
        record = self.peek_record()
        return None if record is None else record.text

    def popleft(self) -> str:
        return self.popleft_record().text

    def popleft_record(self) -> PendingFragment:
        with self._lock:
            record = self.peek_record()
            if record is None:
                raise IndexError("pending-text queue is empty")
            self._stream.seek(self._read_offset)
            line = self._stream.readline()
            self._read_offset = self._stream.tell()
            self._count -= 1
            self._characters -= len(record.text)
            if not self._count:
                self._reset_stream()
            elif not line:
                raise RuntimeError("pending-text spool could not advance")
            return record

    def __iter__(self) -> Iterator[str]:
        return (record.text for record in self.records())

    def records(self) -> tuple[PendingFragment, ...]:
        with self._lock:
            offset = self._read_offset
            remaining = self._count
            values: list[PendingFragment] = []
            self._stream.seek(offset)
            for _ in range(remaining):
                line = self._stream.readline()
                if not line:
                    raise RuntimeError("pending-text spool ended before its index")
                values.append(self._decode_record(line))
        return tuple(values)

    def _reset_stream(self) -> None:
        self._stream.close()
        self._stream = self._new_stream()
        self._read_offset = 0

    def _new_stream(self):
        if not self._allow_disk:
            return io.BytesIO()
        return tempfile.SpooledTemporaryFile(max_size=self._memory_limit_bytes, mode="w+b")

    def _compact_memory(self) -> None:
        if self._read_offset:
            self._stream.seek(self._read_offset)
            remaining = self._stream.read()
            self._reset_stream()
            self._stream.write(remaining)

    def close(self) -> None:
        with self._lock:
            self._stream.close()
            self._count = 0
            self._characters = 0
            self._read_offset = 0


_NO_SPACE_BEFORE = frozenset(".,!?;:%)]}»…")
_NO_SPACE_AFTER = frozenset("([{«")
_ASYNC_CLIPBOARD_READERS = frozenset({"browser.exe", "chrome.exe", "msedge.exe", "code.exe"})
_ASYNC_PASTE_MIN_DELAY_SECONDS = 0.35


def prepare_fragment(previous_tail: str, fragment: str) -> str:
    """Add only the boundary whitespace needed between committed fragments."""

    if not fragment:
        return ""
    if not previous_tail:
        return fragment.lstrip(" \t")

    candidate = fragment
    if previous_tail[-1].isspace():
        return candidate.lstrip(" \t")
    stripped = candidate.lstrip(" \t")
    if not stripped:
        return ""
    if stripped[0] in _NO_SPACE_BEFORE or previous_tail[-1] in _NO_SPACE_AFTER:
        return stripped
    if stripped.startswith(("\r", "\n")):
        return stripped
    if candidate[0].isspace():
        return " " + stripped
    return " " + stripped


def join_fragments(fragments: Iterator[str] | list[str] | tuple[str, ...]) -> str:
    result = ""
    tail = ""
    for fragment in fragments:
        prepared = prepare_fragment(tail, fragment)
        result += prepared
        tail = (tail + prepared)[-128:]
    return result


class _TargetChanged(RuntimeError):
    """The verified target changed before the synthetic input was emitted."""


@dataclass(frozen=True, slots=True)
class ClipboardTransaction:
    attempt_id: str
    clipboard_sequence_before: int
    clipboard_sequence_after_write: int
    target: tuple[int, int]
    written_text_hash: str
    restore_policy: str


class TextInjectionService:
    """Retain committed fragments until a verified target accepts them."""

    def __init__(
        self,
        target_manager: TargetWindowManager | TargetResolver,
        *,
        clipboard: ClipboardManager | None = None,
        sender: SendInputController | None = None,
        strategy: InjectionStrategy | str = InjectionStrategy.AUTO,
        paste_delay: float = 0.35,
        pending_memory_bytes: int = 256 * 1024,
        persist_pending: bool = True,
        context_provider: ForegroundContextProvider | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if paste_delay < 0:
            raise ValueError("paste_delay cannot be negative")
        self._targets = target_manager
        self.context_provider = context_provider
        self._focus_anchor: ForegroundContext | None = None
        self._compatibility_rule: AppRule | None = None
        self._terminal_safe = True
        self._clipboard = clipboard or ClipboardManager()
        self._sender = sender or SendInputController()
        self._strategy = InjectionStrategy(strategy)
        self._paste_delay = paste_delay
        self._sleep = sleep
        self._pending_memory_bytes = pending_memory_bytes
        self._persist_pending = persist_pending
        self._pending = self._make_pending(persist_pending)
        self._tails: dict[tuple[int, int], str] = {}
        self._known_fragment_ids: set[str] = set()
        self._delivered_fragment_ids: set[str] = set()
        self._delivery_statuses: dict[str, DeliveryStatus] = {}
        self._awaiting_explicit_retry = False
        self._lock = threading.RLock()

    def _make_pending(self, persist: bool) -> PendingTextQueue:
        limit = (
            self._pending_memory_bytes
            if persist
            else max(4 * 1024 * 1024, self._pending_memory_bytes)
        )
        return PendingTextQueue(memory_limit_bytes=limit, allow_disk=persist)

    def begin_adaptive_input(
        self,
        context: ForegroundContext | None,
        rule: AppRule | None,
        *,
        terminal_safe: bool,
    ) -> None:
        with self._lock:
            if not len(self._pending):
                self._focus_anchor = context
                self._compatibility_rule = rule
                self._terminal_safe = terminal_safe

    def _adaptive_plan(
        self,
        target: WindowMetadata,
        text: str,
        strategy: InjectionStrategy,
        *,
        manual: bool,
        multiline_confirmed: bool = False,
        fresh: bool = False,
    ) -> tuple[InputPlan | None, ForegroundContext | None]:
        provider = self.context_provider
        if provider is None:
            return None, None
        anchor = self._focus_anchor
        current = provider.capture(target, mode=anchor.target_mode if anchor else "locked")
        if current is None:
            return InputPlan(
                InputStrategy.LOCAL_PREVIEW,
                target.identity,
                "text",
                False,
                "preserve",
                False,
                "Активное поле не подтверждено. Выберите цель или скопируйте текст.",
            ), None
        rule = (
            self._compatibility_rule
            if not fresh and anchor and anchor.identity == current.identity
            else None
        )
        return InputStrategyResolver.resolve(
            current,
            text,
            rule,
            anchor=current if fresh else anchor,
            preferred=strategy.value,
            manual=manual,
            terminal_safe=self._terminal_safe,
            multiline_confirmed=multiline_confirmed,
        ), current

    def configure_pending_storage(self, persist: bool) -> bool:
        with self._lock:
            if persist == self._persist_pending:
                return True
            if len(self._pending):
                return False
            self._pending.close()
            self._pending = self._make_pending(persist)
            self._persist_pending = persist
            return True

    def bind_pending_to_target(
        self, target: WindowMetadata, *, rule: AppRule | None = None
    ) -> bool:
        """Rebind the undelivered FIFO after an explicit user target selection."""
        with self._lock:
            context = self.context_provider.capture(target) if self.context_provider else None
            if self.context_provider and (
                context is None
                or not context.same_session
                or context.is_password
                or context.is_read_only is True
            ):
                return False
            records = self._pending.records()
            if not records:
                self._focus_anchor = context
                self._compatibility_rule = rule
                return True
            if len({item.target_identity for item in records}) > 1:
                return False
            replacement = self._make_pending(self._persist_pending)
            try:
                for item in records:
                    replacement.append(
                        item.text,
                        target_identity=target.identity,
                        fragment_id=item.fragment_id,
                        exact_text=item.exact_text,
                        strategy=item.strategy,
                    )
            except (OSError, BufferError):
                replacement.close()
                return False
            previous, self._pending = self._pending, replacement
            self._focus_anchor = context
            self._compatibility_rule = rule
            previous.close()
            return True

    @property
    def strategy(self) -> InjectionStrategy:
        with self._lock:
            return self._strategy

    @strategy.setter
    def strategy(self, value: InjectionStrategy | str) -> None:
        with self._lock:
            self._strategy = InjectionStrategy(value)

    @property
    def paste_delay(self) -> float:
        with self._lock:
            return self._paste_delay

    @paste_delay.setter
    def paste_delay(self, value: float) -> None:
        if value < 0:
            raise ValueError("paste_delay cannot be negative")
        with self._lock:
            self._paste_delay = float(value)

    @property
    def pending_fragments(self) -> int:
        return len(self._pending)

    @property
    def pending_characters(self) -> int:
        return self._pending.characters

    def delivery_status(self, fragment_id: str) -> DeliveryStatus | None:
        with self._lock:
            return self._delivery_statuses.get(fragment_id)

    def _tail_for(self, target: WindowMetadata) -> str:
        return self._tails.get(target.identity, "")

    def _remember_delivery(self, target: WindowMetadata, text: str) -> None:
        previous = self._tails.get(target.identity, "")
        self._tails[target.identity] = (previous + text)[-128:]

    def _same_target_is_ready(self, expected: WindowMetadata) -> bool:
        decision = self._targets.resolve()
        return (
            decision.can_inject
            and decision.target is not None
            and decision.target.identity == expected.identity
        )

    def _clipboard_paste(
        self,
        text: str,
        target: WindowMetadata,
        *,
        verify: Callable[[], bool],
        attempt_id: str,
    ) -> tuple[bool, SendInputError | None]:
        if (
            isinstance(self._sender, SendInputController)
            and not self._sender.wait_for_modifiers_released()
        ):
            raise ModifierStillPressedError
        snapshot = self._clipboard.snapshot()
        if not snapshot.complete:
            raise ClipboardError("Clipboard contains formats that cannot be restored safely")
        temporary_sequence = self._clipboard.set_text(
            text,
            expected_sequence_number=snapshot.sequence_number,
            rollback_snapshot=snapshot,
        )
        transaction = ClipboardTransaction(
            attempt_id,
            snapshot.sequence_number,
            temporary_sequence,
            target.identity,
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "preserve",
        )

        def check_delivery() -> None:
            if not verify():
                raise _TargetChanged("Target changed while preparing clipboard paste")
            if not self._clipboard.owns_sequence(transaction.clipboard_sequence_after_write):
                raise ClipboardError("Clipboard changed before the paste shortcut")

        send_error: SendInputError | None = None
        try:
            if isinstance(self._sender, SendInputController):
                self._sender.send_paste_shortcut(before_send=check_delivery)
            else:
                check_delivery()
                self._sender.send_paste_shortcut()
            delay = self._paste_delay
            if target.process_name.casefold() in _ASYNC_CLIPBOARD_READERS:
                delay = max(delay, _ASYNC_PASTE_MIN_DELAY_SECONDS)
            if delay:
                self._sleep(delay)
        except SendInputError as exc:
            send_error = exc
        finally:
            try:
                restored = self._clipboard.restore(
                    snapshot, expected_sequence_number=transaction.clipboard_sequence_after_write
                )
            except (ClipboardError, OSError):
                restored = False
        return restored, send_error

    def _inject_once(
        self,
        text: str,
        target: WindowMetadata,
        strategy: InjectionStrategy,
        *,
        context: ForegroundContext | None = None,
        attempt_id: str = "",
        verify_selection: Callable[[], bool] | None = None,
    ) -> tuple[InjectionStrategy, bool | None, str]:
        def verify_native() -> bool:
            if context is None:
                return self._same_target_is_ready(target)
            latest = ForegroundContextProvider.native(target, context.target_mode)
            return (
                not latest.is_password
                and latest.is_read_only is not True
                and FocusIdentityGuard.matches(context, latest, strict=False)
            )

        def verify_paste() -> bool:
            if verify_selection is not None and not verify_selection():
                return False
            if not self._same_target_is_ready(target) or not verify_native():
                return False
            if context is None or not context.runtime_id or self.context_provider is None:
                return True
            latest = self.context_provider.capture(target, mode=context.target_mode)
            return bool(
                latest
                and not latest.is_password
                and latest.is_read_only is not True
                and latest.password_checked
                and FocusIdentityGuard.matches(context, latest, strict=True)
            )

        if strategy in {InjectionStrategy.AUTO, InjectionStrategy.CLIPBOARD}:
            try:
                restored, send_error = self._clipboard_paste(
                    text, target, verify=verify_paste, attempt_id=attempt_id
                )
                if send_error is None:
                    detail = ""
                    if not restored:
                        detail = (
                            "Paste shortcut was sent, but the complete clipboard snapshot "
                            "could not be restored without overwriting newer content"
                        )
                    return InjectionStrategy.CLIPBOARD, restored, detail
                if (
                    strategy is InjectionStrategy.CLIPBOARD
                    or send_error.sent
                    or isinstance(send_error, ModifierStillPressedError)
                ):
                    raise send_error
            except _TargetChanged:
                raise
            except (ClipboardError, OSError, SendInputError) as exc:
                if strategy is InjectionStrategy.CLIPBOARD or (
                    isinstance(exc, SendInputError)
                    and (exc.sent or isinstance(exc, ModifierStillPressedError))
                ):
                    raise

        if not verify_paste():
            raise _TargetChanged("Target changed before Unicode fallback")
        if context is None and target.process_name.casefold() == "winword.exe":
            self._sleep(0.2)
            word_sender = SendInputController(
                self._sender.backend,
                max_chunk_units=2,
                inter_chunk_delay=0.02,
                sleep=self._sleep,
            )
            word_sender.send_focus_prime()
            self._sleep(0.05)
            word_sender.send_unicode(text)
        else:
            if context is None:
                self._sender.send_unicode(text)
            else:
                if not self._sender.wait_for_modifiers_released():
                    raise ModifierStillPressedError
                self._sender.send_unicode(
                    text, validate=verify_paste if context.runtime_id else verify_native
                )
        detail = (
            "Clipboard paste failed; Unicode fallback was used"
            if strategy is InjectionStrategy.AUTO
            else ""
        )
        return InjectionStrategy.UNICODE, None, detail

    @staticmethod
    def _status_for_decision(decision: TargetDecision) -> InjectionStatus:
        return InjectionStatus.BLOCKED if decision.requires_safe_copy else InjectionStatus.QUEUED

    def submit(
        self,
        text: str,
        *,
        strategy: InjectionStrategy | str | None = None,
        fragment_id: str = "",
        exact_text: bool = False,
        manual: bool = False,
        multiline_confirmed: bool = False,
    ) -> InjectionResult:
        """Accept a committed fragment, retaining it until delivery succeeds."""

        if not text:
            return InjectionResult(
                InjectionStatus.EMPTY,
                "",
                "",
                None,
                self.pending_fragments,
                None,
                None,
                detail="Empty fragments are ignored",
            )
        with self._lock:
            if fragment_id and fragment_id in self._known_fragment_ids:
                return InjectionResult(
                    InjectionStatus.DUPLICATE,
                    text,
                    "",
                    None,
                    len(self._pending),
                    None,
                    None,
                    detail="Fragment was already accepted; duplicate delivery was ignored",
                )
            try:
                binding = self._targets.resolve()
            except Exception as exc:
                self._pending.append(text, fragment_id=fragment_id, exact_text=exact_text)
                if fragment_id:
                    self._known_fragment_ids.add(fragment_id)
                    self._delivery_statuses[fragment_id] = DeliveryStatus.FAILED
                return InjectionResult(
                    InjectionStatus.FAILED,
                    text,
                    "",
                    None,
                    len(self._pending),
                    None,
                    None,
                    detail=f"Target resolution failed: {exc}",
                )
            target_identity = None if binding.target is None else binding.target.identity
            self._pending.append(
                text,
                target_identity=target_identity,
                fragment_id=fragment_id,
                exact_text=exact_text,
                strategy=InjectionStrategy(strategy).value if strategy is not None else "",
            )
            if fragment_id:
                self._known_fragment_ids.add(fragment_id)
                self._delivery_statuses[fragment_id] = DeliveryStatus.PENDING
            selected = self._strategy if strategy is None else InjectionStrategy(strategy)
            if selected is InjectionStrategy.SAFE_COPY and self.context_provider is None:
                return self.copy_pending_to_clipboard(consume=True, submitted_text=text)
            return self.flush(
                strategy=selected,
                submitted_text=text,
                retry=manual,
                multiline_confirmed=multiline_confirmed,
            )

    inject = submit

    def dispatch_explicit(
        self,
        text: str,
        target: WindowMetadata,
        *,
        verify_selection: Callable[[], bool] | None = None,
    ) -> bool:
        """Dispatch once to the selected target without draining dictation FIFO."""
        if not text:
            return False
        with self._lock:
            if not self._same_target_is_ready(target):
                return False
            try:
                strategy = self._strategy
                if strategy is InjectionStrategy.SAFE_COPY:
                    return False
                plan, context = self._adaptive_plan(target, text, strategy, manual=True, fresh=True)
                if plan is not None:
                    if plan.strategy in {InputStrategy.LOCAL_PREVIEW, InputStrategy.COPY_ONLY}:
                        return False
                    strategy = InjectionStrategy(plan.strategy.value)
                if verify_selection is not None:
                    # A replacement must be one paste. Unicode chunks would
                    # invalidate the selection after the first chunk.
                    if strategy not in {InjectionStrategy.AUTO, InjectionStrategy.CLIPBOARD}:
                        return False
                    strategy = InjectionStrategy.CLIPBOARD
                self._inject_once(
                    text,
                    target,
                    strategy,
                    context=context,
                    attempt_id=uuid4().hex,
                    verify_selection=verify_selection,
                )
            except (_TargetChanged, SendInputError, ClipboardError, OSError):
                return False
            return True

    def flush(
        self,
        *,
        strategy: InjectionStrategy | str | None = None,
        submitted_text: str = "",
        retry: bool = False,
        multiline_confirmed: bool = False,
    ) -> InjectionResult:
        attempt_id = uuid4().hex
        return replace(
            self._flush(
                strategy=strategy,
                submitted_text=submitted_text,
                retry=retry,
                multiline_confirmed=multiline_confirmed,
                attempt_id=attempt_id,
            ),
            attempt_id=attempt_id,
        )

    def _flush(
        self,
        *,
        strategy: InjectionStrategy | str | None,
        submitted_text: str,
        retry: bool,
        multiline_confirmed: bool,
        attempt_id: str,
    ) -> InjectionResult:
        with self._lock:
            selected = self._strategy if strategy is None else InjectionStrategy(strategy)
            if selected is InjectionStrategy.SAFE_COPY and self.context_provider is None:
                return self.copy_pending_to_clipboard(consume=True, submitted_text=submitted_text)
            if self._awaiting_explicit_retry and len(self._pending) and not retry:
                return InjectionResult(
                    InjectionStatus.FAILED,
                    submitted_text,
                    "",
                    selected,
                    len(self._pending),
                    None,
                    None,
                    detail="Pending delivery requires an explicit retry",
                )
            delivered_parts: list[str] = []
            used_strategy: InjectionStrategy | None = None
            restored: bool | None = None
            detail = ""
            last_target: WindowMetadata | None = None
            last_target_status: TargetStatus | None = None

            while len(self._pending):
                decision = self._targets.resolve()
                last_target = decision.target
                last_target_status = decision.status
                if not decision.can_inject or decision.target is None:
                    pending = self._pending.peek_record()
                    if pending is not None and pending.fragment_id:
                        self._delivery_statuses[pending.fragment_id] = (
                            DeliveryStatus.QUEUED_TARGET_INACTIVE
                        )
                    return InjectionResult(
                        self._status_for_decision(decision),
                        submitted_text,
                        "".join(delivered_parts),
                        used_strategy,
                        len(self._pending),
                        decision.target,
                        decision.status,
                        restored,
                        decision.reason,
                    )

                pending = self._pending.peek_record()
                if pending is None:
                    break
                if (
                    pending.target_identity is None
                    or decision.target.identity != pending.target_identity
                ):
                    return InjectionResult(
                        InjectionStatus.BLOCKED,
                        submitted_text,
                        "".join(delivered_parts),
                        used_strategy,
                        len(self._pending),
                        decision.target,
                        decision.status,
                        restored,
                        "Pending text belongs to a different or unknown target; "
                        "use safe copy instead",
                    )
                raw = pending.text
                preferred = InjectionStrategy(pending.strategy) if pending.strategy else selected
                plan, context = self._adaptive_plan(
                    decision.target,
                    raw,
                    preferred,
                    manual=retry,
                    multiline_confirmed=multiline_confirmed,
                )
                if plan is not None and plan.strategy in {
                    InputStrategy.COPY_ONLY,
                    InputStrategy.LOCAL_PREVIEW,
                }:
                    self._awaiting_explicit_retry = True
                    return InjectionResult(
                        InjectionStatus.BLOCKED,
                        submitted_text,
                        "".join(delivered_parts),
                        None,
                        len(self._pending),
                        decision.target,
                        decision.status,
                        restored,
                        plan.reason,
                    )
                if plan is not None:
                    preferred = InjectionStrategy(plan.strategy.value)
                    if plan.fallback_allowed and plan.strategy is InputStrategy.CLIPBOARD_PASTE:
                        preferred = InjectionStrategy.AUTO
                exact = pending.exact_text or (plan is not None and plan.expected_format == "exact")
                prepared = raw if exact else prepare_fragment(self._tail_for(decision.target), raw)
                if not prepared:
                    self._pending.popleft()
                    continue
                try:
                    used_strategy, restored, injection_detail = self._inject_once(
                        prepared,
                        decision.target,
                        preferred,
                        context=context,
                        attempt_id=attempt_id,
                    )
                except _TargetChanged as exc:
                    return InjectionResult(
                        InjectionStatus.QUEUED,
                        submitted_text,
                        "".join(delivered_parts),
                        used_strategy,
                        len(self._pending),
                        decision.target,
                        TargetStatus.TARGET_INACTIVE,
                        restored,
                        str(exc),
                    )
                except (ClipboardError, OSError, SendInputError) as exc:
                    if isinstance(exc, SendInputError) and exc.sent:
                        uncertain = self._pending.popleft_record()
                        self._awaiting_explicit_retry = True
                        if uncertain.fragment_id:
                            self._delivered_fragment_ids.add(uncertain.fragment_id)
                            self._delivery_statuses[uncertain.fragment_id] = DeliveryStatus.UNKNOWN
                        return InjectionResult(
                            InjectionStatus.UNKNOWN,
                            submitted_text,
                            "".join(delivered_parts),
                            used_strategy,
                            len(self._pending),
                            decision.target,
                            decision.status,
                            restored,
                            "Input may have reached the target; automatic retry was suppressed",
                        )
                    failed = self._pending.peek_record()
                    self._awaiting_explicit_retry = True
                    if failed is not None and failed.fragment_id:
                        self._delivery_statuses[failed.fragment_id] = DeliveryStatus.FAILED
                    return InjectionResult(
                        InjectionStatus.FAILED,
                        submitted_text,
                        "".join(delivered_parts),
                        used_strategy,
                        len(self._pending),
                        decision.target,
                        decision.status,
                        restored,
                        str(exc),
                    )
                delivered = self._pending.popleft_record()
                if delivered.fragment_id:
                    self._delivered_fragment_ids.add(delivered.fragment_id)
                    self._delivery_statuses[delivered.fragment_id] = DeliveryStatus.DELIVERED
                self._remember_delivery(decision.target, prepared)
                delivered_parts.append(prepared)
                detail = injection_detail or detail

            self._awaiting_explicit_retry = False
            return InjectionResult(
                InjectionStatus.INSERTED,
                submitted_text,
                "".join(delivered_parts),
                used_strategy,
                len(self._pending),
                last_target,
                last_target_status,
                restored,
                detail,
            )

    def copy_pending_to_clipboard(
        self, *, consume: bool = False, submitted_text: str = ""
    ) -> InjectionResult:
        """Explicit UIPI-safe fallback; no synthetic input is generated."""

        with self._lock:
            fragments = self._pending.records()
            prepared: list[str] = []
            tail = ""
            for fragment in fragments:
                piece = (
                    fragment.text if fragment.exact_text else prepare_fragment(tail, fragment.text)
                )
                prepared.append(piece)
                tail = (tail + piece)[-128:]
            text = "".join(prepared)
            if not text:
                return InjectionResult(
                    InjectionStatus.EMPTY,
                    submitted_text,
                    "",
                    None,
                    len(self._pending),
                    None,
                    None,
                    detail="There is no pending text to copy",
                )
            try:
                self._clipboard.copy_text(text)
            except (ClipboardError, OSError) as exc:
                return InjectionResult(
                    InjectionStatus.FAILED,
                    submitted_text,
                    "",
                    InjectionStrategy.SAFE_COPY,
                    len(self._pending),
                    None,
                    None,
                    detail=str(exc),
                )
            if consume:
                for _ in fragments:
                    consumed = self._pending.popleft_record()
                    if consumed.fragment_id:
                        self._delivered_fragment_ids.add(consumed.fragment_id)
                        self._delivery_statuses[consumed.fragment_id] = DeliveryStatus.DELIVERED
                self._awaiting_explicit_retry = False
            return InjectionResult(
                InjectionStatus.COPIED,
                submitted_text,
                text,
                InjectionStrategy.SAFE_COPY,
                len(self._pending),
                None,
                None,
                detail="Confirmed text is ready for a manual paste",
            )

    def copy_text_to_clipboard(self, text: str) -> bool:
        """Copy a complete result without consuming or reordering delivery FIFO."""

        if not text:
            return False
        try:
            self._clipboard.copy_text(text)
        except (ClipboardError, OSError):
            return False
        return True

    def close(self) -> None:
        with self._lock:
            self._pending.close()

    def __enter__(self) -> TextInjectionService:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
