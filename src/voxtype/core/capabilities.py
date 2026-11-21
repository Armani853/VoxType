"""Capabilities reported from the services this process can actually use."""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib.util import find_spec


@dataclass(frozen=True, slots=True)
class Capability:
    available: bool
    reason: str


class CapabilityRegistry:
    @staticmethod
    def snapshot(controller, *, file_queue=None, enhancer=None) -> dict[str, Capability]:
        policy = controller.policy
        model_id = controller.backend.model_id
        matching_model = controller._model_available and policy.model_allowed(model_id)
        decoder = find_spec("av") is not None
        selection = os.name == "nt" and find_spec("comtypes") is not None
        ai_allowed = not policy.disable_external_processing
        return {
            "local_asr": Capability(
                matching_model, model_id if matching_model else "Нужна разрешённая локальная модель"
            ),
            "auto_language": Capability(False, "Текущий тракт требует явного выбора RU или EN"),
            "file_transcription": Capability(
                decoder and file_queue is not None,
                "PyAV · WAV, MP3, M4A, FLAC"
                if decoder and file_queue is not None
                else "Очередь или PyAV недоступны",
            ),
            "custom_vocabulary": Capability(True, "Личные, профильные и прикладные термины"),
            "snippets": Capability(
                policy.allow_user_snippets,
                "AutoText" if policy.allow_user_snippets else "Запрещено политикой",
            ),
            "voice_commands": Capability(
                policy.allow_voice_commands,
                "Точный триггер или режим команд"
                if policy.allow_voice_commands
                else "Запрещено политикой",
            ),
            "selection_editing": Capability(
                selection,
                "UI Automation TextPattern"
                if selection
                else "Доступно редактирование вручную вставленного текста",
            ),
            "organization_policies": Capability(os.name == "nt", controller.enterprise.status),
            "local_history": Capability(
                not policy.disable_history,
                "Локальная SQLite" if not policy.disable_history else "Отключена политикой",
            ),
            "optional_ai_backend": Capability(
                enhancer is not None and ai_allowed,
                "Локальная GGUF-модель"
                if enhancer is not None and ai_allowed
                else (
                    "Выберите локальную модель и установите llama-cpp-python"
                    if ai_allowed
                    else "Запрещено политикой"
                ),
            ),
        }
