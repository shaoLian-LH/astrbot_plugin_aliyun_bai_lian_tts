"""High-level voice synchronization, admin management, and TTS workflow."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import mimetypes
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from astrbot.api import logger

try:
    from ..constants import (
        COSY_VOICE_ENROLLMENT_MODEL,
        DEFAULT_CACHE_TTL_SECONDS,
        DEFAULT_MAX_CACHE_ITEMS,
        DEFAULT_MODEL_ID,
        DEFAULT_REGION,
        PLUGIN_ID,
    )
    from ..models import VoiceProfile
    from ..repository.voice_repository import VoiceRepository
    from ..utils.path_utils import md5_file, resolve_uploaded_file
    from .aliyun_tts_client import AliyunTTSClient
except ImportError:
    from constants import (  # type: ignore
        COSY_VOICE_ENROLLMENT_MODEL,
        DEFAULT_CACHE_TTL_SECONDS,
        DEFAULT_MAX_CACHE_ITEMS,
        DEFAULT_MODEL_ID,
        DEFAULT_REGION,
        PLUGIN_ID,
    )
    from models import VoiceProfile  # type: ignore
    from repository.voice_repository import VoiceRepository  # type: ignore
    from utils.path_utils import md5_file, resolve_uploaded_file  # type: ignore
    from services.aliyun_tts_client import AliyunTTSClient  # type: ignore

_PENDING_STATUSES = {"DEPLOYING", "UNKNOWN"}


class VoiceService:
    def __init__(self, config, data_dir: Path):
        self.config = config
        self.repository = VoiceRepository(data_dir)
        self._upload_dir = self.repository.data_dir / "voice_uploads"
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        self._profiles = self.repository.load_profiles()
        self._creation_jobs = self.repository.load_creation_jobs()

        self._sync_lock = asyncio.Lock()
        self._last_signature = ""
        self._resolved_sources: dict[str, dict[str, str]] = {}

    async def sync_voice_profiles(self, force: bool = False) -> dict[str, VoiceProfile]:
        model_id = self._get_str("model_id", DEFAULT_MODEL_ID)
        file_list = self._get_file_list("voice_files")

        async with self._sync_lock:
            resolved: list[tuple[str, Path, str, str]] = []
            unresolved: list[str] = []
            used_names: set[str] = set()
            for raw_path in file_list:
                resolved_path = resolve_uploaded_file(
                    raw_path=raw_path,
                    plugin_id=PLUGIN_ID,
                    data_dir=self.repository.data_dir,
                )
                if not resolved_path:
                    logger.warning(f"[AliyunBailianTTS] 上传音频不存在，已跳过: {raw_path}")
                    unresolved.append(raw_path)
                    continue

                source_hash = md5_file(resolved_path)
                base_name = Path(raw_path).stem or resolved_path.stem
                voice_name = self._unique_voice_name(base_name, used_names)
                used_names.add(voice_name)

                try:
                    persisted_path = self._persist_uploaded_file(
                        source_path=resolved_path,
                        source_name=voice_name,
                        source_hash=source_hash,
                    )
                except Exception as exc:
                    logger.warning(f"[AliyunBailianTTS] 上传音频归档失败，已跳过: {raw_path}, {exc}")
                    unresolved.append(raw_path)
                    continue

                resolved.append((raw_path, persisted_path, voice_name, source_hash))

            signature = self._build_signature(resolved=resolved, unresolved=unresolved, model_id=model_id)
            if not force and signature == self._last_signature:
                return self._profiles

            self._resolved_sources = {
                voice_name: {
                    "source_path": str(source_path),
                    "source_file": source_path.name,
                    "source_hash": source_hash,
                }
                for _, source_path, voice_name, source_hash in resolved
            }
            self._cleanup_stored_uploads(
                {Path(source["source_path"]) for source in self._resolved_sources.values()}
            )
            self._last_signature = signature

            self._sync_creation_jobs_for_sources()
            self._ensure_default_active_voice_id()

            ttl = self._get_int("cache_ttl_seconds", DEFAULT_CACHE_TTL_SECONDS, minimum=0)
            max_items = self._get_int("max_cache_items", DEFAULT_MAX_CACHE_ITEMS, minimum=1)
            self.repository.cleanup_cache(max_items=max_items, ttl_seconds=ttl)
            return self._profiles

    def get_source_names(self) -> list[str]:
        return sorted(self._resolved_sources.keys())

    async def create_voice_by_source_name(self, source_name: str) -> dict[str, str]:
        await self.sync_voice_profiles(force=False)

        source_key = self._match_source_name(source_name)
        if not source_key:
            available = ", ".join(self.get_source_names()) or "无"
            raise RuntimeError(f"未找到音声文件: {source_name}，可选: {available}")

        source = self._resolved_sources[source_key]
        source_path = Path(source["source_path"])
        if not source_path.exists():
            raise RuntimeError(f"音声文件不存在: {source_path}")

        target_model = self._get_str("voice_target_model", self._get_str("model_id", DEFAULT_MODEL_ID))
        enrollment_model = self._get_str("voice_enrollment_model", COSY_VOICE_ENROLLMENT_MODEL)
        prefix = self._build_voice_prefix(source_key)
        language_hints = self._get_str("voice_language_hints", "")
        source_url = self._build_source_url(source_path=source_path)
        client = self._build_client()

        try:
            voice_id = await asyncio.to_thread(
                client.create_voice,
                target_model,
                prefix,
                source_url,
                enrollment_model,
                language_hints,
            )
            query = await asyncio.to_thread(client.query_voice, voice_id, enrollment_model)
            status = str(query.get("status") or "UNKNOWN").strip().upper()
            message = ""
        except Exception as exc:
            self._update_creation_job(
                source_name=source_key,
                source=source,
                target_model=target_model,
                voice_id="",
                status="FAILED",
                message=str(exc),
            )
            raise

        profile = VoiceProfile(
            name=source_key,
            source_file=source["source_file"],
            source_path=source["source_path"],
            source_hash=source["source_hash"],
            voice_id=voice_id,
            model_id=target_model,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._profiles[source_key] = profile
        self.repository.save_profiles(self._profiles)

        if not self.get_active_voice_id():
            self._set_config_value("active_voice_id", voice_id)

        self._update_creation_job(
            source_name=source_key,
            source=source,
            target_model=target_model,
            voice_id=voice_id,
            status=status,
            message=message,
        )

        return {
            "source_name": source_key,
            "voice_id": voice_id,
            "status": status,
            "target_model": target_model,
        }

    async def get_creation_status(self, refresh_remote: bool = True) -> list[dict[str, str]]:
        await self.sync_voice_profiles(force=False)

        if refresh_remote and self._creation_jobs:
            enrollment_model = self._get_str("voice_enrollment_model", COSY_VOICE_ENROLLMENT_MODEL)
            client = self._build_client()
            changed = False
            for source_name, job in list(self._creation_jobs.items()):
                voice_id = str(job.get("voice_id", "")).strip()
                status = str(job.get("status", "")).strip().upper()
                if not voice_id or status in {"FAILED", "NOT_CREATED"}:
                    continue

                try:
                    query = await asyncio.to_thread(client.query_voice, voice_id, enrollment_model)
                except Exception as exc:
                    if status in _PENDING_STATUSES:
                        job["status"] = "UNKNOWN"
                        job["message"] = str(exc)
                        job["updated_at"] = datetime.now(timezone.utc).isoformat()
                        self._creation_jobs[source_name] = job
                        changed = True
                    continue

                remote_status = str(query.get("status") or "UNKNOWN").strip().upper()
                if remote_status != status:
                    job["status"] = remote_status
                    job["updated_at"] = datetime.now(timezone.utc).isoformat()
                    job["message"] = ""
                    self._creation_jobs[source_name] = job
                    changed = True

            if changed:
                self.repository.save_creation_jobs(self._creation_jobs)

        jobs = []
        for source_name, job in self._creation_jobs.items():
            item = dict(job)
            item["source_name"] = source_name
            jobs.append(item)

        jobs.sort(key=lambda x: str(x.get("updated_at", "")), reverse=True)
        return jobs

    async def list_remote_voices(self, ensure_synced: bool = True) -> list[dict[str, str]]:
        if ensure_synced:
            await self.sync_voice_profiles(force=False)

        local_map = self.get_local_voice_name_map()
        client = self._build_client()
        enrollment_model = self._get_str("voice_enrollment_model", COSY_VOICE_ENROLLMENT_MODEL)
        prefix_filter = self._get_str("voice_prefix_filter", "").strip()

        try:
            voices = await asyncio.to_thread(client.list_voices, enrollment_model, prefix_filter, 1, 100)
        except Exception as exc:
            if not local_map:
                raise
            logger.warning(f"[AliyunBailianTTS] 查询远端音色失败，回退本地缓存: {exc}")
            voices = []

        by_id: dict[str, dict[str, str]] = {}
        for item in voices:
            voice_id = str(item.get("voice_id", "")).strip()
            if not voice_id:
                continue
            current = {
                "voice_id": voice_id,
                "status": str(item.get("status") or "").strip(),
                "gmt_create": str(item.get("gmt_create") or "").strip(),
                "provider": str(item.get("provider") or "").strip(),
            }
            names = local_map.get(voice_id)
            if names:
                current["local_names"] = ", ".join(names)
            by_id[voice_id] = current

        for voice_id, names in local_map.items():
            if voice_id in by_id:
                continue
            by_id[voice_id] = {
                "voice_id": voice_id,
                "status": "UNKNOWN",
                "gmt_create": "",
                "provider": "local-cache",
                "local_names": ", ".join(names),
            }

        return sorted(
            by_id.values(),
            key=lambda x: (x.get("gmt_create", ""), x.get("voice_id", "")),
            reverse=True,
        )

    async def set_active_voice_id(self, voice_id: str) -> None:
        target = str(voice_id or "").strip()
        if not target:
            raise RuntimeError("音色ID 不能为空")

        voices = await self.list_remote_voices(ensure_synced=False)
        voice_ids = {item.get("voice_id", "") for item in voices}
        if voice_ids and target not in voice_ids:
            raise RuntimeError(f"未找到音色ID: {target}")
        if not voice_ids and target not in self.get_local_voice_name_map():
            raise RuntimeError(f"未找到音色ID: {target}")

        self._set_config_value("active_voice_id", target)

    async def delete_voice_id(self, voice_id: str) -> tuple[str, int]:
        target = str(voice_id or "").strip()
        if not target:
            raise RuntimeError("音色ID 不能为空")

        enrollment_model = self._get_str("voice_enrollment_model", COSY_VOICE_ENROLLMENT_MODEL)
        client = self._build_client()
        await asyncio.to_thread(client.delete_voice, target, enrollment_model)

        removed_count = self._remove_local_profiles_by_voice_id(target)
        self._mark_jobs_deleted(target)

        active_voice_id = self.get_active_voice_id()
        if active_voice_id == target:
            fallback = self._first_local_voice_id(exclude=target)
            self._set_config_value("active_voice_id", fallback)

        return "cosy", removed_count

    async def synthesize_text(self, text: str) -> Path:
        await self.sync_voice_profiles(force=False)

        active_voice_id = self.get_active_voice_id()
        if not active_voice_id:
            raise RuntimeError("暂无可用音色，请先创建音色或设置 active_voice_id。")

        model_id = self._get_str("model_id", DEFAULT_MODEL_ID)
        cache_key = self._build_cache_key(model_id=model_id, voice_id=active_voice_id, text=text)

        ttl = self._get_int("cache_ttl_seconds", DEFAULT_CACHE_TTL_SECONDS, minimum=0)
        cached_audio = self.repository.get_cached_audio(cache_key=cache_key, ttl_seconds=ttl)
        if cached_audio:
            return cached_audio

        output_path = self.repository.build_cached_audio_path(cache_key)
        client = self._build_client()
        await client.synthesize(
            text=text,
            model_id=model_id,
            voice_id=active_voice_id,
            output_path=output_path,
        )

        max_items = self._get_int("max_cache_items", DEFAULT_MAX_CACHE_ITEMS, minimum=1)
        self.repository.cleanup_cache(max_items=max_items, ttl_seconds=ttl)
        return output_path

    def get_active_voice_id(self) -> str:
        configured = self._get_str("active_voice_id", "").strip()
        if configured:
            return configured
        return self._first_local_voice_id()

    def get_local_voice_name_map(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for name, profile in self._profiles.items():
            voice_id = str(profile.voice_id or "").strip()
            if not voice_id:
                continue
            result.setdefault(voice_id, []).append(name)

        for names in result.values():
            names.sort()
        return result

    def get_admin_ids(self) -> list[str]:
        if self.config is None:
            return []

        raw_value = self.config.get("admin_user_ids", [])
        values: list[str] = []

        if isinstance(raw_value, str):
            values.extend(raw_value.replace("\n", ",").split(","))
        elif isinstance(raw_value, list):
            values.extend([str(item) for item in raw_value])

        normalized: list[str] = []
        seen: set[str] = set()
        for item in values:
            candidate = str(item or "").strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    def is_admin(self, sender_id: str) -> bool:
        candidate = str(sender_id or "").strip()
        if not candidate:
            return False
        return candidate in set(self.get_admin_ids())

    def _sync_creation_jobs_for_sources(self) -> None:
        changed = False
        target_model = self._get_str("voice_target_model", self._get_str("model_id", DEFAULT_MODEL_ID))
        now = datetime.now(timezone.utc).isoformat()

        for source_name, source in self._resolved_sources.items():
            profile = self._profiles.get(source_name)
            existing = dict(self._creation_jobs.get(source_name, {}))
            status = str(existing.get("status", "NOT_CREATED")).strip().upper()
            voice_id = str(existing.get("voice_id", "")).strip()

            if profile and profile.voice_id:
                voice_id = profile.voice_id
                if status in {"NOT_CREATED", "FAILED"}:
                    status = "UNKNOWN"

            merged = {
                "source_file": source["source_file"],
                "source_path": source["source_path"],
                "source_hash": source["source_hash"],
                "target_model": str(existing.get("target_model") or target_model),
                "voice_id": voice_id,
                "status": status or "NOT_CREATED",
                "message": str(existing.get("message", "")),
                "updated_at": str(existing.get("updated_at") or now),
            }

            if merged != existing:
                self._creation_jobs[source_name] = merged
                changed = True

        if changed:
            self.repository.save_creation_jobs(self._creation_jobs)

    def _update_creation_job(
        self,
        source_name: str,
        source: dict[str, str],
        target_model: str,
        voice_id: str,
        status: str,
        message: str,
    ) -> None:
        current = dict(self._creation_jobs.get(source_name, {}))
        current.update(
            {
                "source_file": source.get("source_file", ""),
                "source_path": source.get("source_path", ""),
                "source_hash": source.get("source_hash", ""),
                "target_model": target_model,
                "voice_id": voice_id,
                "status": status,
                "message": message,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._creation_jobs[source_name] = current
        self.repository.save_creation_jobs(self._creation_jobs)

    def _mark_jobs_deleted(self, voice_id: str) -> None:
        changed = False
        now = datetime.now(timezone.utc).isoformat()
        for source_name, job in self._creation_jobs.items():
            if str(job.get("voice_id", "")).strip() != voice_id:
                continue
            job["status"] = "DELETED"
            job["updated_at"] = now
            job["message"] = ""
            changed = True
        if changed:
            self.repository.save_creation_jobs(self._creation_jobs)

    def _ensure_default_active_voice_id(self) -> None:
        current = self._get_str("active_voice_id", "").strip()
        if current:
            return

        fallback = self._first_local_voice_id()
        if fallback:
            self._set_config_value("active_voice_id", fallback)

    def _build_client(self) -> AliyunTTSClient:
        api_key = self._get_str("api_key", "").strip()
        if not api_key:
            raise RuntimeError("请先在配置中填写 api_key")

        region = self._get_str("api_region", DEFAULT_REGION)
        return AliyunTTSClient(api_key=api_key, region=region)

    def _match_source_name(self, source_name: str) -> str:
        target = str(source_name or "").strip()
        if not target:
            return ""
        if target in self._resolved_sources:
            return target

        lower = target.lower()
        for name in self._resolved_sources:
            if name.lower() == lower:
                return name
        return ""

    def _build_source_url(self, source_path: Path) -> str:
        return self._file_to_data_url(source_path)

    def _file_to_data_url(self, source_path: Path) -> str:
        mime, _ = mimetypes.guess_type(source_path.name)
        if not mime:
            mime = "audio/mpeg"
        encoded = base64.b64encode(source_path.read_bytes()).decode("utf-8")
        return f"data:{mime};base64,{encoded}"

    def _build_voice_prefix(self, source_name: str) -> str:
        lowered = source_name.lower()
        normalized = re.sub(r"[^a-z0-9]", "", lowered)
        if not normalized:
            normalized = f"v{hashlib.md5(source_name.encode('utf-8')).hexdigest()[:8]}"
        return normalized[:10]

    def _persist_uploaded_file(self, source_path: Path, source_name: str, source_hash: str) -> Path:
        source = source_path.resolve()
        extension = source.suffix.lower()
        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", source_name).strip("._") or "voice"
        target = self._upload_dir / f"{safe_name}_{source_hash[:10]}{extension}"

        if source == target.resolve():
            return target

        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        return target.resolve()

    def _cleanup_stored_uploads(self, keep_files: set[Path]) -> None:
        keep = {item.resolve() for item in keep_files}
        for candidate in self._upload_dir.glob("*"):
            if not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve()
            except Exception:
                continue
            if resolved in keep:
                continue
            try:
                candidate.unlink()
            except OSError:
                continue

    def _remove_local_profiles_by_voice_id(self, voice_id: str) -> int:
        target = str(voice_id or "").strip()
        if not target:
            return 0

        remaining: dict[str, VoiceProfile] = {}
        removed_count = 0
        for name, profile in self._profiles.items():
            if str(profile.voice_id or "").strip() == target:
                removed_count += 1
                continue
            remaining[name] = profile

        if removed_count:
            self._profiles = remaining
            self.repository.save_profiles(self._profiles)

        return removed_count

    def _first_local_voice_id(self, exclude: str = "") -> str:
        skip = str(exclude or "").strip()
        for profile in self._profiles.values():
            voice_id = str(profile.voice_id or "").strip()
            if not voice_id or voice_id == skip:
                continue
            return voice_id
        return ""

    def _build_signature(
        self,
        resolved: list[tuple[str, Path, str, str]],
        unresolved: list[str],
        model_id: str,
    ) -> str:
        digest = hashlib.md5()
        digest.update(model_id.encode("utf-8"))
        for raw_path, source_path, voice_name, source_hash in sorted(resolved, key=lambda x: x[0]):
            try:
                stat = source_path.stat()
                size = stat.st_size
                mtime_ns = stat.st_mtime_ns
            except OSError:
                size = -1
                mtime_ns = -1
            digest.update(raw_path.encode("utf-8"))
            digest.update(str(source_path).encode("utf-8"))
            digest.update(str(size).encode("utf-8"))
            digest.update(str(mtime_ns).encode("utf-8"))
            digest.update(voice_name.encode("utf-8"))
            digest.update(source_hash.encode("utf-8"))
            digest.update(b"\x00")
        for raw_path in sorted(unresolved):
            digest.update(raw_path.encode("utf-8"))
            digest.update(b"\x01")
        return digest.hexdigest()

    def _build_cache_key(self, model_id: str, voice_id: str, text: str) -> str:
        digest = hashlib.md5()
        digest.update(model_id.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(voice_id.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(text.encode("utf-8"))
        return digest.hexdigest()

    def _unique_voice_name(self, base_name: str, used_names: set[str]) -> str:
        base = base_name.strip() or "voice"
        if base not in used_names:
            return base

        index = 2
        while True:
            candidate = f"{base}_{index}"
            if candidate not in used_names:
                return candidate
            index += 1

    def _get_file_list(self, key: str) -> list[str]:
        if self.config is None:
            return []

        raw_value = self.config.get(key, [])
        if isinstance(raw_value, str):
            return [raw_value] if raw_value else []

        if isinstance(raw_value, list):
            files = [str(item).strip() for item in raw_value if str(item).strip()]
            return files

        return []

    def _get_int(self, key: str, default: int, minimum: int | None = None) -> int:
        if self.config is None:
            value = default
        else:
            raw = self.config.get(key, default)
            try:
                value = int(raw)
            except Exception:
                value = default

        if minimum is not None and value < minimum:
            return minimum
        return value

    def _get_str(self, key: str, default: str) -> str:
        if self.config is None:
            return default
        value = self.config.get(key, default)
        return str(value) if value is not None else default

    def _set_config_value(self, key: str, value: str) -> None:
        if self.config is None:
            return

        current = str(self.config.get(key, "") or "")
        if current == value:
            return

        self.config[key] = value
        self._save_config()

    def _save_config(self) -> None:
        if self.config is None:
            return

        save = getattr(self.config, "save_config", None)
        if callable(save):
            try:
                save()
            except Exception as exc:
                logger.warning(f"[AliyunBailianTTS] 保存配置失败: {exc}")
