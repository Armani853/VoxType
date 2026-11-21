"""Read local administrator configuration only from a protected Windows location."""

from __future__ import annotations

import ctypes
import json
import ntpath
import os
from ctypes import wintypes
from dataclasses import dataclass, fields
from pathlib import Path

from voxtype.core.profiles import DictationContext, normalized_executable

_PRIVILEGED_SIDS = (
    "S-1-5-18",
    "S-1-5-32-544",
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
)
_WRITE_ACCESS = 0x500D0156  # generic write/all, ownership, ACL, delete, data and attribute writes


@dataclass(frozen=True, slots=True)
class EnterprisePolicy:
    enforce_offline_only: bool = False
    disable_history: bool = False
    disable_audio_storage: bool = False
    disable_external_processing: bool = False
    allowed_models: tuple[str, ...] = ()
    allowed_profiles: tuple[str, ...] = ()
    disabled_applications: tuple[str, ...] = ()
    allow_user_snippets: bool = True
    allow_voice_commands: bool = True

    @classmethod
    def parse(cls, raw: object) -> EnterprisePolicy:
        if not isinstance(raw, dict):
            raise ValueError("Неверная структура политик")
        allowed = {item.name for item in fields(cls)}
        if set(raw) - allowed:
            raise ValueError("Неизвестные корпоративные настройки")
        values = {}
        for key, value in raw.items():
            if key in {"allowed_models", "allowed_profiles", "disabled_applications"}:
                if (
                    not isinstance(value, (list, tuple))
                    or len(value) > 500
                    or any(
                        not isinstance(item, str) or not item.strip() or len(item) > 1024
                        for item in value
                    )
                ):
                    raise ValueError("Неверный список корпоративных ограничений")
                values[key] = tuple(value)
            elif type(value) is bool:
                values[key] = value
            else:
                raise ValueError("Корпоративный переключатель должен быть логическим")
        return cls(**values)

    def model_allowed(self, model: str) -> bool:
        return not self.allowed_models or model in self.allowed_models

    def profile_allowed(self, profile_id: str) -> bool:
        return not self.allowed_profiles or profile_id in self.allowed_profiles

    def blocked(self, context: DictationContext) -> bool:
        path = normalized_executable(context.process_path or "")
        name = normalized_executable(context.executable_name)
        for entry in self.disabled_applications:
            key = normalized_executable(entry)
            if key and key == (path if ntpath.dirname(key) else name):
                return True
        return False


class EnterprisePolicyService:
    def __init__(self) -> None:
        self.policy = EnterprisePolicy()
        self.status = "Не настроена"
        self.path: Path | None = None
        if os.name != "nt":
            return
        try:
            base = self._program_data()
            path = base / "VoxType" / "enterprise-policy.json"
            self.path = path
            if not path.is_file():
                return
            if (
                not self._trusted(base, parent_only=True)
                or not self._trusted(path.parent)
                or not self._trusted(path)
            ):
                self.status = "Отклонена: владелец или права NTFS допускают изменение"
                return
            with path.open("rb") as source:
                data = source.read(65_537)
            if len(data) > 65_536:
                raise ValueError("Файл политик слишком большой")
            document = json.loads(data.decode("utf-8-sig"))
            if not isinstance(document, dict) or document.get("schema_version") != 1:
                raise ValueError("Неизвестная версия политики")
            self.policy = EnterprisePolicy.parse(document.get("policy"))
        except (OSError, UnicodeError, ValueError, TypeError):
            self.status = "Отклонена: файл недоступен или повреждён"
        else:
            self.status = "Активна"

    @staticmethod
    def _program_data() -> Path:
        output = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x23, None, 0, output):
            raise OSError("ProgramData unavailable")
        return Path(output.value)

    @staticmethod
    def _trusted(path: Path, *, parent_only: bool = False) -> bool:
        if getattr(path.lstat(), "st_file_attributes", 0) & 0x400:  # reparse point
            return False
        security = ctypes.windll.advapi32
        kernel = ctypes.windll.kernel32
        kernel.LocalFree.argtypes = (ctypes.c_void_p,)
        kernel.LocalFree.restype = ctypes.c_void_p
        owner = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        descriptor = ctypes.c_void_p()
        security.GetNamedSecurityInfoW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        if security.GetNamedSecurityInfoW(
            str(path),
            1,
            5,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        ):
            return False
        sid_strings: list[ctypes.c_void_p] = []

        def sid_string(sid: ctypes.c_void_p) -> str:
            value = ctypes.c_void_p()
            if not security.ConvertSidToStringSidW(sid, ctypes.byref(value)):
                raise OSError("Invalid SID")
            sid_strings.append(value)
            return ctypes.wstring_at(value)

        try:
            if not (owner and dacl) or sid_string(owner) not in _PRIVILEGED_SIDS:
                return False
            # ACL header: revision/reserved, ACL size, ACE count/reserved.
            count = ctypes.c_uint16.from_address(dacl.value + 4).value
            for index in range(count):
                ace = ctypes.c_void_p()
                if not security.GetAce(dacl, index, ctypes.byref(ace)):
                    return False
                ace_type = ctypes.c_ubyte.from_address(ace.value).value
                flags = ctypes.c_ubyte.from_address(ace.value + 1).value
                if flags & 8:  # inherit-only entries do not grant rights on this object
                    continue
                if ace_type == 1:  # access denied ACE does not grant write permissions
                    continue
                if ace_type != 0:
                    return False
                mask = ctypes.c_uint32.from_address(ace.value + 4).value
                sensitive = 0x500D0040 if parent_only else _WRITE_ACCESS
                if mask & sensitive:
                    sid = sid_string(ctypes.c_void_p(ace.value + 8))
                    if sid not in _PRIVILEGED_SIDS and sid != "S-1-3-0":  # creator owner
                        return False
            return True
        finally:
            for value in sid_strings:
                kernel.LocalFree(value)
            kernel.LocalFree(descriptor)
