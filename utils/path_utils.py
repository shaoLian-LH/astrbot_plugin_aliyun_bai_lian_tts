"""Path helpers for uploaded audio files."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_uploaded_file(raw_path: str, plugin_id: str, data_dir: Path | None = None) -> Path | None:
    """Resolve file path returned by AstrBot file config field."""

    if not raw_path:
        return None

    candidate_text = str(raw_path).strip()
    lowered = candidate_text.lower()
    if lowered.startswith("http://") or lowered.startswith("https://") or lowered.startswith("data:"):
        return None

    raw = Path(candidate_text)
    candidates: list[Path] = []

    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(raw)
        if data_dir is not None:
            candidates.append(data_dir / raw)

    if data_dir is not None:
        plugin_root = Path(data_dir)
        candidates.append(plugin_root / raw.name)
        candidates.append(plugin_root / "voice_uploads" / raw.name)

    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        data_root = Path(get_astrbot_data_path())
        candidates.append(data_root / "plugin_data" / plugin_id / candidate_text)
        candidates.append(data_root / "plugin_data" / plugin_id / raw.name)
        candidates.append(data_root / candidate_text)
        candidates.append(data_root.parent / candidate_text)
    except Exception:
        pass

    candidates.append(Path(os.getcwd()) / candidate_text)

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None
