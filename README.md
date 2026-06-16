## asyncio协程异步读写与事件循环
客户端可以同步写，但服务器需要处理并发连接和重叠请求，必须异步读写。
async def定义协程函数，await()让出控制权，事件循环调用其他协程函数执行
asyncio没有优先级，FIFO事件循环

await self._send(writer, make_error()...)
先创建make_error()协程函数再挂起当前协程函数
等待return唤醒当前协程函数
## 为什么不使用线程？
1. 任务性质：
这个 daemon 的工作负载是什么
绝大部分时间在等——等 LLM API 返回、等客户端发下一条命令、等用户 permission 响应。没有 CPU 密集计算。
2. 资源浪费：
每个线程需要分配栈内存，线程切换需要上下文切换，这些操作都是成本高昂的。
3. Python 的 GIL：
Python 是单线程的，多线程得不到并行

## 为什么使用JSON-RPC + NDJSON两层协议？
外层封装路由、id、错误处理方法，内层负责业务语义
添加新命令只改内层，外层复用

## 执行过程
用户->cli->server->
agent-runner->agent-loop->
plan(LLM)->act(Tool, readfile)->observe
## 为什么顺序是 observe → act，而不是 act → observe
Anthropic API 的消息格式有严格要求：assistant 的回复必须先出现在历史里，
tool result 作为下一条 user 消息紧随其后。
## 工具调用失败了，循环停不停？
工具调用失败了，循环会继续执行，is_error结果追加进历史，等待LLM要求调用其他工具或结束循环。
## 缓存命中 ？
Anthropic 的 prompt caching 功能：如果相邻两次 API 调用的 system prompt 内容完全相同，
第二次可以直接从缓存里取，不用重新处理，消耗的 token 数大幅减少

## 事件模型
所有事件都是 pydantic 模型，即用 Python 类定义的带类型校验的数据结构
包括 校验（创建时检查参数类型） 序列化  自动类型分配

## writer获取
每个连接建立时 asyncio.start_server 内部创建一个 writer，用于发送数据
每个协程Task有自己的context（python 3.7+），同一连接对应的多个Task的writer是同一个
每个任务将writer存入context，业务处理逻辑中get_connection_writer()从context获取writer

## 重连 ？


## Token 缓冲
LLM 流式生成时，llm.token 事件密集出现，每个只有一两个字符。每个 token 单独调一次 RichLog.write() 会导致屏幕频繁闪烁。
解决方案：收到 llm.token 只追加到内部字符串，不写屏；等到下一个非 token 事件来时，先把积攒的内容整体写入一行