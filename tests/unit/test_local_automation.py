from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalSocket
from pytestqt.qtbot import QtBot

from voxtype.windows.local_automation import LocalAutomation


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named pipe and peer-session ACL")
def test_named_pipe_requires_capability_and_rotates_it(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    parent = QObject()
    dispatched: list[str] = []

    def dispatch(method: str, _params: dict[str, object]) -> object:
        dispatched.append(method)
        return {"ready": True}

    service = LocalAutomation(dispatch, parent)
    assert service.credentials is None
    service.configure(True)
    assert service.credentials is not None
    endpoint, token = service.credentials

    def request(provided_token: str) -> dict[str, object]:
        socket = QLocalSocket()
        socket.connectToServer(endpoint)
        qtbot.waitUntil(lambda: socket.state() == QLocalSocket.LocalSocketState.ConnectedState)
        socket.write(
            json.dumps(
                {
                    "schema_version": 1,
                    "request_id": str(uuid4()),
                    "method": "get_status",
                    "params": {},
                    "token": provided_token,
                }
            ).encode("utf-8")
            + b"\n"
        )
        qtbot.waitUntil(socket.canReadLine)
        return json.loads(bytes(socket.readLine()))

    try:
        assert request("not-authorized")["error"] == "unauthorized"
        assert not dispatched
        assert request(token)["result"] == {"ready": True}
        assert dispatched == ["get_status"]
        service.configure(False)
        assert service.credentials is None
        service.configure(True)
        assert service.credentials is not None
        assert service.credentials[1] != token
    finally:
        service.close()
