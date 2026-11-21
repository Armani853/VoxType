"""Conservative, opt-in dictionary suggestions from one explicit text edit."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher

from voxtype.settings.repository import SettingsRepository
from voxtype.transcript.professional import DictionaryEntry, ProfessionalContent


@dataclass(frozen=True, slots=True)
class CorrectionSuggestion:
    spoken: str
    canonical: str
    language: str

    @property
    def key(self) -> str:
        pair = "\0".join((self.language, self.spoken.casefold(), self.canonical))
        return hashlib.sha256(pair.encode("utf-8")).hexdigest()


class CorrectionSuggestionService:
    def __init__(self, repository: SettingsRepository, vocabulary: ProfessionalContent) -> None:
        self.repository = repository
        self.vocabulary = vocabulary

    def suggest(self, raw: str, edited: str, language: str) -> CorrectionSuggestion | None:
        if raw == edited or max(len(raw), len(edited)) > 16_000:
            return None
        old = raw.split()
        new = edited.split()
        if max(len(old), len(new)) > 500:
            return None
        differences = [
            (tag, a, b, c, d) for tag, a, b, c, d in
            SequenceMatcher(None, old, new, autojunk=False).get_opcodes() if tag != "equal"
        ]
        if len(differences) != 1:
            return None
        tag, a, b, c, d = differences[0]
        if tag != "replace" or not (1 <= b-a <= 3 and 1 <= d-c <= 3):
            return None
        before, after = " ".join(old[a:b]), " ".join(new[c:d])
        pattern = r"[^\W\d_]+(?:[-'][^\W\d_]+)*(?: [^\W\d_]+(?:[-'][^\W\d_]+)*){0,2}"
        if not re.fullmatch(pattern, before) or not re.fullmatch(pattern, after):
            return None
        words = {word.casefold() for word in (*old[a:b], *new[c:d])}
        if words & {"not", "no", "never", "не", "нет", "никогда", "без"}:
            return None
        if SequenceMatcher(None, before.casefold(), after.casefold(), autojunk=False).ratio() < 0.55:
            return None
        suggestion = CorrectionSuggestion(before, after, language)
        ignored = self.repository.load().professional.get("ignored_corrections", [])
        return None if suggestion.key in ignored else suggestion

    def ignore_permanently(self, suggestion: CorrectionSuggestion) -> None:
        with self.repository._lock:
            ignored = self.repository.load().professional.get("ignored_corrections", [])
            keys = list(dict.fromkeys([*ignored, suggestion.key]))[-500:]
            self.repository.update({"professional": {"ignored_corrections": keys}})

    def save(self, suggestion: CorrectionSuggestion) -> None:
        entry = DictionaryEntry(
            canonical_text=suggestion.canonical, spoken_variants=(suggestion.spoken,), language=suggestion.language,
        )
        with self.vocabulary._lock:
            document = json.loads(self.vocabulary.export_json("dictionary"))
            rows = document["dictionary"]
            serialized = json.loads(json.dumps(asdict(entry), ensure_ascii=False))
            if serialized in rows:
                return
            rows.append(serialized)
            self.vocabulary.save("dictionary", json.dumps(document, ensure_ascii=False))
