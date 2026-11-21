"""Explicit, atomic local model installation, verification and removal."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import Event, RLock
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .profiles import ASRProfile

ProgressCallback = Callable[[int, int | None], None]
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MARKER = ".voxtype-model.json"


class ModelIntegrityState(StrEnum):
    MISSING = "missing"
    INCOMPLETE = "incomplete"
    CORRUPTED = "corrupted"
    INCOMPATIBLE = "incompatible"
    READY = "ready"


class ModelManagerError(RuntimeError):
    """A model install, integrity check, or lifecycle action failed."""


class ModelInstallCancelled(ModelManagerError):
    """Installation was cancelled before the model was published."""


@dataclass(frozen=True, slots=True)
class ModelSpec:
    model_id: str
    repository_id: str | None = None
    revision: str = "main"
    archive_url: str | None = None
    archive_sha256: str | None = None
    expected_size_bytes: int | None = None
    required_files: tuple[str, ...] = ("config.json", "model.bin")

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.model_id):
            raise ValueError("model_id contains unsafe path characters")
        if self.repository_id and self.archive_url:
            raise ValueError("repository_id and archive_url are mutually exclusive")
        if self.archive_sha256 is not None and not re.fullmatch(
            r"[0-9a-fA-F]{64}", self.archive_sha256
        ):
            raise ValueError("archive_sha256 must be a 64-character hex digest")
        if self.expected_size_bytes is not None and self.expected_size_bytes <= 0:
            raise ValueError("expected_size_bytes must be positive")
        if any(
            Path(name).is_absolute() or ".." in Path(name).parts for name in self.required_files
        ):
            raise ValueError("required_files must stay inside the model directory")


@dataclass(frozen=True, slots=True)
class InstalledModel:
    model_id: str
    path: Path
    repository_id: str | None
    revision: str
    installed_at: str
    size_bytes: int
    verified: bool


DEFAULT_MODEL_SPECS: dict[ASRProfile, ModelSpec] = {
    ASRProfile.FAST: ModelSpec(
        "small",
        repository_id="Systran/faster-whisper-small",
        revision="536b0662742c02347bc0e980a01041f333bce120",
    ),
    ASRProfile.BALANCED: ModelSpec(
        "small",
        repository_id="Systran/faster-whisper-small",
        revision="536b0662742c02347bc0e980a01041f333bce120",
    ),
    ASRProfile.MAXIMUM_ACCURACY: ModelSpec(
        "large-v3-turbo",
        repository_id="mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        revision="0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
    ),
}


class ModelManager:
    """Own models under one directory; network access occurs only in install."""

    def __init__(self, models_directory: str | Path) -> None:
        self.models_directory = Path(models_directory).resolve()
        self.models_directory.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._inventory_signature: tuple[tuple[str, int, int], ...] = ()
        self._inventory: tuple[InstalledModel, ...] | None = None
        self._remove_stale_temporary_entries()

    def _remove_stale_temporary_entries(self, *, minimum_age_seconds: float = 86_400.0) -> None:
        cutoff = time.time() - minimum_age_seconds
        for candidate in self.models_directory.iterdir():
            if not candidate.name.startswith(".") or candidate.stat().st_mtime >= cutoff:
                continue
            if candidate.name.endswith((".staging", ".flat")) and candidate.is_dir():
                shutil.rmtree(candidate, ignore_errors=True)
            elif candidate.name.endswith(".download") and candidate.is_file():
                candidate.unlink(missing_ok=True)

    def path_for(self, model_id: str) -> Path:
        if not _SAFE_ID.fullmatch(model_id):
            raise ValueError("unsafe model identifier")
        path = (self.models_directory / model_id).resolve()
        if path.parent != self.models_directory:
            raise ValueError("model path escaped the managed directory")
        return path

    def is_installed(self, model_id: str, *, verify: bool = False) -> bool:
        return self.integrity_state(model_id, verify=verify) is ModelIntegrityState.READY

    def integrity_state(self, model_id: str, *, verify: bool = False) -> ModelIntegrityState:
        path = self.path_for(model_id)
        if not path.is_dir():
            return ModelIntegrityState.MISSING
        marker_path = path / _MARKER
        if not marker_path.is_file():
            return ModelIntegrityState.INCOMPLETE
        try:
            with marker_path.open("rb") as stream:
                raw = stream.read(1_048_577)
            if len(raw) > 1_048_576:
                return ModelIntegrityState.CORRUPTED
            marker = json.loads(raw)
            if not isinstance(marker, dict) or marker.get("model_id") != model_id:
                return ModelIntegrityState.CORRUPTED
            if marker.get("schema") != 1:
                return ModelIntegrityState.INCOMPATIBLE
            files = marker.get("files")
            if not isinstance(files, dict):
                return ModelIntegrityState.CORRUPTED
            required = next(
                (
                    spec.required_files
                    for spec in DEFAULT_MODEL_SPECS.values()
                    if spec.model_id == model_id
                ),
                ("config.json", "model.bin"),
            )
            for name in required:
                candidate = (path / name).resolve()
                if path not in candidate.parents or not candidate.is_file():
                    return ModelIntegrityState.INCOMPLETE
                entry = files.get(name)
                size = candidate.stat().st_size
                if not isinstance(entry, dict) or not size or entry.get("size") != size:
                    return ModelIntegrityState.CORRUPTED
            if verify and not self.verify(model_id):
                return ModelIntegrityState.CORRUPTED
        except (OSError, ValueError, TypeError, RecursionError):
            return ModelIntegrityState.CORRUPTED
        return ModelIntegrityState.READY

    def matches_spec(self, spec: ModelSpec) -> bool:
        marker_path = self.path_for(spec.model_id) / _MARKER
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        return bool(
            marker.get("repository_id") == spec.repository_id
            and marker.get("revision") == spec.revision
        )

    def required_free_space(self, spec: ModelSpec) -> int:
        expected = spec.expected_size_bytes or 3 * 1024**3
        # Download/archive plus extracted model can briefly coexist.
        return int(expected * 2.2) + 256 * 1024**2

    def ensure_free_space(self, spec: ModelSpec) -> None:
        required = self.required_free_space(spec)
        available = shutil.disk_usage(self.models_directory).free
        if available < required:
            raise ModelManagerError(
                f"Недостаточно места для модели {spec.model_id}: нужно {required} байт, доступно {available}."
            )

    def install(
        self,
        spec: ModelSpec,
        *,
        progress: ProgressCallback | None = None,
        cancel: Event | None = None,
    ) -> Path:
        """Download to staging, verify, then publish with one directory rename."""

        def callback(downloaded: int, total: int | None) -> None:
            if cancel is not None and cancel.is_set():
                raise ModelInstallCancelled("Загрузка отменена")
            if progress is not None:
                progress(downloaded, total)

        with self._lock:
            callback(0, spec.expected_size_bytes)
            destination = self.path_for(spec.model_id)
            if self.is_installed(spec.model_id, verify=True) and self.matches_spec(spec):
                callback(self._directory_size(destination), self._directory_size(destination))
                return destination
            if destination.exists():
                raise ModelManagerError(
                    f"Каталог {destination} занят повреждённой или другой версией модели; "
                    "удалите её через настройки перед повторной установкой."
                )
            self.ensure_free_space(spec)
            staging = self.models_directory / f".{spec.model_id}.{uuid.uuid4().hex}.staging"
            archive = self.models_directory / f".{spec.model_id}.{uuid.uuid4().hex}.download"
            try:
                if spec.archive_url is not None:
                    self._download_archive(spec, archive, callback)
                    self._extract_zip(archive, staging)
                    source = self._single_directory_or_self(staging)
                    if source != staging:
                        flattened = (
                            self.models_directory / f".{spec.model_id}.{uuid.uuid4().hex}.flat"
                        )
                        source.replace(flattened)
                        shutil.rmtree(staging)
                        staging = flattened
                elif spec.repository_id is not None:
                    if cancel is None:
                        self._download_snapshot(spec, staging, callback)
                    else:
                        self._download_snapshot_http(spec, staging, callback)
                else:
                    raise ModelManagerError("Для загрузки не указан источник модели.")
                self._check_required_files(staging, spec.required_files)
                self._write_marker(staging, spec)
                callback(self._directory_size(staging), spec.expected_size_bytes)
                staging.replace(destination)
            except Exception as exc:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
                if isinstance(exc, ModelManagerError):
                    raise
                raise ModelManagerError(f"Не удалось установить модель {spec.model_id}.") from exc
            finally:
                archive.unlink(missing_ok=True)
            if progress is not None:
                size = self._directory_size(destination)
                progress(size, size)
            return destination

    def _download_snapshot(
        self, spec: ModelSpec, staging: Path, callback: ProgressCallback
    ) -> None:
        # Xet has historically been able to stall indefinitely on some Windows
        # installations. Force the ordinary HTTP path and bound Hub requests.
        os.environ["HF_HUB_DISABLE_XET"] = "1"
        os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "15")
        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise ModelManagerError("Для загрузки модели установите huggingface-hub.") from exc
        repository_id = spec.repository_id
        if repository_id is None:
            raise ModelManagerError("Для snapshot-загрузки не указан repository_id.")
        callback(0, spec.expected_size_bytes)
        try:
            snapshot_download(
                repo_id=repository_id,
                revision=spec.revision,
                local_dir=staging,
                etag_timeout=15,
            )
        except (OSError, RuntimeError, ValueError):
            # A direct HTTPS fallback avoids Xet and still publishes only through
            # the same verified staging/marker/rename state machine.
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
            try:
                self._download_snapshot_http(spec, staging, callback)
            except ModelManagerError as fallback_exc:
                raise ModelManagerError(
                    f"Сетевая загрузка {spec.repository_id} не завершена."
                ) from fallback_exc
        callback(self._directory_size(staging), spec.expected_size_bytes)

    def _download_snapshot_http(
        self, spec: ModelSpec, staging: Path, callback: ProgressCallback
    ) -> None:
        repository_id = spec.repository_id
        if repository_id is None:
            raise ModelManagerError("Для HTTP-загрузки не указан repository_id.")
        encoded_repository = quote(repository_id, safe="/")
        encoded_revision = quote(spec.revision, safe="")
        metadata_url = (
            f"https://huggingface.co/api/models/{encoded_repository}/revision/{encoded_revision}"
        )
        try:
            with urlopen(
                Request(metadata_url, headers={"User-Agent": "VoxType/1"}), timeout=30
            ) as response:
                metadata = json.loads(response.read().decode("utf-8"))
        except (
            HTTPError,
            URLError,
            OSError,
            TimeoutError,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ModelManagerError("Не удалось получить список файлов модели.") from exc
        siblings = metadata.get("siblings") if isinstance(metadata, dict) else None
        if not isinstance(siblings, list):
            raise ModelManagerError("Hugging Face вернул некорректный список файлов модели.")
        files: list[tuple[str, int | None]] = []
        for item in siblings:
            if not isinstance(item, dict):
                continue
            name = item.get("rfilename")
            if not isinstance(name, str) or not name or name == ".gitattributes":
                continue
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ModelManagerError("Источник модели содержит небезопасный путь.")
            size_value = item.get("size")
            size = int(size_value) if isinstance(size_value, int) and size_value >= 0 else None
            files.append((name, size))
        if not files:
            raise ModelManagerError("Hugging Face не вернул файлы модели.")
        known_total = sum(size for _, size in files if size is not None)
        total = (
            known_total if all(size is not None for _, size in files) else spec.expected_size_bytes
        )
        downloaded = 0
        callback(0, total)
        for name, _size in files:
            encoded_name = quote(name, safe="/")
            url = (
                f"https://huggingface.co/{encoded_repository}/resolve/"
                f"{encoded_revision}/{encoded_name}?download=true"
            )
            destination = staging / Path(name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            request = Request(url, headers={"User-Agent": "VoxType/1"})
            try:
                with urlopen(request, timeout=30) as response, destination.open("xb") as output:
                    while block := response.read(1024 * 1024):
                        output.write(block)
                        downloaded += len(block)
                        callback(downloaded, total)
            except (HTTPError, URLError, OSError, TimeoutError) as exc:
                raise ModelManagerError(f"Загрузка файла модели {name} прервана.") from exc

    @staticmethod
    def _download_archive(spec: ModelSpec, destination: Path, callback: ProgressCallback) -> None:
        request = Request(spec.archive_url or "", headers={"User-Agent": "VoxType/1"})
        digest = hashlib.sha256()
        downloaded = 0
        try:
            with urlopen(request, timeout=30) as response, destination.open("xb") as output:
                header = response.headers.get("Content-Length")
                total = int(header) if header and header.isdigit() else spec.expected_size_bytes
                callback(0, total)
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    digest.update(block)
                    downloaded += len(block)
                    callback(downloaded, total)
        except (OSError, TimeoutError, ValueError) as exc:
            raise ModelManagerError("Загрузка архива модели прервана.") from exc
        if spec.expected_size_bytes is not None and downloaded != spec.expected_size_bytes:
            raise ModelManagerError("Размер загруженного архива не совпадает с ожидаемым.")
        if (
            spec.archive_sha256 is not None
            and digest.hexdigest().lower() != spec.archive_sha256.lower()
        ):
            raise ModelManagerError("Контрольная сумма архива модели не совпадает.")

    @staticmethod
    def _extract_zip(archive: Path, staging: Path) -> None:
        if not zipfile.is_zipfile(archive):
            raise ModelManagerError("Архив модели не является корректным ZIP-файлом.")
        staging.mkdir(parents=True)
        root = staging.resolve()
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                destination = (root / member.filename).resolve()
                if destination != root and root not in destination.parents:
                    raise ModelManagerError("Архив модели содержит небезопасный путь.")
            package.extractall(staging)

    @staticmethod
    def _single_directory_or_self(directory: Path) -> Path:
        children = [item for item in directory.iterdir() if item.name != "__MACOSX"]
        return children[0] if len(children) == 1 and children[0].is_dir() else directory

    def register_local(self, spec: ModelSpec, source: str | Path) -> Path:
        """Atomically copy a user-provided converted model into managed storage."""

        source_path = Path(source).resolve()
        if not source_path.is_dir():
            raise ModelManagerError("Исходный каталог модели не найден.")
        with self._lock:
            destination = self.path_for(spec.model_id)
            if destination.exists():
                if self.is_installed(spec.model_id, verify=True):
                    return destination
                raise ModelManagerError("Целевой каталог уже занят непроверенными данными.")
            staging = self.models_directory / f".{spec.model_id}.{uuid.uuid4().hex}.staging"
            try:
                shutil.copytree(source_path, staging)
                self._check_required_files(staging, spec.required_files)
                self._write_marker(staging, spec)
                staging.replace(destination)
            except Exception as exc:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
                if isinstance(exc, ModelManagerError):
                    raise
                raise ModelManagerError(
                    f"Не удалось зарегистрировать модель {spec.model_id}."
                ) from exc
            return destination

    @staticmethod
    def _check_required_files(directory: Path, required: Iterable[str]) -> None:
        missing = [name for name in required if not (directory / name).is_file()]
        if missing:
            raise ModelManagerError(
                f"В модели отсутствуют обязательные файлы: {', '.join(missing)}"
            )

    def _write_marker(self, directory: Path, spec: ModelSpec) -> None:
        files: dict[str, dict[str, int | str]] = {}
        for path in sorted(
            item for item in directory.rglob("*") if item.is_file() and item.name != _MARKER
        ):
            relative = path.relative_to(directory).as_posix()
            files[relative] = {"size": path.stat().st_size, "sha256": self._sha256(path)}
        marker = {
            "schema": 1,
            "model_id": spec.model_id,
            "repository_id": spec.repository_id,
            "revision": spec.revision,
            "installed_at": datetime.now(UTC).isoformat(),
            "files": files,
        }
        (directory / _MARKER).write_text(
            json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def verify(self, model_id: str) -> bool:
        path = self.path_for(model_id)
        marker_path = path / _MARKER
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            if (
                not isinstance(marker, dict)
                or marker.get("schema") != 1
                or marker.get("model_id") != model_id
            ):
                return False
            files = marker.get("files")
            if not isinstance(files, dict) or not files:
                return False
            for relative, metadata in files.items():
                candidate = (path / relative).resolve()
                if (
                    path not in candidate.parents
                    or not candidate.is_file()
                    or not isinstance(metadata, dict)
                ):
                    return False
                if candidate.stat().st_size != metadata.get("size"):
                    return False
                if self._sha256(candidate) != metadata.get("sha256"):
                    return False
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        return True

    def list_installed(self, *, verify: bool = False) -> tuple[InstalledModel, ...]:
        with self._lock:
            markers = []
            for directory in sorted(self.models_directory.iterdir()):
                if directory.name.startswith("."):
                    continue
                try:
                    marker = (directory / _MARKER).stat()
                except OSError:
                    continue
                markers.append((directory.name, marker.st_mtime_ns, marker.st_size))
            signature = tuple(markers)
            if (
                not verify
                and self._inventory is not None
                and signature == self._inventory_signature
            ):
                return self._inventory
            result = self._scan_installed((item[0] for item in signature), verify=verify)
            if not verify:
                self._inventory_signature, self._inventory = signature, result
            return result

    def _scan_installed(
        self, model_ids: Iterable[str], *, verify: bool
    ) -> tuple[InstalledModel, ...]:
        installed: list[InstalledModel] = []
        for identifier in model_ids:
            directory = self.models_directory / identifier
            marker_path = directory / _MARKER
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
                if not isinstance(marker, dict) or marker.get("model_id") != identifier:
                    continue
                if self.integrity_state(identifier) is not ModelIntegrityState.READY:
                    continue
            except (OSError, ValueError, TypeError, ModelManagerError):
                continue
            valid = self.verify(directory.name) if verify else False
            installed.append(
                InstalledModel(
                    model_id=directory.name,
                    path=directory,
                    repository_id=marker.get("repository_id"),
                    revision=str(marker.get("revision", "unknown")),
                    installed_at=str(marker.get("installed_at", "unknown")),
                    size_bytes=self._directory_size(directory),
                    verified=valid,
                )
            )
        return tuple(installed)

    def remove(self, model_id: str) -> bool:
        with self._lock:
            path = self.path_for(model_id)
            if not path.exists():
                return False
            if not path.is_dir() or path.parent != self.models_directory:
                raise ModelManagerError("Отказано в удалении пути вне каталога моделей.")
            shutil.rmtree(path)
            return True

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _directory_size(path: Path) -> int:
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
