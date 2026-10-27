# 协作房间（多 agent 协作）

面向截图中的「协作」标签：一个房间把**路径**和**若干 agent 成员**绑在一起，用户在房间里用
`@成员` 派活，每个成员在**自己独立的会话**里干活，进度与结果回到房间时间线。

设计目标是**不新造执行引擎**：房间只是对既有会话能力的编排层。因此模型的登录、权限、工具、
计划模式、费用统计全部沿用现有会话，房间本身不持有任何 API 凭据。

---

## 1. 概念模型

```
Room（房间）
  ├ id / name / cwd            ← 房间即一个工作目录的协作上下文
  ├ members[]                  ← 参与者
  └ messages[]                 ← 时间线（用户发言 + 各成员结果）

Member（成员）
  ├ member_id                  ← 房间内唯一
  ├ label                      ← 展示名，如 "Claude · iris" / "Codex · nyx"
  ├ engine                     ← claude | codex | dsh
  ├ profile_id                 ← 可选，选择账号/配置档
  └ session_id                 ← 该成员在房间里的**独立会话**（懒创建）

Message（时间线条目）
  ├ msg_id / ts / text
  ├ author_kind                ← user | member
  ├ author_label
  ├ mentions[]                 ← 派生出的成员 id（用于派发与高亮）
  └ state                      ← pending | running | done | failed（成员消息）
```

**关键决策：成员 = 独立会话。**
截图里每个成员都带「跳转会话 ↗」，说明它们各自是一条普通会话。这样做的好处是：

- 复用现成的模型选择、权限档、计划模式、用量统计
- 用户可以随时跳进某个成员的会话看完整过程，房间只保留结论
- 房间崩溃或重启不影响会话本身

---

## 2. 存储

房间状态放在 Wrapper 的状态目录（`cc_remote.config.wrapper_config().state_dir`）：

```
state_dir/
  rooms.json                 ← 房间与成员定义、时间线（有界）
  rooms.lock                 ← 排他锁（复用 file_lock_compat）
```

- 单文件 JSON，原子写（临时文件 + `os.replace`），权限 0600
- 时间线**按房间有界**：每房间保留最近 N 条（默认 500），成员消息正文截断
- 房间与会话的绑定关系（`session_id`）持久化，重启后自动恢复

> 为什么不放 SQLite：房间数据量小、写入频率低（人手动发言），JSON 更易备份与人工检查。
> 历史索引那种高写入量的东西才需要 SQLite。

---

## 3. 派发语义

一条用户消息带 `@A @B` 时：

1. **解析 mention** → 成员集合；`@全体` 展开为房间全部成员
2. 对每个被点名成员：
   - 若 `session_id` 为空 → 按成员的 engine/profile 创建会话（等价于 `new_session` 带 cwd）
   - 把消息以 `query` 投递到该会话，`delivery` 按房间设置（默认 `queue`）
3. 每个成员**并行**执行，互不阻塞；同一成员的多条派发按队列串行
4. 成员的回合终止时，取其最终 assistant 文本追加为一条 `member` 消息

并发上限：房间级 `max_parallel`（默认 2），避免一台机器上同时开十几个 agent 打爆内存。

### 派发时的提示词包装

成员的会话是普通会话，直接投原文会让 agent 不知道自己在协作。投递前加一段**极短**前缀：

```
[房间 <name> · 目录 <cwd>]
<用户原文>
```

不注入其他成员的历史，避免上下文爆炸；需要上下文时用户显式 `@` 谁就让谁说。

### `@` 句柄规则（实现细节）

展示名形如 `Claude · iris`，**含空格无法写成单个 `@` token**，所以一个成员按**分段**接受
多个句柄：

| 展示名 | 可用句柄 |
|---|---|
| `Claude · iris` | `@Claude`、`@iris` |
| `Codex · nyx` | `@Codex`、`@nyx` |

- 匹配**大小写不敏感**（`@NYX` 等同 `@nyx`）
- 句柄**重名时全部命中**：房间里有 `Claude · iris` 和 `Claude · nyx` 时，
  `@Claude` 会派给两个成员，用 `@iris` / `@nyx` 精确指定
- `@全体`、`@所有人`、`@all`、`@everyone` 广播给房间全部成员
- 未匹配的 `@`（如正文里的 `@media`）**忽略**，不报错

这解释了截图里为什么写 `@Claude` / `@Codex` 而不是全名。

### 引导（steer）与队列（queue）

对应截图右下角的两个按钮：

- **引导** → `steer`：把追加指令送进该成员**正在跑的回合**
- **排队** → `queue`：作为该成员的下一条 query 排队

两者都直接映射到现有 `Steer` / `Query(delivery="queue")`，房间不需要新机制。

---

## 4. 线协议（新增）

方向标记：`C→W` = client → wrapper，`W→C` = wrapper → client。
全部沿用现有 `_Command` / `_Base` 信封（`cmd_id`、`client_id` 由 relay 填充）。

### 命令（C→W）

| 类型 | 关键字段 | 说明 |
|---|---|---|
| `list_rooms` | — | 拉取全部房间（含成员与最近消息摘要） |
| `get_room` | `room_id`, `before?`, `limit?` | 分页读取某房间时间线 |
| `create_room` | `name`, `cwd`, `members[]` | 新建房间 |
| `update_room` | `room_id`, `name?`, `members?` | 改名、增删成员 |
| `delete_room` | `room_id` | 删除房间（不动成员会话） |
| `post_room_message` | `room_id`, `text`, `delivery` | 发言并按 mention 派发 |
| `steer_room_member` | `room_id`, `member_id`, `text` | 引导某成员当前回合 |
| `cancel_room_member` | `room_id`, `member_id` | 中断某成员当前回合 |

### 事件（W→C）

| 类型 | 关键字段 | 说明 |
|---|---|---|
| `room_list` | `request_id`, `rooms[]` | 房间列表 |
| `room_snapshot` | `room`, `messages[]`, `states[]` | 单房间完整快照（供首屏与重连恢复） |
| `room_message` | 一条 `Message` | 时间线增量 |
| `room_member_state` | `member_id`, `session_id`, `state`, `started_at`, `queued` | 成员状态变化（驱动「处理中 24s」「队列 · 1」） |
| `room_invalidated` | `room_id` | 提示客户端重新拉取 |

房间事件与现有会话事件**互不干扰**：房间事件用独立 `type`，不进入会话的 ring buffer，
因此不参与会话重放（重连后靠 `room_snapshot` 恢复）。

---

## 5. 与现有机制的关系

| 需要的能力 | 复用什么 | 不需要新建 |
|---|---|---|
| 成员执行 | 现有会话池（`machine._spawn`）+ `Query` | 执行引擎、模型客户端 |
| 进度与工具过程 | 现有回合事件（`tool_use`、`delta`、`assistant_msg_*`） | 事件总线 |
| 中断 / 引导 / 排队 | `Interrupt` / `Steer` / `Query(queue)` | 调度器 |
| 权限与计划模式 | 会话级 `perm` / `collaboration_mode` | 权限系统 |
| 用量 | 会话级 `turn_usage` | 计量 |

房间层只新增：**房间存储、mention 解析、并行派发、回合终止后的结果回收**。

---

## 6. UI 映射（对照截图逐块）

| 截图位置 | 元素 | 数据来源 |
|---|---|---|
| 左上标签栏 | `Work / Code / 协作` | 新增第三个 surface：`space: "rooms"` |
| 左栏搜索 | 「搜索房间、路径…」 | 房间名 + cwd 过滤 |
| 左栏置顶 | 房间卡片（名称 + 路径 + 成员头像） | `room_list` |
| 左栏底部 | 「+ 新建房间」 | `create_room`（选目录 + 选成员） |
| 中栏头部 | 房间名 · 路径 · 机器 · 成员 chips · `+ 邀请` | `room_snapshot` |
| 中栏消息流 | 用户 @消息 / 成员结果卡片（含「已完成」） | `room_message` |
| 中栏底部 | 成员行「处理中 24s / 展开过程 / 跳转会话」 | `room_member_state` + 会话事件 |
| 中栏底部 | `@` 输入框 | `post_room_message` |
| 右栏 | 成员详情：工具调用、命令输出、token 用量、队列 | 该成员会话的既有事件流 |
| 右栏底部 | 「引导 / 排队」+ 输入框 | `steer_room_member` / `post_room_message(delivery=queue)` |
| 成员行链接 | 「跳转会话 ↗」 | 切到该成员的 `session_id`（复用 `switch_session`） |

右栏**不需要新协议**：它展示的就是该成员会话的既有事件，只要按 `session_id` 订阅即可。

---

## 7. 分阶段落地

| 阶段 | 内容 | 验收 | 状态 |
|---|---|---|---|
| **P1** | 协议契约 + 房间存储 + mention 解析 | 单测覆盖 CRUD、mention、有界时间线 | ✅ 15 测试 |
| **P2** | 派发：成员会话绑定、并行派发、permit 限流、失败回滚 | 两成员各自收到派发、结果回填 | ✅ 11 测试 |
| **P3** | 协议消息 + wrapper 适配器 | 类型可 round-trip、适配器映射到既有入口 | 🟡 协议/适配器完成，`_dispatch_incoming_command` 接入与回合终止回收待做 |
| **P4** | 前端：`协作` surface、房间列表、房间视图、成员状态 | 截图里的主流程可用 | ⬜ |
| **P5** | 右栏详情、引导/排队、跳转会话 | 与截图一致 | ⬜ |

> 已落地代码：`cc_remote/wrapper/rooms.py`（存储 + mention + 派发）、
> `cc_remote/wrapper/room_host.py`（映射到 `_spawn` / `_handle_query` /
> `_handle_steer` / `_handle_interrupt`）、`cc_remote/protocol.py`（13 条房间消息）。
> 测试：`tests/test_rooms.py`、`tests/test_room_dispatch.py`、`tests/test_room_host.py`。

### 已知取舍

- **不做**成员之间的自动对话（agent 互相 @）。截图里也没有：所有派发都由人发起。
  这样并发与费用可控，也避免两个 agent 互相刷消息。
- **不做**跨机器房间。房间绑定单个机器（wrapper），因为成员会话是本机的。
- 成员数量上限 8、房间上限 200、每房间时间线 500 条 —— 与现有协议的有界风格一致。

---

## 8. 待确认的产品问题

1. **成员是否共用目录？** 截图里房间有 `~/cooperation`，成员是否都在该目录启动？
   （当前设计：是，成员会话的 cwd = 房间 cwd；不同目录的成员留给后续）
2. **`@全体` 之后的结果如何汇总？** 当前设计：各成员结果独立追加，不做二次总结。
   若要「汇总成一段」，需要一个额外的 summarize 回合，属于 P5 之后的增强。
3. **删除房间是否保留会话？** 当前设计：保留（会话是用户的资产，房间只是视图）。
