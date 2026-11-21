"""Deterministic, local professional dictation transformations."""

from __future__ import annotations

import csv
import io
import json
import re
import threading
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from voxtype.core.profiles import DictationContext, DictationProfile, normalized_executable
from voxtype.settings.repository import SettingsRepository

_PROTECTED = re.compile(
    r"""(?ix)
    (?:
        ["'](?:[a-z]:[\\/]|\\\\|/|\.\.?/)[^"'\r\n]+["']
        | \b(?:https?://|www\.)\S+
        | \b[\w.+-]+@[\w.-]+\.[a-z]{2,}
        | (?:\b[a-z]:[\\/]|\\\\)\S+
        | (?<!\w)(?:/|\.\.?/)[\w.~-][^\s"'<>]*
    )
    """
)
_PARAMETER = re.compile(r"\{(date|time|input:[A-Za-z][A-Za-z0-9_]{0,39})\}")
VOICE_ACTION_TYPES = (
    "InsertSnippet", "InsertLiteralText", "SwitchProfile", "CopyLastResult",
    "OpenVoxTypePanel", "CancelCurrentSession", "InsertParagraph", "InsertNewline",
)
ACTION_PARAMETERS = {
    "InsertSnippet": "name", "InsertLiteralText": "text", "SwitchProfile": "name",
    "OpenVoxTypePanel": "panel",
}
MAX_HINT_CHARACTERS = 300


class TextExpansionLimit(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DictionaryEntry:
    canonical_text: str
    spoken_variants: tuple[str, ...] = ()
    language: str = ""
    profile_id: str = ""
    match_mode: str = "word"
    case_policy: str = "canonical"
    priority: int = 0
    enabled: bool = True
    executable: str = ""
    vocabulary_id: str = ""
    origin: str = "personal"


@dataclass(frozen=True, slots=True)
class RecognitionRequest:
    session_id: int
    language: str
    profile_id: str
    model_id: str
    context: DictationContext
    hotwords: tuple[str, ...]
    initial_prompt: str | None


@dataclass(frozen=True, slots=True)
class VoiceAction:
    id: str
    trigger: str
    action_type: str
    payload: dict[str, str]
    confirmation_required: bool = False
    enabled: bool = True
    command_set_id: str = ""


@dataclass(frozen=True, slots=True)
class Snippet:
    id: str
    name: str
    trigger_phrase: str
    content: str
    category: str = ""
    profile_id: str = ""
    enabled: bool = True
    snippet_set_id: str = ""

    def input_names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            value[6:] for value in _PARAMETER.findall(self.content) if value.startswith("input:")
        ))

    def render(self, inputs: Mapping[str, str] | None = None) -> str:
        now = datetime.now().astimezone()
        fields = inputs or {}

        def substitute(match: re.Match[str]) -> str:
            key = match[1]
            if key == "date":
                return now.date().isoformat()
            if key == "time":
                return now.strftime("%H:%M %z")
            name = key[6:]
            if name not in fields:
                raise ValueError(f"Заполните поле {name}")
            return fields[name]

        return _PARAMETER.sub(substitute, self.content)


@dataclass(frozen=True, slots=True)
class ReplacementRule:
    id: str
    pattern: str
    replacement: str
    match_type: str = "literal"
    case_sensitive: bool = False
    whole_word: bool = True
    scope: str = ""
    priority: int = 0
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ProcessedDictation:
    session_id: int
    raw_text: str
    normalized_text: str
    final_text: str
    language: str
    profile_id: str
    processing_actions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    voice_action: VoiceAction | None = None


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")


def _outside_protected(text: str, change) -> str:
    parts = []
    index = 0
    for match in _PROTECTED.finditer(text):
        parts.extend((change(text[index:match.start()]), match[0]))
        index = match.end()
    parts.append(change(text[index:]))
    return "".join(parts)


class FormattingPolicy:
    @staticmethod
    def apply(text: str, mode: str) -> str:
        if mode in {"code", "verbatim"}:
            return text

        def normalize_spaces(value: str) -> str:
            value = re.sub(r"[^\S\n]+", " ", value)
            if mode in {"business", "email", "general", "chat"}:
                value = re.sub(r" +([,.;:!?])", r"\1", value)
            return value

        value = _outside_protected(text, normalize_spaces).strip()
        if mode in {"email", "business", "notes"}:
            value = re.sub(r"(?m)^ +| +$", "", value)
            value = re.sub(r"\n{3,}", "\n\n", value)
        return value


def _applies(entry: DictionaryEntry, language: str, profile: DictationProfile, context: DictationContext) -> bool:
    application = normalized_executable(entry.executable)
    return (
        entry.enabled
        and (not entry.language or entry.language == language)
        and (not entry.profile_id or entry.profile_id == profile.id)
        and (not entry.vocabulary_id or entry.vocabulary_id == profile.vocabulary_id)
        and (not application or application in {
            normalized_executable(context.executable_name), normalized_executable(context.process_path or "")
        })
    )


def _matches_scope(scope: str, language: str, profile: DictationProfile, context: DictationContext) -> bool:
    if not scope:
        return True
    if scope.startswith("lang:"):
        return scope[5:] == language
    if scope.startswith("app:"):
        executable = normalized_executable(scope[4:])
        return bool(executable) and executable in {
            normalized_executable(context.executable_name), normalized_executable(context.process_path or ""),
        }
    return scope.removeprefix("profile:") == profile.id


_PROFILE_TERMS = {
    "business": ("договор поставки", "счёт-фактура", "commercial proposal"),
    "developer": ("PostgreSQL", "Python", "PySide6", "CTranslate2", "snake_case", "CamelCase"),
    "legal": ("доверенность", "подсудность", "контрагент", "affidavit", "jurisdiction"),
    "medical": ("анамнез", "аускультация", "электрокардиограмма", "auscultation", "electrocardiogram"),
}


class VocabularyPromptBuilder:
    @staticmethod
    def build(words: Iterable[str], budget: int = 240) -> tuple[str, ...]:
        seen: set[str] = set()
        result = []
        remaining = max(0, min(int(budget), MAX_HINT_CHARACTERS))
        for term in words:
            value = " ".join(unicodedata.normalize("NFC", term).split())
            key = value.casefold()
            cost = len(value) + (2 if result else 0)
            if value and key not in seen and cost <= remaining:
                seen.add(key)
                result.append(value)
                remaining -= cost
        return tuple(result)


def _literal_rules(text: str, rules: Iterable[tuple[str, str, bool, bool, int, bool]]) -> str:
    ordered = sorted(rules, key=lambda rule: (-len(rule[0]), -rule[4]))
    if not ordered:
        return text
    expressions, replacements = [], []
    for pattern, replacement, sensitive, whole, _priority, preserve in ordered:
        literal = re.escape(pattern)
        literal = literal if sensitive else f"(?i:{literal})"
        if whole:
            literal = rf"(?<!\w){literal}(?!\w)"
        expressions.append(f"(?P<r{len(replacements)}>{literal})")
        replacements.append((replacement, preserve))
    regex = re.compile("|".join(expressions))
    extra = 0

    def substitute(match: re.Match[str]) -> str:
        nonlocal extra
        index = match.lastgroup
        if index is None:
            return match[0]
        value, preserve = replacements[int(index[1:])]
        if preserve and match[0].isupper():
            value = value.upper()
        elif preserve and match[0].islower():
            value = value.lower()
        extra += max(0, len(value) - len(match[0]))
        if extra > 500_000:
            raise TextExpansionLimit("Слишком большой результат замены")
        return value

    return _outside_protected(text, lambda value: regex.sub(substitute, value))


def _trigger(text: str) -> str:
    return " ".join(_normalize(text).casefold().strip(" \n\t.,!?").split())


class VoiceCommandRouter:
    @staticmethod
    def route(
        text: str, profile: DictationProfile, commands: tuple[VoiceAction, ...],
        *, activation: str, command_mode: bool, snippets: tuple[Snippet, ...] = (),
    ) -> VoiceAction | None:
        phrase = _trigger(text)
        prefix = _trigger(activation)
        tail = phrase[len(prefix):] if prefix and phrase.startswith(prefix) else ""
        if tail and tail[0] in " ,:;—-":
            phrase = tail.lstrip(" ,:;—-")
        elif not command_mode:
            return None
        fixed = {
            "новый абзац": ("InsertParagraph", {}),
            "новая строка": ("InsertNewline", {}),
            "скопировать результат": ("CopyLastResult", {}),
            "отменить диктовку": ("CancelCurrentSession", {}),
            "открыть словарь": ("OpenVoxTypePanel", {"panel": "dictionary"}),
            "открыть настройки": ("OpenVoxTypePanel", {"panel": "settings"}),
        }
        if profile.formatting_mode == "code":
            for key, symbol in {
                "нижнее подчёркивание": "_", "открывающая скобка": "(",
                "закрывающая скобка": ")", "двоеточие": ":",
            }.items():
                fixed[key] = ("InsertLiteralText", {"text": symbol})
        candidates = [
            item for item in commands if item.enabled and _trigger(item.trigger) == phrase
            and (not item.command_set_id or item.command_set_id == profile.command_set_id)
        ]
        if phrase in fixed:
            action, payload = fixed[phrase]
            candidates.append(VoiceAction("builtin", phrase, action, payload))
        for key, action in (("вставить шаблон ", "InsertSnippet"), ("выбрать профиль ", "SwitchProfile")):
            if phrase.startswith(key):
                candidates.append(VoiceAction("builtin", phrase, action, {"name": phrase[len(key):]}))
        candidates.extend(
            VoiceAction(f"snippet:{item.id}", phrase, "InsertSnippet", {"name": item.id})
            for item in snippets if item.enabled and item.trigger_phrase
            and _trigger(item.trigger_phrase) == phrase
            and (not item.profile_id or item.profile_id == profile.id)
            and (not item.snippet_set_id or item.snippet_set_id == profile.snippet_set_id)
        )
        return candidates[0] if len(candidates) == 1 else None


@dataclass(frozen=True, slots=True)
class TextPipeline:
    dictionary: tuple[DictionaryEntry, ...] = ()
    snippets: tuple[Snippet, ...] = ()
    commands: tuple[VoiceAction, ...] = ()
    replacements: tuple[ReplacementRule, ...] = ()
    activation: str = "вокстайп"
    prompt_budget: int = 240

    def find_snippet(self, name: str, profile: DictationProfile) -> Snippet | None:
        key = _trigger(name)
        if not key:
            return None
        matches = [
            item for item in self.snippets if item.enabled
            and (not item.profile_id or item.profile_id == profile.id)
            and (not item.snippet_set_id or item.snippet_set_id == profile.snippet_set_id)
            and key in {_trigger(item.name), _trigger(item.trigger_phrase), _trigger(item.id)}
        ]
        return matches[0] if len(matches) == 1 else None

    def inline_action(self, action: VoiceAction, profile: DictationProfile) -> str | None:
        if action.confirmation_required:
            return None
        if action.action_type == "InsertParagraph":
            return "\n\n"
        if action.action_type == "InsertNewline":
            return "\n"
        if action.action_type == "InsertLiteralText":
            return action.payload.get("text", "")
        if action.action_type == "InsertSnippet":
            snippet = self.find_snippet(action.payload.get("name", ""), profile)
            if snippet is not None and not snippet.input_names():
                return snippet.render()
        return None

    def process(
        self, request: RecognitionRequest, text: str, profile: DictationProfile,
        *, command_mode: bool = False, allow_commands: bool = True,
    ) -> ProcessedDictation:
        normalized = _outside_protected(text, _normalize)
        actions = ["unicode"] if normalized != text else []
        warnings = []
        try:
            adjusted = _literal_rules(normalized, (
                (spoken, entry.canonical_text, entry.match_mode == "case_sensitive",
                 entry.match_mode != "literal", entry.priority, entry.case_policy == "preserve")
                for entry in self.dictionary if _applies(entry, request.language, profile, request.context)
                for spoken in entry.spoken_variants
            ))
        except TextExpansionLimit:
            adjusted = normalized
            warnings.append("vocabulary_expansion_limit")
        if adjusted != normalized:
            actions.append("vocabulary")
        action = VoiceCommandRouter.route(
            adjusted, profile, self.commands, activation=self.activation,
            command_mode=command_mode, snippets=self.snippets,
        ) if allow_commands else None
        if action is not None and action.action_type == "InsertSnippet":
            if self.find_snippet(action.payload["name"], profile) is None:
                action = None
        if action is not None:
            return ProcessedDictation(
                request.session_id, text, normalized, "", request.language,
                profile.id, (*actions, "voice_command"), tuple(warnings), voice_action=action,
            )
        formatted = FormattingPolicy.apply(adjusted, profile.formatting_mode)
        if formatted != adjusted:
            actions.append("format")
        try:
            final = _literal_rules(formatted, (
                (rule.pattern, rule.replacement, rule.case_sensitive, rule.whole_word, rule.priority, False)
                for rule in self.replacements if rule.enabled and _matches_scope(
                    rule.scope, request.language, profile, request.context,
                )
            ))
        except TextExpansionLimit:
            final = formatted
            warnings.append("replacement_expansion_limit")
        if final != formatted:
            actions.append("replacements")
        return ProcessedDictation(
            request.session_id, text, normalized, final, request.language, profile.id,
            tuple(actions), tuple(warnings),
        )

    def request(
        self, session_id: int, language: str, model_id: str, profile: DictationProfile,
        context: DictationContext, personal: Iterable[str],
    ) -> RecognitionRequest:
        entries = sorted(
            (item for item in self.dictionary if _applies(item, language, profile, context)),
            key=lambda item: -item.priority,
        )
        words = VocabularyPromptBuilder.build(
            (
                *_PROFILE_TERMS.get(profile.vocabulary_id, ()),
                *(item.canonical_text for item in entries if item.origin == "personal" and not item.executable),
                *personal,
                *(item.canonical_text for item in entries if item.origin == "personal" and item.executable),
                *(item.canonical_text for item in entries if item.origin == "organization"),
            ),
            self.prompt_budget,
        )
        return RecognitionRequest(session_id, language, profile.id, model_id, context, words,
                                  ", ".join(words) or None)


class ProfessionalContent:
    TYPES = {
        "dictionary": DictionaryEntry, "snippets": Snippet,
        "commands": VoiceAction, "replacements": ReplacementRule,
    }

    def __init__(self, repository: SettingsRepository, configuration: dict[str, Any]) -> None:
        self._repository = repository
        self._lock = threading.RLock()
        self._configuration = dict(configuration)
        self._collections: dict[str, tuple[Any, ...]] = {}
        for key in self.TYPES:
            try:
                self._collections[key] = self.parse(key, configuration.get(key, []))
            except (ValueError, TypeError):
                self._collections[key] = ()

    def snapshot(self) -> TextPipeline:
        with self._lock:
            budget = self._configuration.get("prompt_budget", 240)
            return TextPipeline(
                **self._collections,
                activation=str(self._configuration.get("command_activation", "вокстайп"))[:64],
                prompt_budget=min(budget, MAX_HINT_CHARACTERS) if type(budget) is int and budget >= 0 else 240,
            )

    def save_options(self, activation: str, budget: int) -> None:
        if len(activation) > 64 or not 0 <= budget <= MAX_HINT_CHARACTERS:
            raise ValueError("Проверьте фразу активации и размер подсказки")
        options = {"command_activation": activation.strip(), "prompt_budget": budget}
        with self._lock:
            self._repository.update({"professional": {"schema_version": 1, **options}})
            self._configuration.update(options)

    @classmethod
    def parse(cls, collection: str, rows: object) -> tuple[Any, ...]:
        if not isinstance(rows, list) or len(rows) > 500:
            raise ValueError("Коллекция должна содержать до 500 записей")
        parsed = []
        seen = set()
        for raw in rows:
            if collection == "dictionary" and isinstance(raw, str):
                raw = {"canonical_text": raw}
            if not isinstance(raw, dict):
                raise ValueError("Каждая запись должна быть объектом JSON")
            row = dict(raw)
            if collection == "dictionary":
                variants = row.get("spoken_variants", ())
                if not isinstance(variants, (list, tuple)) or len(variants) > 8:
                    raise ValueError("Допустимо до восьми вариантов произношения")
                if any(not isinstance(item, str) or not item.strip() or len(item) > 120 for item in variants):
                    raise ValueError("Некорректный вариант произношения")
                row["spoken_variants"] = tuple(variants)
            if any(isinstance(v, str) and len(v) > 20000 for v in row.values()):
                raise ValueError("Слишком длинное поле")
            item = cls.TYPES[collection](**row)
            bool_fields = {"enabled", "confirmation_required", "case_sensitive", "whole_word"}
            for field, value in asdict(item).items():
                if field in bool_fields:
                    valid = type(value) is bool
                elif field == "priority":
                    valid = type(value) is int and -10_000 <= value <= 10_000
                elif field in {"payload", "spoken_variants"}:
                    continue
                else:
                    valid = isinstance(value, str)
                if not valid:
                    raise ValueError(f"Недопустимое поле: {field}")
            if collection == "dictionary":
                if not isinstance(item.canonical_text, str) or not item.canonical_text.strip():
                    raise ValueError("Написание термина обязательно")
                if (item.language not in {"", "ru", "en"}
                        or item.match_mode not in {"word", "case_sensitive", "literal"}
                        or item.case_policy not in {"canonical", "preserve"}
                        or item.origin not in {"personal", "organization"}):
                    raise ValueError("Неверный язык, сопоставление или регистр словаря")
                key = (item.canonical_text.casefold(), item.language, item.profile_id, item.executable, item.vocabulary_id)
            else:
                if not isinstance(item.id, str) or not item.id.strip():
                    raise ValueError("ID обязателен")
                key = item.id
            if collection == "snippets" and (not item.name.strip() or not item.content.strip()):
                raise ValueError("Укажите название и текст шаблона")
            if collection == "commands":
                if item.action_type not in VOICE_ACTION_TYPES or not _trigger(item.trigger):
                    raise ValueError("Неизвестное действие или пустая фраза")
                if not isinstance(item.payload, dict) or any(
                    not isinstance(v, str) or len(v) > 20000 for v in item.payload.values()
                ):
                    raise ValueError("Параметры команды должны быть строками")
                parameter = ACTION_PARAMETERS.get(item.action_type)
                if set(item.payload) != ({parameter} if parameter else set()):
                    raise ValueError("Параметры не соответствуют действию команды")
                if parameter and not item.payload[parameter].strip():
                    raise ValueError("Заполните параметр команды")
                if item.action_type == "OpenVoxTypePanel" and item.payload["panel"] not in {
                    "settings", "dictionary", "snippets", "commands", "replacements",
                }:
                    raise ValueError("Выберите существующую панель VoxType")
                trigger = ("trigger", item.command_set_id, _trigger(item.trigger))
                builtin = VoiceCommandRouter.route(
                    item.trigger, DictationProfile("developer", "Developer", formatting_mode="code"),
                    (), activation="", command_mode=True,
                )
                if item.enabled and (builtin is not None or any(
                    isinstance(entry, tuple) and len(entry) == 3 and entry[0] == "trigger"
                    and entry[2] == trigger[2] and (not entry[1] or not trigger[1] or entry[1] == trigger[1])
                    for entry in seen
                )):
                    raise ValueError("Конфликт голосовых фраз")
                if item.enabled:
                    seen.add(trigger)
            if collection == "replacements" and (
                item.match_type != "literal" or not item.pattern.strip() or len(item.pattern) > 120
            ):
                raise ValueError("Разрешены только буквальные правила длиной до 120 символов")
            if key in seen:
                raise ValueError("Дублирующаяся запись")
            seen.add(key)
            parsed.append(item)
        return tuple(parsed)

    def export_json(self, collection: str) -> str:
        with self._lock:
            rows = [asdict(item) for item in self._collections[collection]]
        return json.dumps({"schema_version": 1, collection: rows}, ensure_ascii=False, indent=2)

    def save(self, collection: str, text: str) -> None:
        if len(text) > 1_000_000:
            raise ValueError("Файл коллекции слишком большой")
        document = json.loads(text)
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("Ожидается схема коллекции версии 1")
        rows = self.parse(collection, document.get(collection))
        with self._lock:
            self._repository.update({"professional": {
                "schema_version": 1, collection: [asdict(item) for item in rows],
            }})
            self._collections[collection] = rows

    @staticmethod
    def dictionary_from_csv(text: str) -> str:
        rows = []
        for row in csv.DictReader(io.StringIO(text)):
            rows.append({
                "canonical_text": row.get("canonical_text", ""),
                "spoken_variants": [word.strip() for word in (row.get("spoken_variants") or "").split("|") if word.strip()],
                "language": row.get("language") or "",
                "profile_id": row.get("profile_id") or "",
                "match_mode": row.get("match_mode") or "word",
                "case_policy": row.get("case_policy") or "canonical",
                "priority": int(row.get("priority") or 0),
                "enabled": (row.get("enabled") or "true").casefold() not in {"false", "0"},
                "executable": row.get("executable") or "",
                "vocabulary_id": row.get("vocabulary_id") or "",
                "origin": row.get("origin") or "personal",
            })
        return json.dumps({"schema_version": 1, "dictionary": rows}, ensure_ascii=False, indent=2)

    def dictionary_to_csv(self) -> str:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=list(DictionaryEntry.__dataclass_fields__))
        writer.writeheader()
        for item in self.snapshot().dictionary:
            writer.writerow({**asdict(item), "spoken_variants": "|".join(item.spoken_variants)})
        return output.getvalue()
