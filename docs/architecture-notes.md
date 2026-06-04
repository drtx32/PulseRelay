# 多入口 / 多出口 / 多 Agent 架构笔记

> 这份文档整理自当前关于“入口聚合、统一中转、Agent 路由、按出口回传”的讨论，属于设计草案，不是当前代码的强制约定。

## 目标
- 将来自不同来源的消息统一接入同一条内部消息流。
- 将内部消息流送入一个或多个具备记忆能力的 Agent。
- 根据预设路由，把 Agent 的结果输出到不同出口。
- 保持入口、Agent、出口之间解耦，方便后续扩展。

## 核心原则
- 入口只负责“接入”和“标准化”，不负责业务编排。
- Agent 只负责“理解、记忆、推理和生成结果”。
- 出口只负责“把结果送到目标平台”。
- 中间层使用统一的数据模型，不直接绑定某个协议。

## 推荐抽象

### 1. Ingress Channel
入口通道，负责把外部消息转成统一事件。

可选类型：
- `webhook`
- `websocket`
- `sse`
- `polling`

职责：
- 建立连接或监听回调
- 验证签名或 token
- 去重、基础清洗、字段适配
- 统一输出 `UnifiedEvent`

### 2. Agent
Agent 是消息处理核心，建议按“一个实例一个配置”管理。

可选类型：
- `openclaw`
- `hermes`

职责：
- 接收统一事件
- 维护会话记忆
- 生成回复、摘要、任务或动作建议
- 返回统一动作 `UnifiedAction`

### 3. Egress Channel
出口通道，负责把动作送往目标平台。

可选类型：
- `websocket`
- `webhook`
- `http`
- `bark`

职责：
- 把统一动作转换成目标平台格式
- 执行重试、超时与失败降级
- 必要时支持通知类输出

## 推荐配置模型

### 存储方式
- 使用 SQLite 作为主配置存储，适合 Web UI 动态编辑、版本回滚和查询。
- 配合 JSON 字段保存各 adapter 的私有配置。
- 复杂的大段模板或提示词，也可以单独保存为 JSON 文本。

### 建议表
- `channels`
- `agents`
- `routes`
- `sessions`

### 典型字段
- `id`
- `name`
- `type`
- `enabled`
- `direction`
- `config_json`
- `match_json`
- `updated_at`

## 路由方式

### 路由流程
1. 入口收到事件。
2. 标准化成 `UnifiedEvent`。
3. 路由器按优先级匹配规则。
4. 选择目标 Agent。
5. Agent 产出 `UnifiedAction`。
6. 路由器把动作交给一个或多个出口。

### 路由建议
- 使用优先级数字控制匹配顺序，数字越小优先级越高。
- 支持按来源、群组、用户、关键词、标签、事件类型过滤。
- 支持 fallback Agent 或 fallback 出口。

## 记忆与会话
- 记忆能力建议由 Agent 自己维护，但中转站要负责 session key 的生成。
- session key 可以按 `platform + conversation_id + user_id` 组合生成。
- 如果需要跨平台统一会话，可以把映射关系单独存进 `sessions`。

## 热重载
- 配置变更后，不建议整进程重启。
- 推荐做法是对比旧配置和新配置，按差异执行：
  - 新增实例：创建并打开
  - 删除实例：关闭并释放
  - 修改实例：重建或 reload
- 路由表和内存缓存应支持刷新。

## 与当前项目的关系
当前仓库已经有一些可直接复用的思想：
- `Signals` 负责统一消息队列
- `Module` 适合作为入口适配器基类
- `TriggerEngine` 体现了“先聚合再触发”的思想
- `gateway.py` 已经承担了网关、转发和通知职责

未来如果要扩展成更通用的多入口中转站，可以沿着下面的方向演进：
- 将 `Signals` 扩展为更明确的统一事件总线
- 将 `sources/` 扩展为多种 ingress adapter
- 将 `handlers/` 扩展为多种 egress adapter
- 把路由规则和 Agent 配置从环境变量迁移到 SQLite 或 JSON 配置

## 最小可行版本
如果只做最小可用版本，建议先保留这三类能力：
- 一个 webhook 入口
- 一个 websocket Agent 调用通道
- 一个 webhook 或 Bark 出口

先跑通“多入口进来，统一路由到 Agent，再按策略回传”的闭环，再逐步增加更多入口和出口。


## 当前实现约束补充
- `templates/` 中的模板用于构建聚合后的统一消息，不绑定微信；WeFlow/微信模板只是示例。
- WeFlow 是当前微信消息接入方式，源码命名使用 `sources/weflow.py`。
- 当前聚合规则（最大消息条数、最大字符数、两条消息最大间隔/空闲超时）默认对所有消息源适用；后续多 Agent 路由可以按 Agent 或 route 覆盖这些规则。
- Redis 可作为后续聚合状态存储和符合条件后的 push 通道；当前最小实现仍使用进程内 `TriggerEngine` 状态，并在触发后推送到 WebSocket Agent。

- 当前配置先采用 OpenClaw 风格的单文件 JSON（默认 `data/pulserelay.json`），便于复制、回滚和后续迁移到 SQLite。
- 飞书/Lark 当前作为 webhook ingress 接入，默认路径是 `/sources/lark/events`，支持 URL verification 和 `im.message.receive_v1` 与旧版 `message` 消息事件适配；加密回调先显式返回未支持，后续可引入 AES 依赖完善。
