from __future__ import annotations

import uuid

from voxtype.windows.single_instance import SingleInstanceGuard, Win32SingleInstanceBackend


class MemorySingleInstanceBackend:
    def __init__(self) -> None:
        self.names: set[str] = set()
        self.handles: dict[int, str] = {}
        self.next_handle = 1
        self.closed: list[int] = []
        self.activation_requests: list[tuple[str, float]] = []

    def create_mutex(self, name: str) -> tuple[int, bool]:
        handle = self.next_handle
        self.next_handle += 1
        already_exists = name in self.names
        self.names.add(name)
        self.handles[handle] = name
        return handle, already_exists

    def close_handle(self, handle: int) -> None:
        name = self.handles.pop(handle)
        self.closed.append(handle)
        if name not in self.handles.values():
            self.names.discard(name)

    def activate_window(self, title: str, timeout_s: float) -> bool:
        self.activation_requests.append((title, timeout_s))
        return True


def test_only_first_guard_owns_runtime_and_second_activates_existing_window() -> None:
    backend = MemorySingleInstanceBackend()
    owner = SingleInstanceGuard(backend=backend)
    duplicate = SingleInstanceGuard(backend=backend)

    assert owner.acquire()
    assert owner.is_owner
    assert not duplicate.acquire()
    assert not duplicate.is_owner
    assert duplicate.activate_existing(0.25)
    assert backend.activation_requests == [("VoxType — локальная диктовка", 0.25)]

    owner.close()
    assert SingleInstanceGuard(backend=backend).acquire()


def test_guard_is_idempotent_and_context_closes_only_owned_handle() -> None:
    backend = MemorySingleInstanceBackend()
    guard = SingleInstanceGuard(name="Local\\fixture", backend=backend)
    with guard:
        assert guard.is_owner
        assert guard.acquire()
        assert len(backend.handles) == 1
    assert not guard.is_owner
    assert not backend.handles


def test_invalid_mutex_name_is_rejected_before_native_call() -> None:
    backend = MemorySingleInstanceBackend()
    for name in ("", "bad\x00name"):
        try:
            SingleInstanceGuard(name=name, backend=backend)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid mutex name was accepted")
    assert not backend.handles


def test_real_win32_mutex_has_exactly_one_owner() -> None:
    name = rf"Local\VoxType.Test.{uuid.uuid4()}"
    first = SingleInstanceGuard(name=name, backend=Win32SingleInstanceBackend())
    second = SingleInstanceGuard(name=name, backend=Win32SingleInstanceBackend())
    assert first.acquire()
    try:
        assert not second.acquire()
    finally:
        first.close()
        second.close()
    replacement = SingleInstanceGuard(name=name, backend=Win32SingleInstanceBackend())
    try:
        assert replacement.acquire()
    finally:
        replacement.close()
