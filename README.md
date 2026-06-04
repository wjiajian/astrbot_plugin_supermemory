# astrbot_plugin_supermemory

这是一个 AstrBot 插件，用于接入 [Supermemory](https://supermemory.ai/docs/intro)，为私聊和群聊提供按会话隔离的长期记忆能力。

插件不修改 AstrBot 核心，也不提供自定义 WebUI。配置仍然通过 AstrBot 根据 `_conf_schema.json` 生成的插件配置界面完成，记忆管理使用 Supermemory 自带后台。

## 功能

- 在 LLM 请求前从 Supermemory 召回当前会话相关记忆，并以临时上下文注入。
- 在 LLM 回复后把本轮用户消息和助手回复写入 Supermemory conversations。
- 私聊、群聊使用独立 `containerTag`，群聊同时维护群公共记忆和群内成员个人记忆，避免个人记忆串场。
- 会话 ID 使用本地 salt 哈希，不把原始用户 ID、群 ID 写入 Supermemory 标签。
- 支持按当前会话开启/关闭记忆。

## 配置

在 AstrBot 插件配置中填写：

- `api_key`：Supermemory API Key。
- `api_base`：默认 `https://api.supermemory.ai`。
- `recall_limit`：每轮最多注入的记忆条数，默认 `5`。
- `search_threshold`：搜索阈值，默认 `0.6`。
- `search_mode`：默认 `memories`，也可设为 `hybrid` 或 `documents`。
- `enable_group_shared_memory`：默认 `true`。开启后群聊会同时召回/写入群公共记忆和当前群成员个人记忆；关闭后群聊只使用当前群成员个人记忆。

## 命令

- `/supermemory status`：检查配置、当前 scope 和连接状态。
- `/supermemory recall <query>`：手动检索当前会话记忆。
- `/supermemory on`：启用当前会话记忆。
- `/supermemory off`：关闭当前会话记忆。
- `/supermemory help`：显示帮助。

## 隔离规则

- 私聊：`astrbot_private_<platform>_<sender_hash>_<umo_hash>`
- 群公共：`astrbot_group_shared_<platform>_<group_hash>_<umo_hash>`
- 群成员个人：`astrbot_group_member_<platform>_<group_hash>_<sender_hash>_<umo_hash>`

群聊召回会先查群公共层，再查当前发言人的群成员个人层。群成员个人层按群 ID + 用户 ID 隔离，同一个群里的不同用户不会共用个人记忆。

## 开发验证

```bash
python -m unittest discover -s tests -v
```
