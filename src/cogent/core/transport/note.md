# 协程与事件循环
async def定义协程函数，await()让出控制权，事件循环调用其他协程函数执行
asyncio没有优先级，FIFO

await self._send(writer, make_error()...)
先创建make_error()协程函数再挂起当前协程函数
等待return唤醒当前协程函数