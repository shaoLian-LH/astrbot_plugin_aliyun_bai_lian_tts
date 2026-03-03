"""Plugin constants."""

PLUGIN_ID = "astrbot_plugin_aliyun_bai_lian_tts"
PLUGIN_AUTHOR = "xuemufan"
PLUGIN_DESC = "支持管理员创建/管理音色与缓存的阿里百炼 TTS 插件"
PLUGIN_VERSION = "0.0.1"
PLUGIN_DATA_ROOT = "/AstrBot/data/plugin_data"

DEFAULT_MODEL_ID = "qwen3-tts-vc-realtime-2025-11-27"
COSY_VOICE_ENROLLMENT_MODEL = "voice-enrollment"
DEFAULT_MAX_TEXT_LENGTH = 120
DEFAULT_TTS_PROBABILITY = 100
DEFAULT_CACHE_TTL_SECONDS = 7 * 24 * 3600
DEFAULT_MAX_CACHE_ITEMS = 200
DEFAULT_REGION = "cn"

REGION_CONFIG = {
    "cn": {
        "customization_url": "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization",
        "ws_url": "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
    },
    "intl": {
        "customization_url": "https://dashscope-intl.aliyuncs.com/api/v1/services/audio/tts/customization",
        "ws_url": "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime",
    },
}
