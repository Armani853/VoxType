"""Deterministic local workflows with a fixed data-only action set."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from voxtype.windows.local_paths import is_local_path

if TYPE_CHECKING:
    from collections.abc import Callable
    from voxtype.settings.repository import SettingsRepository
    from voxtype.sessions.repository import SessionRepository

ACTIONS = {"save_txt", "save_json", "apply_template", "copy", "preview", "offer_export"}
TRIGGERS = {"dictation", "file", "manual"}


@dataclass(frozen=True, slots=True)
class WorkflowAction:
    kind: str
    template: str = ""


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    id: str
    name: str
    trigger_type: str
    profile_id: str
    actions: tuple[WorkflowAction, ...]
    enabled: bool


@dataclass(frozen=True, slots=True)
class StepResult:
    workflow: str
    action: str
    status: str


_DRAFTS = (
    ("meeting", "Meeting Notes Draft", "Встреча\nДата: [заполнить]\nУчастники: [заполнить]"),
    ("email", "Business Email Draft", "Кому: [заполнить]\nТема: [заполнить]"),
    ("technical", "Technical Notes", "Техническая заметка\nПроект: [заполнить]"),
    ("interview", "Interview Transcript", "Интервью\nСобеседник: [заполнить]"),
    ("legal", "Legal Dictation Template", "Юридическая диктовка\nДело: [заполнить]"),
    ("medical", "Medical Dictation Template", "Медицинская диктовка\nИдентификатор: [заполнить]"),
)

WORKFLOW_TEMPLATES = tuple(
    WorkflowDefinition(key, name, "manual", "", (
        WorkflowAction("apply_template", heading + "\n\n{text}"), WorkflowAction("preview"),
    ), True)
    for key, name, heading in _DRAFTS
)


def parse_workflows(rows: object) -> tuple[WorkflowDefinition, ...]:
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("Допускается до 100 локальных процессов")
    workflows: list[WorkflowDefinition] = []
    ids: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict) or not isinstance(raw.get("actions"), list):
            raise ValueError("Неверная структура процесса")
        actions = tuple(WorkflowAction(**item) for item in raw["actions"])
        workflow = WorkflowDefinition(**{**raw, "actions": actions})
        if (not isinstance(workflow.id, str) or not workflow.id.isalnum() or workflow.id in ids
                or not isinstance(workflow.name, str) or not 0 < len(workflow.name.strip()) <= 100
                or not isinstance(workflow.profile_id, str) or type(workflow.enabled) is not bool
                or workflow.trigger_type not in TRIGGERS or not 1 <= len(actions) <= 12):
            raise ValueError("Неверный ID, режим или число шагов процесса")
        if any(step.kind not in ACTIONS or not isinstance(step.template, str)
               or len(step.template) > 8192 for step in actions):
            raise ValueError("Недопустимое действие или слишком длинный шаблон")
        ids.add(workflow.id)
        workflows.append(workflow)
    return tuple(workflows)


class WorkflowService:
    def __init__(
        self, repository: SettingsRepository, sessions: SessionRepository, configuration: dict[str, Any],
    ) -> None:
        self._repository = repository
        self._sessions = sessions
        self._lock = threading.RLock()
        try:
            self._definitions = parse_workflows(configuration.get("workflows", []))
        except (ValueError, TypeError):
            self._definitions = ()
        self._directory = str(configuration.get("workflow_directory", ""))
        try:
            self.last_steps = tuple(StepResult(**row) for row in sessions.latest_workflow_steps())
        except (AttributeError, sqlite3.Error, TypeError, ValueError):
            self.last_steps = ()
        self.desktop_action: Callable[[str, str, str], None] | None = None

    def definitions(self) -> tuple[WorkflowDefinition, ...]:
        with self._lock:
            return self._definitions

    def save(self, definitions: tuple[WorkflowDefinition, ...]) -> None:
        with self._lock:
            data = json.loads(json.dumps([asdict(item) for item in definitions], ensure_ascii=False))
            self._repository.update({"professional": {"schema_version": 1, "workflows": data}})
            self._definitions = definitions

    def copy_template(self, template: WorkflowDefinition) -> WorkflowDefinition:
        return replace(template, id=uuid4().hex)

    def set_directory(self, directory: Path) -> None:
        if not is_local_path(directory.absolute()):
            raise ValueError("Выберите локальную папку")
        resolved = directory.resolve(strict=True)
        if not resolved.is_dir() or not is_local_path(resolved):
            raise ValueError("Выберите локальную папку")
        with self._lock:
            self._repository.update({"professional": {"workflow_directory": str(resolved)}})
            self._directory = str(resolved)

    def dispatch(self, trigger: str, profile_id: str, text: str, *, workflow_id: str = "") -> tuple[StepResult, ...]:
        if not text.strip():
            return ()
        with self._lock:
            definitions, directory = self._definitions, self._directory
        steps: list[StepResult] = []
        for definition in definitions:
            if (not definition.enabled or definition.trigger_type != trigger
                    or definition.profile_id and definition.profile_id != profile_id
                    or workflow_id and definition.id != workflow_id):
                continue
            value = text
            for action in definition.actions:
                try:
                    match action.kind:
                        case "apply_template":
                            fields = action.template.count("{text}")
                            if not fields:
                                raise ValueError("В шаблоне отсутствует {text}")
                            if len(action.template) + fields * (len(value) - len("{text}")) > 4_000_000:
                                raise ValueError("Слишком длинный результат")
                            value = action.template.replace("{text}", value)
                        case "save_txt" | "save_json":
                            self._save_text(directory, value, profile_id, action.kind)
                        case "copy" | "preview" | "offer_export":
                            if self.desktop_action is None:
                                raise RuntimeError("Панель приложения недоступна")
                            self.desktop_action(action.kind, definition.name, value)
                    steps.append(StepResult(definition.name, action.kind, "completed"))
                except (OSError, ValueError, RuntimeError):
                    steps.append(StepResult(definition.name, action.kind, "failed"))
                    break
        with self._lock:
            if steps:
                self.last_steps = tuple(steps)
                try:
                    self._sessions.save_workflow_steps([asdict(item) for item in steps])
                except (AttributeError, sqlite3.Error):
                    pass
        return tuple(steps)

    def _save_text(self, directory: str, text: str, profile_id: str, kind: str) -> None:
        if not directory:
            raise ValueError("Выходная папка не выбрана")
        path = Path(directory)
        if not is_local_path(path):
            raise ValueError("Выберите локальную папку")
        path = path.resolve(strict=True)
        if not path.is_dir() or not is_local_path(path):
            raise ValueError("Выберите локальную папку")
        extension = ".json" if kind == "save_json" else ".txt"
        if kind == "save_json":
            payload = json.dumps({"schema_version": 1, "profile_id": profile_id, "text": text},
                                 ensure_ascii=False, indent=2)
        else:
            payload = text
        with NamedTemporaryFile(dir=path, delete=False) as stream:
            temporary = Path(stream.name)
        try:
            temporary.write_text(payload, encoding="utf-8")
            destination = path / f"VoxType-{uuid4().hex}{extension}"
            if os.name == "nt":
                os.rename(temporary, destination)
            else:
                os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
