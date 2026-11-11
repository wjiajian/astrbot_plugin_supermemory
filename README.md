# astrbot_plugin_supermemory

这是一个 AstrBot 插件，用于接入 [Supermemory](https://supermemory.ai/docs/intro)，为私聊和群聊提供按会话隔离的长期记忆能力。

Supermemory 是一个面向 AI 应用的记忆服务，可以把对话内容写入 conversations，并在后续请求中按语义搜索相关记忆。插件使用 Supermemory 托管 API，不需要自建向量数据库，也不需要额外维护记忆管理 WebUI。

插件不修改 AstrBot 核心，也不提供自定义 WebUI。配置仍然通过 AstrBot 根据 `_conf_schema.json` 生成的插件配置界面完成，记忆管理使用 Supermemory 自带后台。

## 功能特性

- 在每次 LLM 请求前，从 Supermemory 召回当前会话相关记忆。
- 通过 `extra_user_content_parts` 注入临时 `<supermemory_context>` 内容，不写入 AstrBot 持久会话历史。
- 在 LLM 回复后，按写入判定策略将值得长期保存的内容写入 Supermemory conversations。
- 私聊和群聊严格按 scope 隔离，群聊使用“群公共记忆 + 群成员个人记忆”双层召回，避免同群不同用户的个人记忆互相串扰。
- 发送到 Supermemory 的 sender ID、group ID、`unified_msg_origin` 都会先做 hash。
- 可启用统一的 `memory_ai_*` 配置，让小模型负责判断是否写入、生成自然事实句，并为自动召回和 `/supermemory recall` 拓展搜索 query。
- 支持 `search_threshold` 和 `search_mode` 调整召回严格程度与搜索模式。
- 结合最近几轮对话理解“就用那个”这类指代，用于判断记什么、查什么。
- 提供 `/supermemory` 命令，用于状态检查、手动召回和当前会话临时开关。
- 可选接入 private_companion 7.0.7+，承接模型侧记忆，并把角色日记、梦境和主动消息保存到独立的角色容器。

## 平台兼容性

已测试 AstrBot 平台：

- `aiocqhttp`
- `qq_official`
- `qq_official_webhook`

其他 AstrBot 平台理论兼容，未逐一测试。

## 安装与配置

1. 在 Supermemory Developer Platform 创建或获取 API Key。
2. 将本仓库安装到 AstrBot 插件目录。
3. 在 AstrBot WebUI 的插件配置中填写必填项“Supermemory API Key（必填）”（`api_key`）。

   其余配置保持默认即可使用。如果想让记忆更精准，可以开启“用 AI 整理和查找记忆”（`memory_ai_enabled`），并在“整理记忆使用的模型”（`memory_ai_provider_id`）中选一个便宜、快速的小模型。
4. 保存配置后，重载插件或重启 AstrBot。

插件依赖只包含 `httpx`，AstrBot 通常会根据 `requirements.txt` 自动安装。

如果需要手动安装依赖，可在 AstrBot 环境中执行：

```bash
pip install -r data/plugins/astrbot_plugin_supermemory/requirements.txt
```

## 配置项说明

下表中的“页面名称”是 AstrBot WebUI 插件配置页中显示的名称，括号内是对应的配置 key。

### 连接

| 页面名称 | 默认值 | 说明 |
|---|---|---|
| 启用长期记忆（`enabled`） | 开启 | 总开关。关闭后插件不再读取或写入任何记忆，已保存的记忆不受影响。 |
| 接入陪伴插件（`companion_provider_enabled`） | 关闭 | 向 private_companion 7.0.7+ 注册为外部记忆 Provider，承接模型侧记忆，并接收角色日记、梦境和主动消息。没装陪伴插件时无效果；具体行为见下方协同说明。 |
| 允许保存含个人信息的角色记录（`companion_retain_personal_sensitive`） | 关闭 | 角色记录里的邮箱、手机号、身份证号或地址默认不保存，打开后才保存。密码、API Key、私钥始终不保存。 |
| Supermemory 服务地址（`api_base`） | `https://api.supermemory.ai` | 保持默认即可。 |
| Supermemory API Key（必填）（`api_key`） | 空 | 在 Supermemory Developer Platform 后台创建并复制到这里。 |
| 连接 Supermemory 的超时时间（秒）（`request_timeout_seconds`） | `8` | 超过这个时间没有响应就放弃本次读写，机器人照常回复。网络较慢时可适当调大。 |

### 记忆范围

| 页面名称 | 默认值 | 说明 |
|---|---|---|
| 私聊中使用记忆（`enable_private_memory`） | 开启 | 机器人会记住私聊对象的信息，并在之后的私聊中想起来。每个用户的记忆相互隔离。 |
| 群聊中使用记忆（`enable_group_memory`） | 开启 | 机器人会记住群成员在该群里的个人信息，以及（若开启下一项）群规则、群项目等群内公共信息。不同群的记忆相互隔离。 |
| 群聊中使用群公共记忆（`enable_group_shared_memory`） | 开启 | 群规则、群公告、项目约定等全群通用的信息会单独保存，并对该群所有成员可见。关闭后群聊只使用每个成员各自的个人记忆。 |

### 召回

| 页面名称 | 默认值 | 说明 |
|---|---|---|
| 每次回复最多参考几条记忆（`recall_limit`） | `5` | 回复前会从记忆库中找出相关的几条，附加给 AI 作参考。群聊中群公共记忆和个人记忆各自最多取这么多条。条数越多参考越全，但会消耗更多 token。 |
| 每条记忆最多附加多少字（`recall_item_max_chars`） | `360` | 过长的记忆会被截断后再交给 AI，用来控制 token 消耗。一般无需修改。 |
| 记忆相关度门槛（0～1）（`search_threshold`） | `0.6` | 只有与当前消息相关度达到此值的记忆才会被参考。调低能找到更多记忆，但可能混入无关内容；调高则只保留非常相关的记忆。 |
| 记忆搜索方式（`search_mode`） | 记忆（`memories`） | 记忆：只搜索 Supermemory 整理后的记忆，适合聊天场景。记忆 + 原文（`hybrid`）：同时搜索记忆和原始对话片段，更全但更杂。仅原文（`documents`）：只搜索原始对话片段。 |
| 参考最近几轮对话来理解指代（`context_turns`） | `3` | 用户说“就用那个”“改成周五”时，插件会结合最近几轮对话判断“那个”“周五”指什么，再决定记什么、查什么。这些旧对话只用来理解当前这句话，不会被再次写入。填 `0` 关闭。 |
| 记忆结果解析深度（高级）（`memory_extract_max_depth`） | `4` | 解析 Supermemory 返回数据时最多向下查找几层。一般无需修改。 |

### 写入

| 页面名称 | 默认值 | 说明 |
|---|---|---|
| 自动保存新记忆（`retain_enabled`） | 开启 | 机器人每次回复后判断这轮对话有没有值得长期记住的内容，有就保存。关闭后停止自动保存对话；陪伴插件的角色记录由“接入陪伴插件”单独控制。 |
| 自动保存的严格程度（`retain_decision_mode`） | 均衡（`balanced`） | 均衡：只保存偏好、身份、约定这类长期有用的信息，忽略闲聊。严格（`strict`）：只有用户明确说“记住……”时才保存。全部保存（`all`）：每轮对话原样保存，记忆库增长快、噪音多，仅用于兼容旧版本。 |
| 过短的消息不保存（字数）（`retain_min_chars`） | `8` | 用户消息的有效字数少于此值时，视为信息量不足，不会自动保存。用户明确说“记住”时不受此限制。 |
| 隐私信息需用户明确要求才保存（`retain_sensitive_requires_explicit`） | 开启 | 包含手机号、邮箱、身份证号等隐私信息的消息，只有用户明确说“记住”才会保存。密码、API Key、Token、私钥等密钥无论如何都不会保存。 |
| 保存对话原文而不是整理后的要点（`retain_write_raw_conversation`） | 关闭 | 默认只保存整理后的简洁事实（如“用户喜欢喝美式咖啡”）。开启后改为保存这轮对话的原文，细节更全，但记忆更冗长、更难被准确查到。“全部保存”模式下始终保存原文。 |
| 保存原文时包含用户的消息（`retain_user_message`） | 开启 | 仅在保存对话原文时生效：原文里是否包含用户说的话。一般保持开启。 |
| 保存原文时包含机器人的回复（`retain_assistant_message`） | 开启 | 仅在保存对话原文时生效：原文里是否包含机器人的回复。关闭可减少冗余内容。 |

### 去重

| 页面名称 | 默认值 | 说明 |
|---|---|---|
| 跳过重复的记忆（`retain_dedupe_enabled`） | 开启 | 保存前先查一下已有记忆，内容几乎相同的就不再重复保存。用户纠正旧信息时（如“我搬到上海了”）始终会保存。 |
| 相似度达到多少算重复（0～1）（`retain_dedupe_threshold`） | `0.85` | 新记忆与已有记忆的相似度达到此值时视为重复并跳过。调低会跳过更多相近内容；调高则只跳过几乎一模一样的内容。 |
| 查重时比对的已有记忆条数（`retain_dedupe_limit`） | `5` | 保存前取出最相关的几条已有记忆做比对。条数越多查重越全，但请求稍慢。 |

### AI 记忆分析

| 页面名称 | 默认值 | 说明 |
|---|---|---|
| 用 AI 整理和查找记忆（`memory_ai_enabled`） | 关闭 | 由 AI 判断哪些内容值得记、把对话整理成简洁的事实，并在查找记忆时补充更多相关搜索词，效果更好。代价是每轮对话会额外调用 1～2 次 LLM，建议选一个便宜、快速的小模型。 |
| 整理记忆使用的模型（`memory_ai_provider_id`） | 空 | 仅在开启“用 AI 整理和查找记忆”后生效。推荐选择便宜、快速的小模型；留空则使用当前对话正在用的模型。 |
| 所选模型出错时改用当前对话模型（`memory_ai_fallback_to_current_provider`） | 开启 | 上一项选择的模型调用失败时，是否改用当前对话的模型重试。关闭后本次跳过 AI，改用不调用 AI 的默认方式处理。 |
| AI 把握度低于此值时不保存（`memory_ai_min_confidence`） | `0.7` | AI 判断每条记忆时会给出 0～1 的把握度。数值越高，保存得越谨慎、记忆越少越准；数值越低，保存得越多。 |

旧版 `retain_ai_enabled` / `retain_ai_provider_id` / `retain_ai_fallback_to_current_provider` / `retain_ai_min_confidence` 已在配置页隐藏，已保存的旧配置仍然有效；新配置请使用 `memory_ai_*`。

## 写入判定策略

写入流程现在采用 AI 优先、规则兜底：

1. 如果 `memory_ai_enabled=true`，本地只做空文本、命令、硬敏感信息等必要过滤，然后调用 AI 从本轮对话中提取 0 到 5 条关于用户或群的长期记忆，每条带 scope、记忆类型（`preference`/`profile`/`event`/`project`/`rule`/`correction`）和置信度。AI 被要求只记用户本人说出或确认的信息，不记助手回复中的通用知识、用户的一次性提问和闲聊，并结合当前时间把“下周五”等相对时间换算成绝对日期。
2. AI 必须返回结构化 JSON（`{"memories": [...]}`，同时兼容旧的单条格式）；插件解析失败或调用失败时，不影响聊天流程，会回退到本地精简规则。置信度低于 `memory_ai_min_confidence` 的单条记忆会被单独丢弃。
3. 如果未启用 AI，本地规则只保留明确记忆意图、基础稳定事实、群规则/项目事实、个人偏好等几类，不再依赖大量手写锚点。
4. 写入前仍会用 `memory_text` 在当前 containerTag search，若相似度超过 `retain_dedupe_threshold` 则跳过；纠正类记忆会继续写入，并在 metadata 中标记为 `correction`。
5. AI 判定时会附带最近 `context_turns` 轮对话（来自 AstrBot 当前请求的上下文），但只从当前这一轮提取记忆；群聊上下文中其他成员的发言不会被归到当前用户。工具调用的中间响应不会触发写入。
6. 每条记忆单独写入一次 conversation，metadata 中附带消息发生时间 `retention_timestamp`（UTC ISO 8601）。

AI 生成的 `memory_text` 会作为自然、独立、可检索的事实句写入，不再强制添加人工锚点前缀。默认不会额外调用 LLM；只有开启 `memory_ai_enabled` 后才会调用小模型。需要兼容旧行为时，可设置 `retain_decision_mode=all`。

## 召回拓展策略

默认召回只使用用户原始 query。开启 `memory_ai_enabled` 后，每次自动召回和手动 `/supermemory recall` 都会先让 AI 将原始 query 改写成 1 到 4 条搜索 query，插件会并发检索，在每个 scope 内用倒数排名融合（RRF）合并各条 query 的结果并去重，再按 `recall_limit` 注入。这样扩展 query 的结果不会被第一条 query 挤掉；部分请求失败时仍使用其余结果。

召回改写同样会参考最近 `context_turns` 轮对话来补全指代。未开启 `memory_ai_enabled` 时，如果当前消息很短（如“那个呢？”），会额外用“上一条用户消息 + 当前消息”检索一次。“谢谢”“好的”等闲聊和命令不会触发自动召回。

自动注入的记忆内容首行带有使用说明（按需参考、不逐条复述、以用户当前说法为准、无关则忽略），每条记忆前标注日期（取自 Supermemory 返回的日期字段，缺失时不标注），群公共记忆和群成员记忆分别放在 `scope="group_shared"` 和 `scope="group_member"` 的块中。手动 `/supermemory recall` 的输出不带使用说明。AI 只负责拓展搜索词，不允许回答用户问题；如果 AI 调用失败或 JSON 解析失败，则直接回退为只使用原 query。

Supermemory 仍继续使用现有 `search_threshold` 和 `search_mode`，本版不新增额外的手动召回阈值配置。

## 命令

- `/supermemory status`：检查配置完整性、当前 scope 和 Supermemory 连通性。
- `/supermemory recall <query>`：在当前会话 scope 下手动检索记忆。
- `/supermemory on`：启用当前会话记忆。
- `/supermemory off`：关闭当前会话记忆。
- `/supermemory help`：显示命令帮助。

`/supermemory on` 和 `/supermemory off` 的状态保存在 AstrBot 插件数据目录中，只影响当前会话 scope，不会修改 Supermemory 中已有的记忆。

## 与陪伴插件（private_companion）协同

[陪伴插件](https://github.com/menglimi/astrbot_plugin_private_companion) 7.0.7+ 提供 `companion.external-memory.v1` 接口。在两个插件都启用、Supermemory API Key 已配置后，打开“接入陪伴插件”并重载插件即可使用；该选项默认关闭。

- Supermemory 注册为 `handles_model_memory=True` 的 Provider，继续通过自己的 LLM 钩子召回和保存对话。陪伴插件的内置记忆注入与记忆工具随之让位；本插件不注册 `read` 或 `summary` 回调。
- 日记、梦境（`persona_life`）和主动消息（`proactive_message`）作为角色自己的内容写入独立的 `astrbot_persona_<platform>_<role_hash>_<installation_hash>` 容器，**不参与私聊、群聊召回**。角色、安装和会话身份均使用带盐哈希。
- 角色记录沿用现有敏感内容规则：硬凭据始终拒绝，个人信息默认拒绝，由“允许保存含个人信息的角色记录”单独控制。
- 同一目标内按 `memory_id` 防止重复写入，并使用稳定的 `conversationId`。本地账本保留 30 天、最多 5000 条；账本占位无法写入磁盘时，不提交远端写入。
- 只有确定未接收的失败（4xx、连接未建立）进入本地补写队列。队列最多 50 条，从首次失败起保留 24 小时；后续成功写入时最多补写 10 条，并检查时间预算。宿主取消回调或进程重启时，尚未处理的队列条目继续保留。
- 超时、连接中断、5xx、响应无法解析或回调被取消时，远端可能已经接收，账本保留 `pending`，后续不自动重写；原记录仍在陪伴插件中。
- 账本和队列按“服务地址 + API Key 的带盐哈希”分区。换地址或 Key 后，旧目标的失败记录不会补写到新目标。由于无法仅凭 Key 判断账号是否相同，同一账号轮换 Key 也会视为新目标；已保存的记录若再次推送，可能重新写入。
- 停用时先停止接收新写入，再等待在途写入与补写结束（最多 10 秒）并注销；注销失败时保留引用供重试，该注册不再参与选择。

接管是安装级的。执行 `/supermemory off` 或关闭某类会话记忆后，该会话不会自动回退到陪伴插件的内置记忆；角色记录上传仍由“接入陪伴插件”控制，不受会话开关或 `retain_enabled` 影响。两边已有的记忆不会迁移。关闭“接入陪伴插件”或停用 Supermemory，即可让陪伴插件恢复管理自己的记忆。

同时安装 Hindsight、Supermemory 或其他外部记忆插件时，仅启用其中一个长期记忆插件。只关闭陪伴接入开关，不会停止该插件自身的 LLM 记忆注入。

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

私聊只召回当前私聊 scope 的记忆；群聊会同时召回当前群的公共记忆和当前发言成员在该群内的个人记忆。启用 `memory_ai_enabled` 时由 AI 在允许范围内选择 `group_shared` 或 `group_member`；未启用 AI 或 AI 失败时，本地规则会把群规则、群公告、项目约定等内容写入群公共层，把个人偏好、称呼、个人资料等内容写入群成员个人层。`retain_decision_mode=all` 时保持旧行为，同时写入群公共层和群成员个人层。

写入 metadata 会附带 `retention_reason`、`retention_type`、`retention_source`、`retention_confidence`、`retention_action` 和 `retention_timestamp`，便于在 Supermemory 后台排查某条记忆来自规则、AI 判断还是纠正/补充写入。

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
- 群聊事件拿不到当前发言人的 `sender_id`，插件会跳过本轮记忆操作，避免多个未知用户被归入同一个个人记忆层。

迁移 AstrBot 或插件时，建议同时备份插件数据目录中的 `salt.txt` 和 `scope_state.json`。其中 `salt.txt` 会影响历史记忆是否还能被同一 scope 召回，`scope_state.json` 保存 `/supermemory on` 和 `/supermemory off` 的当前会话开关状态。使用陪伴插件接入时，一并备份 `companion_written_ids.json`（去重账本）和 `companion_pending_records.json`（补写队列）。

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
PYTHONPYCACHEPREFIX=/tmp/supermemory-pycache python -m py_compile main.py commands.py supermemory_client.py memory_formatter.py scope.py retention_policy.py memory_ai.py conversation_context.py companion_bridge.py
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
