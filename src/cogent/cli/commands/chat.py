from __future__ import annotations

import asyncio
import sys
from typing import Any

from cogent.core.config import CogentConfig
from cogent.core.transport.socket_client import IpcError, SocketClient

_DECISION_MAP: dict[str, str] = {
    "y": "allow_once",
    "a": "always_allow",
    "n": "deny_once",
    "d": "always_deny",
}


class ChatPrinter:
    # 初始化 chat 模式的流式输出状态、待审批权限请求和运行状态
    def __init__(self) -> None:
        self._inline = False
        self.pending_permission_id: str | None = None
        self.busy = False            # agent 是否正在执行一次 run
        self.closed = False          # session 已关闭
        self._ready = asyncio.Event()  # 主循环需要用户输入时设置
        self._ready.set()            # 初始状态：等待用户输入

    # 若当前 LLM token 尚未换行，则补一个换行
    def _ensure_newline(self) -> None:
        if self._inline:
            print()
            self._inline = False

    # 通知主循环：状态有变，可能需要读取用户输入
    def _wakeup(self) -> None:
        self._ready.set()

    # 按事件类型打印 chat 输出、更新权限和运行状态
    async def handle(self, event: dict[str, Any]) -> None:
        t = event.get("type", "")
        if t == "llm.token":
            print(event.get("token", ""), end="", flush=True)
            self._inline = True
        elif t == "tool.call_started":
            self._ensure_newline()
            print(f"[tool] {event.get('tool_name', '')}")
        elif t == "permission.requested":
            self._ensure_newline()
            tool_name = str(event.get("tool_name", ""))
            param_preview = str(event.get("param_preview", ""))
            tool_use_id = str(event.get("tool_use_id", ""))
            print(f"[permission] {tool_name}  {param_preview}")
            print("  y=allow once  a=allow session  n=deny once  d=deny session")
            self.pending_permission_id = tool_use_id
            self._wakeup()
        elif t == "permission.denied":
            # 超时或断连等非用户交互触发的 deny——清除挂起状态
            self.pending_permission_id = None
        elif t == "session.waiting_for_input":
            self._ensure_newline()
            self.pending_permission_id = None
            self.busy = False
            self._wakeup()
        elif t == "session.closed":
            self._ensure_newline()
            self.pending_permission_id = None
            self.busy = False
            self.closed = True
            print("session closed.")
            self._wakeup()


# 在线程池中读取 stdin，避免阻塞 socket event loop
async def _readline(prompt: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


# 在后台发送一条消息到 session，不阻塞主输入循环
async def _send_message_bg(
    client: SocketClient,
    session_id: str,
    content: str,
    printer: ChatPrinter,
) -> None:
    try:
        await client.send_command(
            "session.send_message",
            {"session_id": session_id, "content": content},
        )
    except (IpcError, RuntimeError, OSError) as e:
        printer._ensure_newline()
        print(f"[error] {e}")
    finally:
        # run 结束（正常或异常），恢复到空闲状态
        printer.busy = False
        printer.pending_permission_id = None
        printer._wakeup()


# 异步核心：创建 chat session，仅在需要用户输入时才提示 "> "，避免 prompt 干扰输出
async def _chat_async(config: CogentConfig) -> int:
    client = SocketClient(config.host, config.port)
    try:
        await client.connect()
    except (ConnectionRefusedError, OSError):
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        return 1

    printer = ChatPrinter()
    client.on_event(printer.handle)
    loop_task = asyncio.create_task(client.run_event_loop())

    try:
        await client.send_command(
            "event.subscribe",
            {
                "topics": ["session.*", "run.*", "tool.*", "llm.token", "permission.*"],
                "scope": "global",
            },
        )
        created = await client.send_command("session.create", {"mode": "chat"})
        session_id = str(created["session_id"])
        print(f"[session: {session_id}]")

        while not printer.closed:
            # agent 运行中且无待审批权限——休眠等待事件通知，不打印 "> "
            if printer.busy and not printer.pending_permission_id:
                await printer._ready.wait()
                printer._ready.clear()
                continue

            try:
                line = await _readline("> ")
            except (EOFError, KeyboardInterrupt):
                break
            content = line.strip()
            if not content:
                continue

            # 有待审批的权限请求时，将用户输入解释为决策而非聊天消息
            if printer.pending_permission_id:
                decision = _DECISION_MAP.get(content.lower())
                if decision is None:
                    print("  enter y (allow once), a (allow session), "
                          "n (deny once), d (deny session)")
                    continue
                tool_use_id = printer.pending_permission_id
                printer.pending_permission_id = None
                await client.send_command(
                    "permission.respond",
                    {"tool_use_id": tool_use_id, "decision": decision},
                )
                continue

            # agent 正在运行中但无待审批权限——提示用户等待
            if printer.busy:
                print("  agent is running — wait or respond to permission prompt")
                continue

            # 开始新的 agent run：后台发送，主循环休眠等事件通知
            printer.busy = True
            printer._ready.clear()
            asyncio.create_task(
                _send_message_bg(client, session_id, content, printer)
            )

        # session.closed 事件已触发时不再重复 close
        if not printer.closed:
            try:
                await client.send_command("session.close", {"session_id": session_id})
            except IpcError:
                pass
    except IpcError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        await client.close()
    return 0


# 执行 cogent chat 命令
def cmd_chat(config: CogentConfig) -> None:
    try:
        exit_code = asyncio.run(_chat_async(config))
    except KeyboardInterrupt:
        sys.exit(130)
    sys.exit(exit_code)
