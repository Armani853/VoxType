"""Conservative, opt-in dictionary suggestions from one explicit text edit."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from unicodedata import category, normalize

from voxtype.settings.repository import SettingsRepository
from voxtype.transcript.professional import DictionaryEntry, ProfessionalContent


@dataclass(frozen=True, slots=True)
class CorrectionSuggestion:
    spoken: str
    canonical: str
    language: str
    profile_id: str = ""

    @property
    def key(self) -> str:
        fields = (self.language, self.spoken.casefold(), self.canonical)
        pair = "\0".join((*fields, self.profile_id) if self.profile_id else fields)
        return hashlib.sha256(pair.encode("utf-8")).hexdigest()


class CorrectionSuggestionService:
    def __init__(self, repository: SettingsRepository, vocabulary: ProfessionalContent) -> None:
        self.repository = repository
        self.vocabulary = vocabulary

    def suggest(
        self, raw: str, edited: str, language: str, profile_id: str = "",
    ) -> CorrectionSuggestion | None:
        if max(len(raw), len(edited)) > 16_000:
            return None
        raw, edited = normalize("NFC", raw), normalize("NFC", edited)
        if raw == edited:
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
        # Boundary punctuation shared by both selections is context, not part
        # of the spoken form that would be stored in the dictionary.
        while before and after and before[0] == after[0] and category(before[0]).startswith("P"):
            before, after = before[1:], after[1:]
        while before and after and before[-1] == after[-1] and category(before[-1]).startswith("P"):
            before, after = before[:-1], after[:-1]
        if before == after:
            return None
        pattern = r"[^\W\d_]+(?:[-'][^\W\d_]+)*(?: [^\W\d_]+(?:[-'][^\W\d_]+)*){0,2}"
        if not re.fullmatch(pattern, before) or not re.fullmatch(pattern, after):
            return None
        words = {word.casefold() for word in (*before.split(), *after.split())}
        if words & {"not", "no", "never", "не", "нет", "никогда", "без"}:
            return None
        if SequenceMatcher(None, before.casefold(), after.casefold(), autojunk=False).ratio() < 0.55:
            return None
        suggestion = CorrectionSuggestion(before, after, language, profile_id)
        ignored = self.repository.load().professional.get("ignored_corrections", [])
        return None if suggestion.key in ignored else suggestion

    def ignore_permanently(self, suggestion: CorrectionSuggestion) -> None:
        with self.repository._lock:
            ignored = self.repository.load().professional.get("ignored_corrections", [])
            keys = list(dict.fromkeys([*ignored, suggestion.key]))[-500:]
            self.repository.update({"professional": {"ignored_corrections": keys}})

    def save(self, suggestion: CorrectionSuggestion) -> None:
        entry = DictionaryEntry(
            canonical_text=suggestion.canonical, spoken_variants=(suggestion.spoken,),
            language=suggestion.language, profile_id=suggestion.profile_id,
        )
        with self.vocabulary._lock:
            document = json.loads(self.vocabulary.export_json("dictionary"))
            rows = document["dictionary"]
            serialized = json.loads(json.dumps(asdict(entry), ensure_ascii=False))
            for row in rows:
                if (
                    row["canonical_text"].casefold() == entry.canonical_text.casefold()
                    and row["language"] == entry.language and row["profile_id"] == entry.profile_id
                    and not row["executable"] and not row["vocabulary_id"]
                ):
                    variants = row["spoken_variants"]
                    if any(value.casefold() == suggestion.spoken.casefold() for value in variants):
                        return
                    if len(variants) >= 8:
                        raise ValueError("У термина уже восемь вариантов произношения")
                    variants.append(suggestion.spoken)
                    break
            else:
                rows.append(serialized)
            self.vocabulary.save("dictionary", json.dumps(document, ensure_ascii=False))
