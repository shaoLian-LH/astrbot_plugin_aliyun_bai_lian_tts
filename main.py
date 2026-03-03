import random
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from .constants import (
        DEFAULT_MAX_TEXT_LENGTH,
        DEFAULT_TTS_PROBABILITY,
        PLUGIN_AUTHOR,
        PLUGIN_DESC,
        PLUGIN_DATA_ROOT,
        PLUGIN_ID,
        PLUGIN_VERSION,
    )
    from .services.voice_service import VoiceService
except ImportError:
    from constants import (  # type: ignore
        DEFAULT_MAX_TEXT_LENGTH,
        DEFAULT_TTS_PROBABILITY,
        PLUGIN_AUTHOR,
        PLUGIN_DESC,
        PLUGIN_DATA_ROOT,
        PLUGIN_ID,
        PLUGIN_VERSION,
    )
    from services.voice_service import VoiceService  # type: ignore


@register(PLUGIN_ID, PLUGIN_AUTHOR, PLUGIN_DESC, PLUGIN_VERSION)
class AliyunBailianTTSPlugin(Star):
    """多音色阿里百炼 TTS 插件。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config
        self.voice_service = VoiceService(config=config, data_dir=self._resolve_data_dir())

    async def initialize(self):
        try:
            await self.voice_service.sync_voice_profiles(force=True)
        except Exception as exc:
            logger.warning(f"[AliyunBailianTTS] 初始化音色同步失败: {exc}")

    @filter.on_decorating_result()
    async def convert_result_to_tts(self, event: AstrMessageEvent):
        if not self._should_generate_tts():
            return

        result = event.get_result()
        if not result or not getattr(result, "chain", None):
            return

        text = self._extract_text(result.chain)
        if not text:
            return

        max_text_length = self._read_int_config("max_text_length", DEFAULT_MAX_TEXT_LENGTH, min_value=1)
        if len(text) > max_text_length:
            return

        try:
            audio_file = await self.voice_service.synthesize_text(text)
        except Exception as exc:
            logger.warning(f"[AliyunBailianTTS] 语音合成跳过: {exc}")
            return

        result.chain = [Comp.Record(file=str(audio_file), url=str(audio_file))]

    @filter.command("音色列表", alias={"tts_voice_list"})
    async def list_voices(self, event: AstrMessageEvent):
        """管理员查看远端音色列表。"""
        denied = self._check_admin(event)
        if denied:
            yield event.plain_result(denied)
            return

        try:
            voices = await self.voice_service.list_remote_voices(ensure_synced=True)
        except Exception as exc:
            yield event.plain_result(f"查询音色失败: {exc}")
            return

        if not voices:
            yield event.plain_result("暂无可用音色，请先上传音频并执行「刷新音色」。")
            return

        active_voice_id = self.voice_service.get_active_voice_id()
        lines = ["当前音色列表："]
        for item in voices:
            voice_id = item.get("voice_id", "")
            if not voice_id:
                continue
            marker = "* " if voice_id == active_voice_id else "- "
            extras: list[str] = []
            status = item.get("status", "")
            if status:
                extras.append(status)
            provider = item.get("provider", "")
            if provider:
                extras.append(provider)
            local_names = item.get("local_names", "")
            if local_names:
                extras.append(f"本地文件: {local_names}")
            suffix = f" ({' | '.join(extras)})" if extras else ""
            lines.append(f"{marker}{voice_id}{suffix}")

        lines.append("（* 为当前生效音色）")
        yield event.plain_result("\n".join(lines))

    @filter.command("设置音色", alias={"tts_set_voice"})
    async def set_voice(self, event: AstrMessageEvent, voice_id: str = ""):
        """管理员设置当前生效音色。"""
        denied = self._check_admin(event)
        if denied:
            yield event.plain_result(denied)
            return

        target = str(voice_id or "").strip()
        if not target:
            yield event.plain_result("用法: 设置音色 <音色ID>")
            return

        try:
            await self.voice_service.set_active_voice_id(target)
        except Exception as exc:
            yield event.plain_result(f"设置失败: {exc}")
            return

        yield event.plain_result(f"已切换当前音色为: {target}")

    @filter.command("创建音色", alias={"tts_create_voice"})
    async def create_voice(self, event: AstrMessageEvent, source_name: str = ""):
        """管理员基于上传音声文件创建音色。"""
        denied = self._check_admin(event)
        if denied:
            yield event.plain_result(denied)
            return

        target = str(source_name or "").strip()
        if not target:
            names = await self._fetch_source_names()
            hint = "可用音声: " + (", ".join(names) if names else "无")
            yield event.plain_result(f"用法: 创建音色 <音声文件名(不含后缀)>\n{hint}")
            return

        try:
            result = await self.voice_service.create_voice_by_source_name(target)
        except Exception as exc:
            yield event.plain_result(f"创建音色失败: {exc}")
            return

        lines = [
            f"创建音色请求成功: {result.get('source_name', target)}",
            f"音色ID: {result.get('voice_id', '')}",
            f"状态: {result.get('status', 'UNKNOWN')}",
            f"目标模型: {result.get('target_model', '')}",
            "可使用「创建音色状态」继续查看部署进度。",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.command("创建音色状态", alias={"tts_create_voice_status"})
    async def create_voice_status(self, event: AstrMessageEvent):
        """管理员查看创建音色任务状态。"""
        denied = self._check_admin(event)
        if denied:
            yield event.plain_result(denied)
            return

        try:
            jobs = await self.voice_service.get_creation_status(refresh_remote=True)
        except Exception as exc:
            yield event.plain_result(f"查询创建音色状态失败: {exc}")
            return

        if not jobs:
            yield event.plain_result("暂无创建记录。先执行「创建音色 <音声文件名(不含后缀)>」。")
            return

        lines = ["创建音色状态："]
        for item in jobs:
            source = item.get("source_name", "")
            status = item.get("status", "UNKNOWN")
            voice_id = item.get("voice_id", "")
            target_model = item.get("target_model", "")
            message = item.get("message", "")
            base = f"- {source}: {status}"
            if voice_id:
                base += f" | {voice_id}"
            if target_model:
                base += f" | {target_model}"
            lines.append(base)
            if message:
                lines.append(f"  说明: {message}")
        yield event.plain_result("\n".join(lines))

    @filter.command("删除音色", alias={"tts_delete_voice"})
    async def delete_voice(self, event: AstrMessageEvent, voice_id: str = ""):
        """管理员删除远端音色。"""
        denied = self._check_admin(event)
        if denied:
            yield event.plain_result(denied)
            return

        target = str(voice_id or "").strip()
        if not target:
            yield event.plain_result("用法: 删除音色 <音色ID>")
            return

        try:
            provider, removed_count = await self.voice_service.delete_voice_id(target)
        except Exception as exc:
            yield event.plain_result(f"删除失败: {exc}")
            return

        active_voice_id = self.voice_service.get_active_voice_id()
        lines = [f"已删除音色: {target}（接口: {provider}）"]
        if removed_count:
            lines.append(f"本地缓存已移除 {removed_count} 条关联记录。")
        if active_voice_id:
            lines.append(f"当前生效音色: {active_voice_id}")
        else:
            lines.append("当前未设置生效音色，请使用「设置音色 <音色ID>」。")
        yield event.plain_result("\n".join(lines))

    @filter.command("刷新音色", alias={"tts_voice_refresh"})
    async def refresh_voice_profiles(self, event: AstrMessageEvent):
        """管理员手动刷新上传文件索引。"""
        denied = self._check_admin(event)
        if denied:
            yield event.plain_result(denied)
            return

        try:
            await self.voice_service.sync_voice_profiles(force=True)
        except Exception as exc:
            yield event.plain_result(f"刷新失败: {exc}")
            return

        source_names = self.voice_service.get_source_names()
        if not source_names:
            yield event.plain_result("刷新完成，但未发现可用音声文件。")
            return

        profiles = await self.voice_service.get_creation_status(refresh_remote=False)
        active_voice_id = self.voice_service.get_active_voice_id()
        lines = [f"刷新完成，共 {len(source_names)} 个音声文件：{', '.join(source_names)}"]
        if active_voice_id:
            lines.append(f"当前生效音色ID: {active_voice_id}")
        pending = [item for item in profiles if str(item.get("status", "")).upper() == "NOT_CREATED"]
        if pending:
            names = ", ".join(str(item.get("source_name", "")) for item in pending)
            lines.append(f"待创建音色: {names}")
        yield event.plain_result("\n".join(lines))

    async def _fetch_source_names(self) -> list[str]:
        try:
            await self.voice_service.sync_voice_profiles(force=False)
        except Exception:
            return []
        return self.voice_service.get_source_names()

    def _resolve_data_dir(self) -> Path:
        target = Path(PLUGIN_DATA_ROOT) / PLUGIN_ID
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _read_int_config(self, key: str, default: int, min_value: int = 0, max_value: int = 10**9) -> int:
        if self.config is None:
            return default

        raw = self.config.get(key, default)
        try:
            value = int(raw)
        except Exception:
            value = default

        if value < min_value:
            return min_value
        if value > max_value:
            return max_value
        return value

    def _check_admin(self, event: AstrMessageEvent) -> str:
        sender_id = self._get_sender_id(event)
        admin_ids = self.voice_service.get_admin_ids()
        if not admin_ids:
            return "未配置管理员。请先在配置 admin_user_ids 中填写可操作的用户ID。"
        if not sender_id or not self.voice_service.is_admin(sender_id):
            return f"仅管理员可操作。当前发送者ID: {sender_id or 'unknown'}"
        return ""

    def _get_sender_id(self, event: AstrMessageEvent) -> str:
        try:
            sender_id = str(event.get_sender_id() or "").strip()
        except Exception:
            sender_id = ""
        return sender_id

    def _should_generate_tts(self) -> bool:
        probability = self._read_int_config(
            "tts_probability",
            default=DEFAULT_TTS_PROBABILITY,
            min_value=0,
            max_value=100,
        )
        if probability <= 0:
            return False
        if probability >= 100:
            return True
        return random.randint(1, 100) <= probability

    def _extract_text(self, chain) -> str:
        text_parts: list[str] = []
        for component in chain:
            text = getattr(component, "text", None)
            if text:
                text_parts.append(str(text))
        return "".join(text_parts).strip()

    async def terminate(self):
        return None
