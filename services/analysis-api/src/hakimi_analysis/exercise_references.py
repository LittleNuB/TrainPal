"""Small text-only reference catalog. No networking, prescriptions or source claims."""

import re
import unicodedata
from importlib.resources import files
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReferenceText = Annotated[str, Field(min_length=1, max_length=160)]


def _name_key(name: str) -> str:
    return re.sub(r"[\s_-]+", " ", unicodedata.normalize("NFKC", name).casefold()).strip()


class ExerciseReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(pattern=r"^\d{4}$")
    source_name: str = Field(min_length=1, max_length=120)
    equipment: str = Field(min_length=1, max_length=60)
    source_instructions_en: str = Field(min_length=1, max_length=2000)
    name_zh: str = Field(min_length=1, max_length=120)
    aliases: tuple[ReferenceText, ...] = Field(max_length=12)
    discriminators: tuple[ReferenceText, ...] = Field(max_length=8)
    review_status: Literal["agent_reviewed_not_expert"]
    reference_only: Literal[True]


class ExerciseReferenceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["exercise-reference.v1"]
    source_repo: Literal["https://github.com/hasaneyldrm/exercises-dataset"]
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    records: tuple[ExerciseReference, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_source_ids(self) -> "ExerciseReferenceCatalog":
        if len({record.source_id for record in self.records}) != len(self.records):
            raise ValueError("reference source IDs must be unique")
        return self

    @classmethod
    def bundled(cls) -> "ExerciseReferenceCatalog":
        resource = files("hakimi_analysis").joinpath("data/exercise_reference_seed.json")
        return cls.model_validate_json(resource.read_text(encoding="utf-8"))

    def lookup(self, name: str, *, equipment: str | None = None) -> tuple[ExerciseReference, ...]:
        """Exact reviewed aliases only, sorted by source ID (not a confidence rank)."""
        if not name.strip() or len(name) > 160:
            return ()
        key = _name_key(name)
        if not key:
            return ()
        matches = tuple(
            sorted(
                (
                    record
                    for record in self.records
                    if (equipment is None or _name_key(equipment) == _name_key(record.equipment))
                    and key
                    in map(
                        _name_key,
                        (
                            record.source_name,
                            record.name_zh,
                            *record.aliases,
                        ),
                    )
                ),
                key=lambda record: record.source_id,
            )
        )
        return matches if len(matches) <= 4 else ()
