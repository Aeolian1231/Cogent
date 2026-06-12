# bus/（总线）目录
定义了 JSON-RPC 协议、事件命令相关类，用于json序列化和反序列化
作用：防错（拼写错误、类型检查）、文档化

envlope.py：定义了 JSON-RPC 协议类，用于双端构造和解析、错误处理
commands.py：定义了 commands命令类，用于服务端解析调用命令
events.py：定义了 events事件类，目前只用于服务端构造，客户端使用raw dict裸解析（取type字段），没有使用单序列化
