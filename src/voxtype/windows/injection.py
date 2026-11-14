"""Ordered, target-safe text delivery with clipboard and Unicode fallbacks."""

from __future__ import annotations

import json
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .clipboard import ClipboardError, ClipboardManager
from .foreground import WindowMetadata
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


class PendingTextQueue:
    """FIFO that automatically spills sensitive pending text out of RAM.

    The OS removes the anonymous temporary file when it is closed. Durable crash
    recovery remains the responsibility of SessionJournal.
    """

    def __init__(self, *, memory_limit_bytes: int = 256 * 1024) -> None:
        if memory_limit_bytes < 1:
            raise ValueError("memory_limit_bytes must be positive")
        self._memory_limit_bytes = memory_limit_bytes
        self._stream = tempfile.SpooledTemporaryFile(  # noqa: SIM115
            max_size=memory_limit_bytes, mode="w+b"
        )
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
    ) -> None:
        if not text:
            return
        payload = {
            "target": None if target_identity is None else list(target_identity),
            "text": text,
            "fragment_id": fragment_id,
        }
        record = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        with self._lock:
            self._stream.seek(0, 2)
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
        return PendingFragment(value["text"], target, str(value.get("fragment_id", "")))

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
        with self._lock:
            offset = self._read_offset
            remaining = self._count
            values: list[str] = []
            self._stream.seek(offset)
            for _ in range(remaining):
                line = self._stream.readline()
                if not line:
                    raise RuntimeError("pending-text spool ended before its index")
                values.append(self._decode_record(line).text)
        return iter(values)

    def _reset_stream(self) -> None:
        self._stream.close()
        self._stream = tempfile.SpooledTemporaryFile(  # noqa: SIM115
            max_size=self._memory_limit_bytes, mode="w+b"
        )
        self._read_offset = 0

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
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if paste_delay < 0:
            raise ValueError("paste_delay cannot be negative")
        self._targets = target_manager
        self._clipboard = clipboard or ClipboardManager()
        self._sender = sender or SendInputController()
        self._strategy = InjectionStrategy(strategy)
        self._paste_delay = paste_delay
        self._sleep = sleep
        self._pending = PendingTextQueue(memory_limit_bytes=pending_memory_bytes)
        self._tails: dict[tuple[int, int], str] = {}
        self._known_fragment_ids: set[str] = set()
        self._delivered_fragment_ids: set[str] = set()
        self._delivery_statuses: dict[str, DeliveryStatus] = {}
        self._lock = threading.RLock()

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
        self, text: str, target: WindowMetadata
    ) -> tuple[bool, SendInputError | None]:
        snapshot = self._clipboard.snapshot()
        if not snapshot.complete:
            raise ClipboardError("Clipboard contains formats that cannot be restored safely")
        temporary_sequence = self._clipboard.set_text(
            text,
            expected_sequence_number=snapshot.sequence_number,
            rollback_snapshot=snapshot,
        )
        send_error: SendInputError | None = None
        try:
            if not self._same_target_is_ready(target):
                raise _TargetChanged("Target changed while preparing clipboard paste")
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
                    snapshot, expected_sequence_number=temporary_sequence
                )
            except (ClipboardError, OSError):
                restored = False
            if not restored:
                # Some editors (notably Word) strip custom formats while
                # enriching CF_UNICODETEXT during paste. If the clipboard still
                # contains exactly our submitted phrase, content ownership is
                # unambiguous and the original snapshot can be restored without
                # overwriting unrelated user data.
                try:
                    if self._clipboard.get_text() == text:
                        restored = self._clipboard.restore(snapshot)
                except (ClipboardError, OSError):
                    restored = False
        return restored, send_error

    def _inject_once(
        self,
        text: str,
        target: WindowMetadata,
        strategy: InjectionStrategy,
    ) -> tuple[InjectionStrategy, bool | None, str]:
        if strategy in {InjectionStrategy.AUTO, InjectionStrategy.CLIPBOARD}:
            try:
                restored, send_error = self._clipboard_paste(text, target)
                if send_error is None:
                    detail = ""
                    if not restored:
                        detail = (
                            "Text was pasted, but the complete clipboard snapshot "
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

        if not self._same_target_is_ready(target):
            raise _TargetChanged("Target changed before Unicode fallback")
        if target.process_name.casefold() == "winword.exe":
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
            self._sender.send_unicode(text)
        return InjectionStrategy.UNICODE, None, "Clipboard paste failed; Unicode fallback was used"

    @staticmethod
    def _status_for_decision(decision: TargetDecision) -> InjectionStatus:
        return InjectionStatus.BLOCKED if decision.requires_safe_copy else InjectionStatus.QUEUED

    def submit(
        self,
        text: str,
        *,
        strategy: InjectionStrategy | str | None = None,
        fragment_id: str = "",
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
                self._pending.append(text, fragment_id=fragment_id)
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
            )
            if fragment_id:
                self._known_fragment_ids.add(fragment_id)
                self._delivery_statuses[fragment_id] = DeliveryStatus.PENDING
            selected = self._strategy if strategy is None else InjectionStrategy(strategy)
            if selected is InjectionStrategy.SAFE_COPY:
                return self.copy_pending_to_clipboard(consume=True, submitted_text=text)
            return self.flush(strategy=selected, submitted_text=text)

    inject = submit

    def flush(
        self,
        *,
        strategy: InjectionStrategy | str | None = None,
        submitted_text: str = "",
    ) -> InjectionResult:
        with self._lock:
            selected = self._strategy if strategy is None else InjectionStrategy(strategy)
            if selected is InjectionStrategy.SAFE_COPY:
                return self.copy_pending_to_clipboard(consume=True, submitted_text=submitted_text)
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
                prepared = prepare_fragment(self._tail_for(decision.target), raw)
                if not prepared:
                    self._pending.popleft()
                    continue
                try:
                    used_strategy, restored, injection_detail = self._inject_once(
                        prepared, decision.target, selected
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
            fragments = tuple(iter(self._pending))
            text = join_fragments(fragments)
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
