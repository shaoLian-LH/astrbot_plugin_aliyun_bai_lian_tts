"""Domain models used by the plugin."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class VoiceProfile:
    """One extracted voice profile bound to an uploaded audio file."""

    name: str
    source_file: str
    source_path: str
    source_hash: str
    voice_id: str
    model_id: str
    created_at: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VoiceProfile":
        return cls(
            name=str(data.get("name", "")),
            source_file=str(data.get("source_file", "")),
            source_path=str(data.get("source_path", "")),
            source_hash=str(data.get("source_hash", "")),
            voice_id=str(data.get("voice_id", "")),
            model_id=str(data.get("model_id", "")),
            created_at=str(data.get("created_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_file": self.source_file,
            "source_path": self.source_path,
            "source_hash": self.source_hash,
            "voice_id": self.voice_id,
            "model_id": self.model_id,
            "created_at": self.created_at,
        }
