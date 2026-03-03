"""Aliyun Bailian voice enrollment and realtime TTS client."""

from __future__ import annotations

import asyncio
import base64
import wave
from pathlib import Path
from typing import Any

import requests

try:
    from ..constants import COSY_VOICE_ENROLLMENT_MODEL, REGION_CONFIG
except ImportError:
    from constants import COSY_VOICE_ENROLLMENT_MODEL, REGION_CONFIG  # type: ignore

_VOICE_STATUS_OK = {"OK", "DEPLOYING", "UNDEPLOYED", "UNKNOWN"}


class _WaveCallback:
    def __init__(self, output_path: Path):
        self.output_path = output_path
        self._pcm_chunks: list[bytes] = []
        self._done = asyncio.Event()
        self.error: str | None = None
        self._closed = False

    def on_open(self):
        return None

    def on_close(self, code, msg):
        self._finish()

    def on_error(self, error):
        self.error = str(error)
        self._done.set()

    def on_event(self, response):
        event_type = response.get("type", "")

        if event_type == "response.audio.delta":
            delta = response.get("delta", "")
            if delta:
                self._pcm_chunks.append(base64.b64decode(delta))
            return

        if event_type == "error":
            self.error = str(response.get("message", "Unknown error"))
            self._done.set()
            return

        if event_type == "session.finished":
            self._finish()

    def _finish(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._pcm_chunks:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(self.output_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(24000)
                wav_file.writeframes(b"".join(self._pcm_chunks))

        self._done.set()

    async def wait(self, timeout_seconds: int = 120) -> None:
        await asyncio.wait_for(self._done.wait(), timeout=timeout_seconds)


class AliyunTTSClient:
    def __init__(self, api_key: str, region: str):
        region_cfg = REGION_CONFIG.get(region) or REGION_CONFIG["cn"]
        self.api_key = api_key
        self.customization_url = region_cfg["customization_url"]
        self.ws_url = region_cfg["ws_url"]

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post_customization(self, payload: dict[str, Any], timeout: int = 60) -> dict[str, Any]:
        response = requests.post(
            self.customization_url,
            json=payload,
            headers=self._headers(),
            timeout=timeout,
        )
        response.raise_for_status()

        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"接口返回格式异常: {data}")

        code = str(data.get("code", "")).strip()
        if code and code != "200":
            message = str(data.get("message", "")).strip()
            raise RuntimeError(f"{code}: {message or data}")
        return data

    def create_voice(
        self,
        target_model: str,
        prefix: str,
        source_url: str,
        enrollment_model: str = COSY_VOICE_ENROLLMENT_MODEL,
        language_hints: str = "",
    ) -> str:
        input_data = {
            "action": "create_voice",
            "target_model": target_model,
            "prefix": prefix,
            "url": source_url,
        }
        if language_hints.strip():
            input_data["language_hints"] = language_hints.strip()

        payload = {
            "model": enrollment_model,
            "input": input_data,
        }

        data = self._post_customization(payload=payload, timeout=120)
        output = data.get("output", {})
        voice_id = str(output.get("voice_id") or output.get("voice") or "").strip()
        if not voice_id:
            raise RuntimeError(f"创建音色失败，返回结果缺少 voice_id: {data}")
        return voice_id

    def query_voice(
        self,
        voice_id: str,
        enrollment_model: str = COSY_VOICE_ENROLLMENT_MODEL,
    ) -> dict[str, str]:
        payload = {
            "model": enrollment_model,
            "input": {
                "action": "query_voice",
                "voice_id": voice_id,
            },
        }
        data = self._post_customization(payload=payload)
        output = data.get("output", {})

        status = str(output.get("status") or "UNKNOWN").strip().upper()
        if status not in _VOICE_STATUS_OK:
            status = "UNKNOWN"

        return {
            "voice_id": str(output.get("voice_id") or voice_id).strip(),
            "status": status,
            "gmt_create": str(output.get("gmt_create") or "").strip(),
            "gmt_modified": str(output.get("gmt_modified") or "").strip(),
        }

    def list_voices(
        self,
        enrollment_model: str = COSY_VOICE_ENROLLMENT_MODEL,
        prefix: str = "",
        page_index: int = 1,
        page_size: int = 100,
    ) -> list[dict[str, str]]:
        input_data = {
            "action": "list_voice",
            "page_index": page_index,
            "page_size": page_size,
        }
        if prefix.strip():
            input_data["prefix"] = prefix.strip()

        payload = {
            "model": enrollment_model,
            "input": input_data,
        }

        data = self._post_customization(payload=payload)
        voice_list = data.get("output", {}).get("voice_list", [])
        if not isinstance(voice_list, list):
            return []

        result: list[dict[str, str]] = []
        for item in voice_list:
            if not isinstance(item, dict):
                continue
            voice_id = str(item.get("voice_id") or item.get("voice") or "").strip()
            if not voice_id:
                continue
            status = str(item.get("status") or "UNKNOWN").strip().upper()
            if status not in _VOICE_STATUS_OK:
                status = "UNKNOWN"
            result.append(
                {
                    "voice_id": voice_id,
                    "status": status,
                    "gmt_create": str(item.get("gmt_create") or "").strip(),
                    "provider": "cosy",
                }
            )
        return result

    def delete_voice(
        self,
        voice_id: str,
        enrollment_model: str = COSY_VOICE_ENROLLMENT_MODEL,
    ) -> None:
        payload = {
            "model": enrollment_model,
            "input": {
                "action": "delete_voice",
                "voice_id": voice_id,
            },
        }
        self._post_customization(payload=payload)

    async def synthesize(self, text: str, model_id: str, voice_id: str, output_path: Path) -> Path:
        import dashscope
        from dashscope.audio.qwen_tts_realtime import (
            AudioFormat,
            QwenTtsRealtime,
            QwenTtsRealtimeCallback,
        )

        callback_impl = _WaveCallback(output_path)

        class CallbackAdapter(QwenTtsRealtimeCallback):
            def on_open(self):
                callback_impl.on_open()

            def on_close(self, code, msg):
                callback_impl.on_close(code, msg)

            def on_error(self, error):
                callback_impl.on_error(error)

            def on_event(self, response):
                callback_impl.on_event(response)

        dashscope.api_key = self.api_key
        callback = CallbackAdapter()
        tts = QwenTtsRealtime(model=model_id, callback=callback, url=self.ws_url)

        try:
            tts.connect()
            tts.update_session(
                voice=voice_id,
                response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
                mode="server_commit",
            )
            tts.append_text(text)
            tts.finish()
            await callback_impl.wait()
        finally:
            close_func = getattr(tts, "close", None)
            if callable(close_func):
                try:
                    close_func()
                except Exception:
                    pass

        if callback_impl.error:
            raise RuntimeError(f"语音合成失败: {callback_impl.error}")

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("语音合成失败: 未生成有效音频")

        return output_path
