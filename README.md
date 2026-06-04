# astrbot_plugin_supermemory

这是一个 AstrBot 插件，用于接入 [Supermemory](https://supermemory.ai/docs/intro)，为私聊和群聊提供按会话隔离的长期记忆能力。

Supermemory 是一个面向 AI 应用的记忆服务，可以把对话内容写入 conversations，并在后续请求中按语义搜索相关记忆。插件使用 Supermemory 托管 API，不需要自建向量数据库，也不需要额外维护记忆管理 WebUI。

插件不修改 AstrBot 核心，也不提供自定义 WebUI。配置仍然通过 AstrBot 根据 `_conf_schema.json` 生成的插件配置界面完成，记忆管理使用 Supermemory 自带后台。

## 功能特性

- 在每次 LLM 请求前，从 Supermemory 召回当前会话相关记忆。
- 通过 `extra_user_content_parts` 注入临时 `<supermemory_context>` 内容，不写入 AstrBot 持久会话历史。
- 在 LLM 回复后，将本轮用户消息和助手回复写入 Supermemory conversations。
- 私聊和群聊严格按 scope 隔离，群聊使用“群公共记忆 + 群成员个人记忆”双层召回，避免同群不同用户的个人记忆互相串扰。
- 发送到 Supermemory 的 sender ID、group ID、`unified_msg_origin` 都会先做 hash。
- 支持 `search_threshold` 和 `search_mode` 调整召回严格程度与搜索模式。
- 提供 `/supermemory` 命令，用于状态检查、手动召回和当前会话临时开关。

## 平台兼容性

已测试 AstrBot 平台：

- `aiocqhttp`
- `qq_official`
- `qq_official_webhook`

其他 AstrBot 平台理论兼容，未逐一测试。

## 安装与配置

1. 在 Supermemory Developer Platform 创建或获取 API Key。
2. 将本仓库安装到 AstrBot 插件目录。
3. 在 AstrBot 插件配置中填写：
   - `api_key`：Supermemory API Key
   - `api_base`：保持默认值 `https://api.supermemory.ai`
   - `recall_limit`：每轮最多注入的记忆条数，默认 `5`
   - `search_threshold`：搜索阈值，默认 `0.6`
   - `search_mode`：搜索模式，默认 `memories`
4. 按需调整私聊、群聊和群公共记忆开关。
5. 保存配置后，重载插件或重启 AstrBot。

插件依赖只包含 `httpx`，AstrBot 通常会根据 `requirements.txt` 自动安装。

如果需要手动安装依赖，可在 AstrBot 环境中执行：

```bash
pip install -r data/plugins/astrbot_plugin_supermemory/requirements.txt
```

## 配置项说明

- `enabled`：全局启用或关闭插件。
- `api_base`：Supermemory API Base URL，默认 `https://api.supermemory.ai`。
- `api_key`：Supermemory API Key。
- `enable_private_memory`：启用私聊记忆。
- `enable_group_memory`：启用群聊记忆。
- `enable_group_shared_memory`：启用群公共记忆层。关闭后，群聊只使用当前群成员个人记忆。
- `recall_limit`：每轮最多注入的记忆条数。
- `search_threshold`：Supermemory 搜索阈值，`0` 返回更多结果，`1` 更严格。
- `search_mode`：搜索模式，可选 `memories`、`hybrid`、`documents`。
- `retain_enabled`：启用 LLM 回复后写入 Supermemory。
- `retain_user_message`：写入用户本轮消息。
- `retain_assistant_message`：写入助手本轮回复。
- `request_timeout_seconds`：Supermemory 请求超时时间，单位秒。

## 命令

- `/supermemory status`：检查配置完整性、当前 scope 和 Supermemory 连通性。
- `/supermemory recall <query>`：在当前会话 scope 下手动检索记忆。
- `/supermemory on`：启用当前会话记忆。
- `/supermemory off`：关闭当前会话记忆。
- `/supermemory help`：显示命令帮助。

`/supermemory on` 和 `/supermemory off` 的状态保存在 AstrBot 插件数据目录中，只影响当前会话 scope，不会修改 Supermemory 中已有的记忆。

## Scope 隔离策略

Supermemory 使用 `containerTag` 隔离不同会话 scope。

私聊 containerTag：

```text
astrbot_private_<platform_id>_<sender_id_hash>_<umo_hash>
```

群聊使用双层 containerTag。

群公共记忆：

```text
astrbot_group_shared_<platform_id>_<group_id_hash>_<umo_hash>
```

群成员个人记忆：

```text
astrbot_group_member_<platform_id>_<group_id_hash>_<sender_id_hash>_<umo_hash>
```

私聊只召回当前私聊 scope 的记忆；群聊会先召回当前群的公共记忆，再召回当前发言成员在该群内的个人记忆。写入时也会同时写入群公共层和群成员个人层，便于后续在 Supermemory 后台分别管理。

如果关闭 `enable_group_shared_memory`，群聊不会召回或写入群公共记忆层，但仍会使用当前群成员个人记忆层。

## ID 稳定性与迁移注意事项

正常重启 AstrBot 通常不会改变记忆 scope。插件生成 `containerTag` 时会使用：

- `platform_id`：AstrBot 平台名，例如 `aiocqhttp`、`qq_official`。
- `sender_id`：平台提供的用户 ID。
- `group_id`：平台提供的群 ID。
- `unified_msg_origin`：AstrBot 的会话来源标识。
- 本地 `salt`：插件首次运行时生成，并保存在 AstrBot 插件数据目录中。

只要平台适配器、机器人账号和插件数据目录不变，重启后 hash 出来的 `containerTag` 应保持稳定，旧记忆可以继续召回。

以下情况可能导致同一个用户或群生成不同 scope，从而召回不到旧记忆：

- 删除或迁移时丢失插件数据目录，导致 `salt.txt` 重新生成。
- 更换平台适配器，例如从 `aiocqhttp` 切换到 `qq_official`。
- 更换机器人账号、QQ 官方应用或平台配置，导致平台侧用户 ID 变化。
- 平台或 AstrBot 适配器更新后改变了 `sender_id`、`group_id`、`unified_msg_origin` 的生成方式。
- 群聊事件暂时拿不到 `group_id`，插件会降级为私聊 scope；之后如果又能拿到 `group_id`，scope 会发生变化。

迁移 AstrBot 或插件时，建议同时备份插件数据目录中的 `salt.txt` 和 `scope_state.json`。其中 `salt.txt` 会影响历史记忆是否还能被同一 scope 召回，`scope_state.json` 保存 `/supermemory on` 和 `/supermemory off` 的当前会话开关状态。

## Supermemory API

插件直接调用 Supermemory REST API，不依赖官方 SDK：

- Search：`POST /v4/search`
- Retain：`POST /v4/conversations`
- 状态检查：使用 `POST /v4/search` 对当前 `containerTag` 发起一次轻量搜索

写入时使用 `conversationId`、`messages`、`containerTags` 和 `metadata`。搜索时使用当前 scope 的 `containerTag`，并传入 `limit`、`threshold` 和 `searchMode`。

## 测试方法

### 本地单元测试

在仓库根目录执行：

```bash
python -B -m unittest discover -s tests -v
```

### 语法检查

```bash
python -B -m py_compile main.py commands.py supermemory_client.py memory_formatter.py scope.py
```

### AstrBot 手动验收

1. 在 AstrBot WebUI 中确认插件已启用，且 `api_key` 已配置。
2. 在聊天中发送：

   ```text
   /supermemory status
   ```

   期望看到配置完整，并且 Supermemory 连接正常。

3. 发送一条需要记忆的内容，例如：

   ```text
   我最喜欢的饮料是冰美式，请记住。
   ```

4. 等待几秒到几十秒后，再问：

   ```text
   我最喜欢喝什么？
   ```

   如果 Supermemory 已完成处理，模型应能通过 recall 回答出相关记忆。

5. 测试当前会话开关：

   ```text
   /supermemory off
   /supermemory on
   ```

6. 在不同私聊、不同群聊之间分别测试，确认记忆不会跨 scope 召回。
