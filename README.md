# Cogent

Cogent 是一个**双进程本地 AI Agent 系统**：`cogent-core` 作为持久化守护进程运行，`cogent` / `cogent-tui` 作为客户端通过 TCP 连接，使用 JSON-RPC 2.0 + NDJSON 协议通信。

```text
cogent-core (daemon)                    cogent (CLI) / cogent-tui (TUI)
     │                                        │
     └── TCP 127.0.0.1:7437 ◀─────────────────┘
         JSON-RPC 2.0 over NDJSON
```

---

## Quick Start

### 环境要求

- Python **3.12**
- [uv](https://docs.astral.sh/uv/) 包管理器
- [Anthropic API Key](https://console.anthropic.com/)（设为环境变量 `ANTHROPIC_API_KEY`）

### 安装

```bash
# 进入项目目录
cd Cogent

# 同步依赖
uv sync
```

### 启动守护进程

```bash
# 前台运行（Ctrl+C 停止）
uv run cogent-core

# 自定义端口
COGENT_PORT=8000 uv run cogent-core
```

输出示例：

```
cogent-core 0.0.1 listening addr=127.0.0.1:7437
```

### 客户端命令

```bash
# 测试连通性
uv run cogent ping        # → pong（含服务版本与运行时长）

# 查看版本
uv run cogent --version

# 一键任务
uv run cogent run --goal "列出当前目录的文件"

# 多轮对话
uv run cogent chat

# TUI 前端（全功能界面）
uv run cogent-tui

# 守护进程管理
uv run cogent core start   # 后台启动
uv run cogent core status  # 状态查询
uv run cogent core stop    # 停止
```

### 追踪日志

```bash
# 查看全量 trace
uv run cogent trace

# 查看特定 run 的 trace，实时跟随
uv run cogent trace <run_id> --follow

# 按层过滤：ipc / event / llm
uv run cogent trace --layer llm
```