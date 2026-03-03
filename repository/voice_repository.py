"""Persistent storage for voice profiles and synthesized audio cache."""

from __future__ import annotations

import json
import time
from pathlib import Path

try:
    from ..models import VoiceProfile
except ImportError:
    from models import VoiceProfile  # type: ignore


class VoiceRepository:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.profile_file = self.data_dir / "voice_profiles.json"
        self.creation_job_file = self.data_dir / "voice_creation_jobs.json"
        self.cache_dir = self.data_dir / "audio_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def load_profiles(self) -> dict[str, VoiceProfile]:
        if not self.profile_file.exists():
            return {}

        try:
            raw = json.loads(self.profile_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

        profiles: dict[str, VoiceProfile] = {}
        voices = raw.get("voices", {}) if isinstance(raw, dict) else {}
        if not isinstance(voices, dict):
            return {}

        for name, item in voices.items():
            if isinstance(item, dict):
                profile = VoiceProfile.from_dict(item)
                profile.name = name
                if profile.voice_id:
                    profiles[name] = profile
        return profiles

    def save_profiles(self, profiles: dict[str, VoiceProfile]) -> None:
        data = {
            "updated_at": int(time.time()),
            "voices": {name: profile.to_dict() for name, profile in profiles.items()},
        }
        self.profile_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_creation_jobs(self) -> dict[str, dict]:
        if not self.creation_job_file.exists():
            return {}

        try:
            raw = json.loads(self.creation_job_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

        jobs = raw.get("jobs", {}) if isinstance(raw, dict) else {}
        if not isinstance(jobs, dict):
            return {}

        result: dict[str, dict] = {}
        for source_name, item in jobs.items():
            if isinstance(item, dict):
                result[str(source_name)] = dict(item)
        return result

    def save_creation_jobs(self, jobs: dict[str, dict]) -> None:
        data = {
            "updated_at": int(time.time()),
            "jobs": jobs,
        }
        self.creation_job_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_cached_audio(self, cache_key: str, ttl_seconds: int) -> Path | None:
        path = self.cache_dir / f"{cache_key}.wav"
        if not path.exists():
            return None

        if ttl_seconds > 0:
            age = time.time() - path.stat().st_mtime
            if age > ttl_seconds:
                try:
                    path.unlink()
                except OSError:
                    pass
                return None

        return path

    def build_cached_audio_path(self, cache_key: str) -> Path:
        return self.cache_dir / f"{cache_key}.wav"

    def cleanup_cache(self, max_items: int, ttl_seconds: int) -> None:
        files = list(self.cache_dir.glob("*.wav"))
        if not files:
            return

        now = time.time()
        valid_files: list[Path] = []

        for file_path in files:
            if ttl_seconds > 0 and (now - file_path.stat().st_mtime) > ttl_seconds:
                try:
                    file_path.unlink()
                except OSError:
                    pass
                continue
            valid_files.append(file_path)

        if max_items <= 0:
            return

        valid_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in valid_files[max_items:]:
            try:
                stale.unlink()
            except OSError:
                pass
