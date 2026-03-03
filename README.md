# astrbot_plugin_aliyun_bai_lian_tts

支持阿里百炼语音复刻（声音提取）的 AstrBot TTS 插件。

## 功能

- 支持上传多份音声文件（mp3/wav/m4a/flac/ogg/amr/aac）
- 创建音色改为管理员手动触发：`创建音色 <音声文件名(不含后缀)>`
- 支持查看创建结果与部署进度：`创建音色状态`
- 支持管理员命令管理音色：`音色列表`、`设置音色`、`删除音色`
- 支持语音缓存（按 `model + voice_id + text` 命中）

## 配置项

- `api_key`: 阿里百炼 API Key
- `api_region`: `cn` / `intl`
- `model_id`: TTS 合成模型 ID
- `voice_target_model`: 创建音色目标模型（`create_voice.target_model`）
- `voice_enrollment_model`: 音色管理模型（默认 `voice-enrollment`）
- `voice_language_hints`: 创建音色语言提示（可选）
- `voice_files`: 上传音声文件（可多选）
- `active_voice_id`: 当前生效音色 ID（建议用命令维护）
- `admin_user_ids`: 管理员用户 ID 列表（所有管理命令都受此限制）
- `cache_ttl_seconds`: 缓存有效期
- `max_cache_items`: 最大缓存条数

## 管理员命令

- `刷新音色`：刷新上传音声文件索引
- `创建音色 <音声文件名(不含后缀)>`：创建音色
- `创建音色状态`：查看创建/部署状态
- `音色列表`：查看远端音色列表与当前生效音色
- `设置音色 <音色ID>`：切换生效音色
- `删除音色 <音色ID>`：删除远端音色并清理本地关联缓存

> 兼容英文别名：`tts_voice_refresh`、`tts_create_voice`、`tts_create_voice_status`、`tts_voice_list`、`tts_set_voice`、`tts_delete_voice`

## 说明

- 插件运行数据保存在 AstrBot `data` 目录下：
  - `/AstrBot/data/plugin_data/astrbot_plugin_aliyun_bai_lian_tts/voice_uploads/`：上传音声归档文件
  - `/AstrBot/data/plugin_data/astrbot_plugin_aliyun_bai_lian_tts/voice_profiles.json`：本地音色映射缓存
  - `/AstrBot/data/plugin_data/astrbot_plugin_aliyun_bai_lian_tts/voice_creation_jobs.json`：创建音色状态缓存
  - `/AstrBot/data/plugin_data/astrbot_plugin_aliyun_bai_lian_tts/audio_cache/`：合成音频缓存
- 上传文件不会自动创建音色，必须执行 `创建音色` 指令。
- 创建音色时只使用本地归档文件，不再依赖公网 URL。
