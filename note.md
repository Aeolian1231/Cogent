## question？
当前实现两个终端分别启动tui会互相影响，cli也会影响tui，修改该问题

客户端-daemon通信逻辑

## 为什么使用 asyncio 协程而非多线程？

1. 任务性质：daemon 绝大部分时间在等——等 LLM API 返回、等客户端命令、等用户 permission 响应。没有 CPU 密集计算。
2. 资源浪费：线程需要栈内存分配和上下文切换，成本高昂。
3. Python GIL：CPython 多线程无法真正并行，IO 密集型场景 asyncio 更轻量。

## asyncio 事件循环怎么工作？

async def 定义协程函数，await 让出控制权，事件循环调度其他就绪协程执行。asyncio 是 FIFO 非抢占式调度：

1. 创建协程对象（如 make_error()）
2. 挂起当前协程
3. 等待被调用方 return 后唤醒

## 为什么用 JSON-RPC + NDJSON 两层协议？

分层解耦：外层（envelope）统一处理路由、请求 ID、错误码；内层（commands/events）只管业务语义。添加新命令只需修改内层模型，外层完全复用。

## 客户端如何收到 daemon 事件推送？

客户端                              daemon
  │                                  │
  │ event.subscribe(topics, scope)   │
  │ ───────────────────────────────> │
  │                                  │ subscriber(writer, topics, scope)
  │                                  │ → 存入 _subscriptions 列表
  │                                  │
  │              ◀─── EventBus.publish(event)
  │              ◀─── broadcaster.handle() 遍历订阅
  │              ◀─── 匹配 topic/scope → writer.write(event)

1. 客户端 on_event(handler) 注册本地回调（零网络通信）
2. 客户端 event.subscribe(topics, scope) 向 daemon 注册兴趣
3. daemon 用 IpcEventBroadcaster 存储 (writer, topics, scope) 三元组
4. 事件产生时遍历列表，匹配则写入对应 writer
5. 客户端 run_event_loop() 从自己的 reader 读取并调用 handler

## 不同客户端？writer 获取机制

每个连接 accept 时 asyncio 内部为每个客户端生成独立的 StreamWriter，是同一 TCP 端口下的不同连接对象。

text
SocketServer._handle_line()
  → _writer_var.set(writer)       # ContextVar 存入当前连接的 writer
  → handler(params)
    → get_connection_writer()      # ContextVar 取出
      → broadcaster.subscribe(writer, ...)

每个 asyncio.Task 有独立的 context 副本（Python 3.7+），同一连接多个 Task 的 writer 是**同一个对象**，不同连接的 Task 互不干扰。

## 断线重连与事件回放

TUI 客户端支持 TCP 断线后自动重连。重连时客户端重新发送 event.subscribe，带上 replay_from_run 参数指向之前的 run_id。daemon 从 events.jsonl 补推错过的历史事件，再接实时流。

text
TUI 断线 → new TCP 连接 → event.subscribe(replay_from_run="abc123")
  → daemon 回放 abc123/events.jsonl → 再实时订阅

## Agent 执行流程

text
用户 → CLI/TUI → daemon
  → AgentRunner → AgentLoop
    → plan(LLM) → act(Tool) → observe

## 为什么是 plan → act，不是 act → observe？

Anthropic Messages API 要求：assistant 消息必须先于 tool result 出现在历史中；tool result 作为下一条 user 消息紧跟其后。

## 工具调用失败了，循环会停吗？

不会。is_error 结果追加进历史，LLM 自行决定换方式重试或结束循环。

## Prompt Caching（缓存命中）

相邻两次 API 调用若 system prompt 内容相同，Anthropic API 直接从缓存读取，消耗的 token 数大幅减少。

## 事件模型

所有事件是 Pydantic v2 模型（带 Discriminator("type") 的联合类型），提供：
1. **校验**：创建时检查参数类型
2. **序列化**：.model_dump() / .model_dump_json()
3. **类型判别**：根据 type 字段自动分派到具体子类

## Token 缓冲

LLM 流式生成时 llm.token 事件密集，每个只含一两个字符。TUI 将 token 追加到内部缓冲区，等下一个非 token 事件到来时整段渲染，避免频繁屏幕闪烁。

## trace 设计

埋点 1：IPC 层——SocketServer
SocketServer 在两个地方埋点：收到命令时，以及发出响应时

埋点 2：IPC 层——IpcEventBroadcaster
broadcaster 每次成功推送事件之后，写一条 push 记录

埋点 3：EventBus 层——CoreApp 订阅者
CoreApp.run() 里，trace 作为 EventBus 的普通订阅者挂上去，和 EventWriter、IpcEventBroadcaster 并列

埋点 4：LLM 层——TracingProvider
LLM 层的 trace 用了一个不同的思路：不是在 AnthropicProvider 里埋点，而是在它外面封装一层
TracingProvider 实现了和 AnthropicProvider 完全相同的 LLMProvider 接口，内部持有一个 inner provider 的引用

## trace 记录为什么是一个文件，而不是每个 run 一个文件

CLIENT→CORE 命令在被解析成功之前，run_id 还不存在。客户端发来 agent.run，守护进程解析出命令、生成 run_id、启动 AgentRunner——这三件事有先后顺序，第一条 trace 记录在第三件事之前就必须写出去。

更大的问题是 IPC 命令本身：core.ping、event.subscribe 这类命令根本就没有关联的 run_id，它们是全局性的守护进程行为，不属于任何一次 run。

## 为什么不去掉events.jsonl，只保留daemon.jsonl

events.jsonl 用于客户端重连时回放历史事件
当 TUI 断开重连时，它发 event.subscribe(replay_from_run="20260617-072736-6cd47e")，daemon 直接定位到这一个 run 的 events.jsonl，把历史事件推过去。

如果用 daemon.jsonl 替代：
✗ 需要在几万条全局记录里 grep run_id
✗ 每条是 TraceRecord 包装，需要提取 data 字段才是原始事件
✗ 全表扫描，O(n) 且 n 持续增长

## events.jsonl 和 thread.jsonl

两者对比
events.jsonl	                            thread.jsonl
粒度	per-run：runs/<run_id>/	              per-session：<session_id>/
内容	事件流：step.、tool.、llm.token       对话流：user 消息、assistant 回复
用途	TUI 重连回放、daemon 重启后重建 LLM     对话上下文，传给下一次 API 调用

## 为什么不用数据库

任务数量通常是个位数到十几个，文件 I/O 的开销完全可以忽略。用文件的好处是：任务的完整历史可以直接用 ls 和 cat 查看，不需要任何工具，调试非常方便。

## Agent自主规划

LLM 是无状态的——每次 API 调用都是一个独立的请求-响应，它不会自动记得"我规划了 5 步，现在做到第 3 步"。

task 工具把规划能力和执行能力分开：
LLM 负责规划（task_create 拆解目标）和决策（task_update 标记完成）
TaskManager 负责记忆（磁盘持久）和自动化（级联解锁）

工具           作用
task_create   创建新任务，可选设置 blocked_by 依赖
task_update   更新状态（pending / in_progress / completed）或调整依赖
task_list     列出所有任务的当前状态，返回格式化摘要
task_get      获取单个任务的完整 JSON

## 为什么不强制依赖关系

强制锁有代价：如果 agent 中途意识到依赖不合理（比如 blocked_by 是它自己之前错误填的），它无法灵活调整。正确的做法是：

Task 工具提示依赖关系（task_list 里显示 (blocked by: [1,2])）
LLM 自己决定是否服从——它可以理性遵守，也可以在必要时自主覆盖
依赖在完成时自动清理（_clear_dependency），agent 不需要记住清理这件事

## 一次典型的多任务 run，context.messages 的演变

step 1:
  → LLM: [task_create("分析目录结构"), task_create("读取核心模块", blocked_by=[1]), ...]
  → context.messages 追加 assistant 消息（tool_use blocks）
  → invoke_tool 依次执行，任务文件写入 .tasks/
  → context.messages 追加 user 消息（tool_result blocks）

step 2:
  → LLM: [task_update(1, "in_progress"), list_dir(".")]
  → ...

step 3:
  → LLM: [read_file("src/core/loop.py"), ...]
  → task_update(1, "completed")  ← 触发 _clear_dependency，任务 2 的 blocked_by 被清空

step N:
  → LLM: [write_file("/tmp/report.md", content="...")]
  → task_update(N, "completed")
  → end_turn

## 重连机制

TUI 内部有一个永不退出的 _socket_loop 循环，TCP 断线后自动等待 2 秒重试，不退出进程。重连过程分三步：接会话 → 补事件 → 同步状态。

第一步：接上旧会话。 重连时如果内存里还有 _session_id（说明之前已经建过会话），就不创建 session，直接用旧 id 继续通信。daemon 那边的 session 在 TCP 断线后依旧存活，重连后发的 send_message 能正常路由到同一会话。如果 daemon 也重启过、内存里丢了 session，它会从磁盘 meta.json 自动恢复。

第二步：补回错过的事件。 如果当前有活跃 run（_active_run_id 非空），订阅事件时带上 replay_from_run，daemon 从 events.jsonl 把断线期间产生的事件一次性推回来。TUI 逐条重放后 UI 与断线前一致——工具调用块、LLM 输出、步骤进度全部还原。_active_run_id 由 run.started 设置、run.finished 清空，全程自动追踪。

第三步：恢复交互状态。 重放完历史事件后，根据 _active_run_id 决定 prompt 状态：有活跃 run 就设为 busy 并禁用输入框；run 已结束或空闲就启用输入框，用户可以继续对话。

覆盖范围
当前机制只覆盖同一 TUI 进程内的 TCP 断线重连、daemon崩溃重启。TUI 进程崩溃重启后内存全丢，_session_id 和 _active_run_id 都是 None，会走首次连接路径——新建 session、不回放历史、UI 空白。

## AgentRunner 使用工厂模式

每次 run 需要新实例
每个 send_message 是一次全新的 Agent 执行，AgentRunner 内部持有本次 run 的状态（事件总线订阅、工具注册表、task_registry）。如果用同一个实例复用，上一轮的工具状态、事件监听会污染下一轮。工厂保证每次 factory() 调出一个干净的新 AgentRunner。

## Agent 两层记忆

thread.jsonl 记录的是完整对话过程：用户说了什么，assistant 返回了什么，工具调用了什么，工具结果是什么。它回答的是"上一轮发生过什么"。
notes.md 记录的是 agent 主动保存的长期事实和决策，用于session内多轮对话的长期记忆。它回答的是"以后应该记住什么"。

只有 thread 够不够？
thread 是"历史流水"，notes 是"事实层"。当 thread 过长时，旧消息会被压缩成摘要。摘要可能漏掉某个细节，但 notes 不参与 compact，会原样注入 system prompt。

## thread.jsonl 完整回放太浪费 token 了，为什么不只取最近 5 轮？

问题在于滑动窗口看起来省钱，实际会破坏 agent 的连续性：
1. 它可能截断 tool_use 和 tool_result 的配对，直接让 Anthropic API 拒绝请求。
2. 它会丢掉旧工具结果，而旧工具结果可能正是下一轮回答的依据。
3. 它把"哪些历史重要"这个判断提前交给工程代码，但这件事通常模型自己更擅长。

cache_read_input_tokens 会承担大头。我们用缓存降低成本，而不是用工程截断破坏语义。
完整回放不是偷懒，而是现代 agent 会话的基础策略：保留完整消息结构，让模型看到真实过程，再靠 prompt caching 控制成本。

## 命令权限管理

Tier 1  deny_patterns    → 未命中（没有危险模式）
Tier 2  OUTSIDE_CWD      → 未命中（不涉及绝对路径/~等）
Tier 3  session always   → 未命中（首次执行，无缓存）
Tier 4  persistent       → 未命中（policy.toml 无记录）
Tier 5  allow_patterns   → 未命中（不在白名单）
Tier 6  tool default     → bash 默认 ASK
─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ 
→ 进入 ASK 路径：创建 Future → 发 permission.requested → 等用户回复

返回值 (allowed, decision) 的含义：
decision	            allowed	含义
"allow_once"	        True	本次放行，下次还问
"always_allow"	      True	本次session放行（缓存，非持久化）
"deny_once"	          False	本次拒绝
"always_deny"	        False	本次session拒绝
"auto_allow"	        True	被某层策略直接放行，未弹窗
"auto_deny"	          False	被 deny_patterns 拦截
"timeout"	            False	用户 60 秒未响应

## future 跨协程通信

Future 就是 asyncio 内置的"一次性跨协程通道"——创建者 await，另一人 set_result，不需要轮询、不需要回调、不需要锁。

等待回复的代码（check_and_wait）和收到回复的代码（_permission_respond_handler）分别在两个无关协程中：
协程 A                           协程 B
check_and_wait()                 _permission_respond_handler()
  ↓                                ↓
  需要等回复                       收到 TCP 回复
  ↓                                ↓
  ???     ← 怎么把回复传过去？     拿到了 decision = "allow_once"

替代方案对比
方案	代码	                                           问题
轮询	while id not in results: await sleep(0.1)	      CPU 空转，延迟 100ms
回调	self._callbacks[id] = on_done	                  回调地狱，错误处理难
