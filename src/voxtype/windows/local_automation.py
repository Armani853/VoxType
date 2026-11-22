"""Opt-in Windows named-pipe API with a transient capability token."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import Any
from uuid import UUID

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_MAX_REQUEST = 4096
_MAX_CLIENTS = 4


class AutomationRejected(ValueError):
    """An authenticated client invoked a method that cannot run."""


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_field")
        result[key] = value
    return result


class LocalAutomation(QObject):
    def __init__(
        self,
        dispatch: Callable[[str, dict[str, object]], object],
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self._dispatch = dispatch
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.setMaxPendingConnections(_MAX_CLIENTS)
        self._server.newConnection.connect(self._accept)
        self._sockets: set[QLocalSocket] = set()
        self._session: int | None = None
        self._token = ""
        self.status = "Выключен"

    @property
    def credentials(self) -> tuple[str, str] | None:
        return (self._server.fullServerName(), self._token) if self._server.isListening() else None

    def configure(self, enabled: bool) -> None:
        if enabled and self._server.isListening():
            return
        self.close()
        if not enabled:
            return
        if os.name != "nt":
            self.status = "Доступен только через именованный канал Windows"
            return
        try:
            library = ctypes.WinDLL("kernel32")
            session = wintypes.DWORD()
            process_session = library.ProcessIdToSessionId
            process_session.argtypes = (wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
            process_session.restype = wintypes.BOOL
            if not process_session(os.getpid(), ctypes.byref(session)):
                raise OSError("Could not identify Windows session")
            peer_session = library.GetNamedPipeClientSessionId
            peer_session.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG))
            peer_session.restype = wintypes.BOOL
        except (AttributeError, OSError):
            self.status = "Windows не подтвердила изоляцию канала"
            return
        self._session = session.value
        identity = hashlib.sha256(str(Path.home()).casefold().encode("utf-8")).hexdigest()[:24]
        if not self._server.listen(f"VoxType-automation-v1-{identity}-{self._session}"):
            self.status = "Не удалось открыть локальный канал"
            return
        self._token = secrets.token_urlsafe(32)
        self.status = "Доступен для текущего пользователя и сеанса Windows"

    def close(self) -> None:
        self._server.close()
        self._token = ""
        self._session = None
        self.status = "Выключен"
        for socket in tuple(self._sockets):
            socket.abort()
            socket.deleteLater()
        self._sockets.clear()

    def _trusted_peer(self, socket: QLocalSocket) -> bool:
        if self._session is None or socket.socketDescriptor() <= 0:
            return False
        try:
            function = ctypes.WinDLL("kernel32").GetNamedPipeClientSessionId
            function.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG))
            function.restype = wintypes.BOOL
            session = wintypes.ULONG()
            return bool(function(socket.socketDescriptor(), ctypes.byref(session))) and (
                session.value == self._session
            )
        except (AttributeError, OSError):
            return False

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                return
            if len(self._sockets) >= _MAX_CLIENTS or not self._trusted_peer(socket):
                socket.abort()
                socket.deleteLater()
                continue
            self._sockets.add(socket)
            socket.setReadBufferSize(_MAX_REQUEST + 1)
            timeout = QTimer(socket, singleShot=True, interval=3000)
            timeout.timeout.connect(socket.abort)
            timeout.start()
            socket.disconnected.connect(lambda current=socket: self._forget(current))
            socket.readyRead.connect(lambda current=socket: self._read(current))
            self._read(socket)

    def _forget(self, socket: QLocalSocket) -> None:
        self._sockets.discard(socket)
        socket.deleteLater()

    def _read(self, socket: QLocalSocket) -> None:
        if socket.bytesAvailable() > _MAX_REQUEST:
            self._reply(socket, None, error="request_too_large")
        elif socket.canReadLine():
            frame = bytes(socket.readLine(_MAX_REQUEST + 1))
            if socket.bytesAvailable():
                self._reply(socket, None, error="one_request_per_connection")
            else:
                self._handle(socket, frame)

    def _handle(self, socket: QLocalSocket, frame: bytes) -> None:
        request_id = None
        try:
            message = json.loads(frame.decode("utf-8"), object_pairs_hook=_unique_fields)
            if (
                not isinstance(message, dict)
                or set(message) != {"schema_version", "request_id", "method", "params", "token"}
                or type(message["schema_version"]) is not int
                or message["schema_version"] != 1
                or not isinstance(message["request_id"], str)
                or len(message["request_id"]) > 36
            ):
                raise ValueError
            request_id = str(UUID(message["request_id"]))
            method = message["method"]
            params = message["params"]
            token = message["token"]
            if not isinstance(method, str) or not isinstance(params, dict):
                raise ValueError
        except (ValueError, RecursionError):
            self._reply(socket, request_id, error="invalid_request")
            return
        if (
            not isinstance(token, str)
            or not token.isascii()
            or not self._token
            or not secrets.compare_digest(token, self._token)
        ):
            self._reply(socket, request_id, error="unauthorized")
            return
        try:
            result = self._dispatch(method, params)
        except AutomationRejected as exc:
            self._reply(socket, request_id, error=str(exc))
        except Exception:
            self._reply(socket, request_id, error="operation_failed")
        else:
            self._reply(socket, request_id, result=result)

    @staticmethod
    def _reply(
        socket: QLocalSocket,
        request_id: str | None,
        *,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        response = {
            "schema_version": 1,
            "request_id": request_id,
            "ok": error is None,
            "result": result if error is None else None,
            "error": error,
        }
        socket.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
        socket.disconnectFromServer()
