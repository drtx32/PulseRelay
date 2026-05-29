# AstrBot 调研报告与 PulseRelay 仿建建议

> 调研时间: 2026/05/29
> 调研对象: [AstrBot](https://github.com/AstrBotDevs/AstrBot)

---

## 一、AstrBot 核心功能概览

AstrBot 是一个开源的一站式 Agentic 个人和群聊助手，支持 QQ、Telegram、企业微信、飞书、钉钉、Slack、Discord 等 15+ 即时通讯平台，内置 WebUI、插件市场、LLM 对话、Agent 执行、MCP 工具等能力。

### 1.1 核心功能清单

| 功能 | 说明 |
|------|------|
| 多平台消息接入 | 15+ 平台适配器，通过 `@register_platform_adapter` 注册 |
| 消息组件化 | `MessageChain` 支持 Plain/Image/File/Record/Video/At/Reply 等 14 种组件 |
| AI 对话 | 多 LLM 提供商支持（OpenAI/Anthropic/DeepSeek/本地Ollama等） |
| Agent 执行 | ToolLoopAgent + MCP Client + 函数工具 + 子 Agent 委托 |
| 插件生态 | Star 插件系统，1000+ 插件，GitHub 市场，热重载 |
| WebUI | Vue 3 + Vuetify 控制面板 + Web ChatUI |
| 知识库 | 内置知识库系统 |
| 定时任务 | 内置 Cron 调度器 |
| Agent 沙箱 | 隔离代码执行环境 |

---

## 二、架构分析

### 2.1 整体架构

```
外部消息平台 (QQ/TG/飞书/钉钉/...)
         │
         ▼
┌─────────────────────────────────────┐
│  PlatformAdapter (平台适配器)         │
│  - run() 接收消息                    │
│  - send_by_session() 发送消息        │
│  - convert_message() 转为AstrBotMessage│
└────────────────┬────────────────────┘
                 ▼
┌─────────────────────────────────────┐
│  AstrMessageEvent (统一事件)          │
│  - send() / send_streaming()        │
│  - react() 表情反应                  │
│  - 维护会话上下文                    │
└────────────────┬────────────────────┘
                 ▼
┌─────────────────────────────────────┐
│  EventBus (asyncio.Queue)            │
└────────────────┬────────────────────┘
                 ▼
┌─────────────────────────────────────┐
│  MainAgent (Agent 运行时)             │
│  - build_main_agent()                │
│  - ToolLoopAgentRunner               │
│  - Provider 选择 + LLM 调用          │
│  - 工具执行 (本地/MCP/Handoff)       │
└────────────────┬────────────────────┘
                 ▼
┌─────────────────────────────────────┐
│  Star Plugins (插件系统)              │
│  - initialize() / terminate()       │
│  - Handler 注册表                    │
│  - 过滤器链 (Command/Regex/...)     │
└────────────────┬────────────────────┘
                 ▼
┌─────────────────────────────────────┐
│  PlatformAdapter (投递)               │
│  - send_with_client()               │
│  - 流式消息支持 (Telegram等)         │
└─────────────────────────────────────┘
```

### 2.2 消息段架构 (Platform Adapter)

**核心接口 (`astrbot/core/platform/platform.py`):**

```python
class Platform(ABC):
    async def run(self): ...           # 启动连接 (polling/webhook)
    def meta(self) -> PlatformMetadata: # 元数据
    async def terminate(self): ...      # 优雅关闭
    async def send_by_session(self, session, message): ...  # 发送消息
```

**平台元数据 (`PlatformMetadata`):**

```python
@dataclass
class PlatformMetadata:
    name: str                    # "telegram", "discord", ...
    id: str                     # 唯一标识
    support_streaming_message: bool  # 是否支持流式消息
    support_proactive_message: bool  # 是否支持主动推送
```

**标准化消息 (`AstrBotMessage`):**

```python
class AstrBotMessage:
    type: MessageType              # FRIEND_MESSAGE 或 GROUP_MESSAGE
    self_id: str                  # 机器人 ID
    session_id: str               # 会话 ID
    message_id: str               # 平台原始消息 ID
    sender: MessageMember          # 发送者信息
    message: list[BaseMessageComponent]  # 消息链
    message_str: str              # 纯文本版本
    raw_message: object           # 平台原始消息
```

**支持的平台 (15+):**

| 平台 | 目录 | 流式支持 |
|------|------|---------|
| Telegram | `sources/telegram/` | ✅ 完整流式 |
| Discord | `sources/discord/` | ❌ |
| QQ 官方 | `sources/qqofficial/` | ✅ 私聊 |
| OneBot V11 | `sources/aiocqhttp/` | ❌ |
| 飞书 | `sources/lark/` | - |
| 钉钉 | `sources/dingtalk/` | - |
| 企业微信 | `sources/wecom/` | - |
| 微信公众号 | `sources/weixin_official_account/` | - |
| Slack | `sources/slack/` | - |
| KOOK | `sources/kook/` | - |
| LINE | `sources/line/` | - |
| Satori | `sources/satori/` | - |
| Misskey | `sources/misskey/` | - |
| Mattermost | `sources/mattermost/` | - |

### 2.3 消息组件 (MessageComponent)

**组件类型 (`ComponentType`):**

| 类型 | 说明 | 示例 |
|------|------|------|
| `Plain` | 纯文本 | "Hello World" |
| `Image` | 图片 | URL/本地路径/Base64 |
| `Record` | 语音/音频 | 录音消息 |
| `Video` | 视频 | 视频文件 |
| `File` | 文件 | 任意文件 |
| `Face` | QQ 表情 | QQ 表情贴纸 |
| `At` | @提及 | @某个用户 |
| `Reply` | 引用回复 | 回复某条消息 |
| `Forward` | 转发消息 | 消息合并转发 |
| `Poke` | 戳一戳 | 拍一拍 |
| `Share` | 分享链接 | URL 卡片 |
| `Music` | 音乐卡片 | 音乐分享 |
| `Json` | JSON 卡片 | 结构化消息 |

### 2.4 插件系统 (Star)

**目录结构:**

```
astrbot/core/star/
├── star.py              # Star 基类 + StarMetadata
├── base.py             # 基础类
├── context.py          # 插件上下文 (暴露给插件的 API)
├── star_manager.py     # 生命周期管理
├── star_handler.py     # Handler 注册表 + EventType 枚举
├── star_tools.py       # 插件工具集
├── updater.py          # 插件安装/更新
├── register/           # 装饰器注册
│   ├── star.py        # @register_star
│   └── star_handler.py
└── filter/            # 事件过滤器
    ├── command.py
    ├── regex.py
    ├── permission.py
    └── platform_adapter_type.py
```

**插件生命周期:**

```python
class Star(CommandParserMixin, PluginKVStoreMixin):
    async def initialize(self) -> None:  # 激活时调用
    async def terminate(self) -> None:    # 禁用时调用
```

**StarMetadata 关键字段:**

```python
@dataclass
class StarMetadata:
    name: str
    author: str
    desc: str
    version: str
    repo: str                    # GitHub 仓库 URL
    star_cls: type              # 插件类
    activated: bool
    reserved: bool               # 内置插件不可卸载
    support_platforms: list[str]
    astrbot_version: str        # 兼容版本范围
```

**注册机制 (自动注册 via `__init_subclass__`):**

```python
class MyPlugin(Star):
    pass  # 继承即自动注册
```

**Handler 事件类型 (12种):**

- `AdapterMessageEvent` — 收到适配器消息
- `OnLLMRequestEvent` / `OnLLMResponseEvent` — LLM 请求/响应
- `OnAgentBeginEvent` / `OnAgentDoneEvent` — Agent 运行
- `OnCallingFuncToolEvent` — 调用函数工具
- `OnDecoratingResultEvent` — 发送消息前
- `OnPluginLoadedEvent` / `OnPluginUnloadedEvent` — 插件加载/卸载
- 等等...

**插件市场机制:**

```python
# 从 GitHub 下载插件
install_plugin(repo_url, proxy="", download_url="")
  -> updator.download_from_repo_url()
  -> unzip_file()
  -> _ensure_plugin_requirements()  # 安装依赖
  -> load(specified_dir_name)
```

**插件目录结构要求:**

```
plugin_name/
├── main.py              # 或 plugin_name.py
├── metadata.yaml        # 元数据 (必需)
├── requirements.txt     # 依赖 (可选)
├── _conf_schema.json   # 配置 schema (可选)
├── logo.png            # Logo (可选)
└── README.md           # 说明文档
```

### 2.5 Agent 运行时

**核心组件关系:**

```
MainAgent (astr_main_agent.py)
    │
    ├── build_main_agent() → AgentRunner
    │
    ▼
ToolLoopAgentRunner (step() / step_until_done())
    │
    ├── _iter_llm_responses_with_fallback()  # LLM 调用
    ├── _handle_function_tools()              # 工具执行
    │
    ├── Provider (LLM 提供商)
    ├── ToolExecutor (FunctionTool/MCP/Handoff)
    └── ContextManager (上下文压缩/截断)
```

**工具执行器 (`FunctionToolExecutor`):**

```python
execute(tool, run_context, **kwargs)
  ├── HandoffTool → _execute_handoff()  → 子 Agent 委托
  ├── MCPTool → _execute_mcp()          → MCP 协议工具
  ├── is_background_task=True → 后台任务
  └── 普通工具 → _execute_local()        → 本地执行
```

**Provider 选择流程:**

```python
1. 优先使用 event.selected_provider
2. 降级使用平台默认 Provider
3. 支持 fallback_chat_models 配置多个备选
4. 每个 provider 重试最多 3 次
```

### 2.6 WebUI

**技术栈:**

| 类别 | 技术 |
|------|------|
| 框架 | Vue 3.3.4 (Composition API) + TypeScript 5.1.6 |
| UI 库 | Vuetify 3.7.11 (Material Design) |
| 构建 | Vite 6.4.1 |
| 状态 | Pinia 2.1.6 |
| 路由 | Vue Router 4.2.4 |
| HTTP | Axios 1.13.5 |
| 编辑器 | Monaco Editor + Tiptap 富文本 |
| 国际化 | vue-i18n 11.1.5 (zh-CN/en-US/ru-RU) |

**主要页面 (Views):**

| 页面 | 功能 |
|------|------|
| ConfigPage | 核心配置 (33KB) |
| ConversationPage | 会话管理 |
| ExtensionPage | 插件/技能/MCP 管理 (39KB) |
| ProviderPage | AI Provider 配置 (27KB) |
| PlatformPage | 平台配置 (23KB) |
| SessionManagementPage | 会话管理 (60KB，最大) |
| CronJobPage | 定时任务 |
| SubAgentPage | 子代理 |
| PersonaPage | 人格角色 |
| ConsolePage | 控制台 |
| TracePage | 追踪 |

---

## 三、PulseRelay 现状分析

### 3.1 已完成 (Phase 0-5)

| Phase | 内容 | 状态 |
|-------|------|------|
| 0 | 原型稳定化 (WeFlow/Trigger/Bark/FastAPI/WebSocket) | ✅ 完成 |
| 1 | EventEnvelope 事件抽象 | ✅ 完成 |
| 2 | TriggerEngine 迁移到事件模型 | ✅ 完成 |
| 3 | SourceAdapter 架构 | ✅ 完成 |
| 4 | DeliveryHandler 架构 | ✅ 完成 |
| 5 | Router + PolicyEngine | ✅ 完成 |

### 3.2 未完成 (Phase 6-10)

| Phase | 内容 | 状态 |
|-------|------|------|
| 6 | RuntimeDispatcher | ❌ 未开始 |
| 7 | Local Runner Protocol | 📋 规划 |
| 8 | Plugin System | 📋 规划 |
| 9 | Replay/Audit/Persistence | 📋 规划 |
| 10 | Control Plane UI | 📋 规划 |

### 3.3 关键问题

**Router + PolicyEngine 是死代码:**

`gateway.py` 的 `_handle_trigger() 直接硬编码调用 bark_notify() 和 WebSocket，从未调用 Router 或 PolicyEngine。Phase 5 完成但从未激活。

### 3.4 架构对比

| 维度 | PulseRelay | AstrBot |
|------|------------|---------|
| 架构定位 | 轻量级事件中继/通知网关 | 全功能 AI Agent 平台 |
| 消息源 | 仅 WeFlow (1个) | 15+ 平台 |
| 投递出口 | 仅 Bark (1个) | 20+ 投递渠道 |
| AI 集成 | 无 LLM 调用能力 | 完整 Agent 系统 |
| 扩展机制 | 无插件系统 | 1000+ 插件生态 |
| Agent 能力 | 仅路由/策略，无执行 | MCP Client/Tools/Agent Sandbox |
| WebUI | 无 | Vue 3 + Vuetify 控制面板 |
| 多租户 | 无 | 多用户/会话管理 |
| 代码执行 | 无沙箱 | Agent Sandbox |

---

## 四、仿建建议

### 4.1 明确建议：先完成 Phase 6，再搭框架

**理由:**

1. **Router + PolicyEngine 是死代码** — Phase 5 完成了但从未被调用
2. **Phase 6 是打通管道的关键** — 完成后事件才能真正流经完整管道
3. **Plugin 系统需要宿主** — 在管道不工作之前建插件系统，是给空壳搭扩展
4. **Phase 6 完成后每加一个 Source/Delivery 都是增量** — 易于验证

### 4.2 实现顺序

```
现在 ──────────────────────────────────────────────────>
     │
     │ Phase 6: RuntimeDispatcher
     │   - 激活 Router + PolicyEngine
     │   - Job 抽象 + RuntimeDispatcher
     │   - 替换 gateway.py 硬编码路径
     │   - 端到端管道测试通过
     │
     ▼ Phase 7: Plugin System
     │   - PluginManifest + PluginRegistry
     │   - 入口点: sources / deliveries / runtimes / policies
     │   - 热重载 + 沙箱隔离
     │
     ▼ Phase 8: 消息源扩展
     │   - Telegram 适配器
     │   - Slack 适配器
     │   - GitHub Webhook 适配器
     │   - Cron 事件源
     │
     ▼ Phase 9: 投递扩展
     │   - Slack 投递
     │   - 飞书投递
     │   - Email 投递
     │   - Webhook 投递
     │
     ▼ Phase 10: WebUI
         - Vue 3 + Vuetify 控制面板
         - 插件管理界面
         - 事件时间线
         - 审批收件箱
```

### 4.3 Phase 6 详细规划

**目标:** 打通完整事件管道，激活 Router + PolicyEngine

**交付物:**

| 组件 | 说明 |
|------|------|
| `core/runtime.py` | `RuntimeDispatcher` — 作业编排核心 |
| `core/job.py` | `Job` dataclass — 工作单元抽象 |
| `JobRegistry` | 追踪 active/pending/completed 作业 |
| `RuntimeSelector` | 根据 RouteDecision 选择运行时 |
| WebSocket Runtime | OpenClaw 集成的出站 WebSocket 运行时 |
| 管道集成 | gateway.py 移除硬编码，改为 RuntimeDispatcher 调用 |

**验收标准:**

```
[ ] RouteDecision 从 Router 被实际使用
[ ] PolicyEngine.evaluate() 在作业分发前被调用
[ ] Job 状态机: pending → running → completed/failed
[ ] RuntimeDispatcher 可在多个运行时类型间选择
[ ] gateway.py 不再在 _handle_trigger() 里直接调用 bark_notify()
[ ] RuntimeDispatcher 路由逻辑的单元测试通过
```

### 4.4 Phase 7 详细规划 (Plugin System)

**对标 AstrBot Star 系统**

**交付物:**

| 组件 | 说明 |
|------|------|
| `core/plugin/manifest.py` | `PluginManifest` — 插件清单 schema |
| `core/plugin/registry.py` | `PluginRegistry` — 运行时注册表 |
| `core/plugin/context.py` | `PluginContext` — 暴露给插件的 API |
| `core/plugin/loader.py` | 动态加载器 |
| `core/plugin/sandbox.py` | 隔离沙箱 |
| `core/plugin/hot_reload.py` | 热更新机制 |

**入口点:**

```python
@dataclass
class PluginManifest:
    id: str
    name: str
    version: str
    entry_points: list[str]    # ["sources", "deliveries"]
    source_adapters: list[type]    # SourceAdapter 子类
    delivery_handlers: list[type]  # DeliveryHandler 子类
    runtimes: list[type]           # Runtime 子类
    dependencies: list[str]       # 依赖插件
```

**验收标准:**

```
[ ] 插件放入 plugins/ 目录后自动加载
[ ] 新 SourceAdapter 自动出现在 SourceRegistry
[ ] 新 DeliveryHandler 自动出现在 DeliveryRegistry
[ ] 插件可声明对其他插件的依赖
[ ] 插件失败不影响主应用
[ ] WebUI 可列出/启用/禁用插件
```

### 4.5 消息源扩展策略 (Phase 8)

**参考 AstrBot 的平台适配器注册机制:**

```python
# AstrBot 的注册方式
@register_platform_adapter("telegram", "Telegram 适配器")
class TelegramPlatformAdapter(Platform):
    ...

# PulseRelay 可以复用类似机制
class TelegramSource(SourceAdapter):
    manifest = SourceManifest(
        id="telegram",
        name="Telegram Source",
        capabilities=SourceCapabilities(...)
    )
```

**建议优先接入的平台:**

1. **Telegram** — API 完善，调试方便，流式消息支持
2. **Slack** — Socket Mode 支持，WebSocket 实时推送
3. **GitHub Webhook** — 事件驱动，适合作为第一个 Webhook 源

### 4.6 投递扩展策略 (Phase 9)

**参考 AstrBot 的 MessageChain 组件化:**

```
AstrBot: MessageChain = [Plain("..."), Image(...), At(...)]
PulseRelay: 复用 DeliveryMessage 组件化
```

**建议优先实现的投递:**

1. **Slack** — Webhook 简单易实现
2. **飞书** — Webhook 卡片消息
3. **Email** — SMTP 投递
4. **Webhook** — 通用 HTTP 回调

### 4.7 WebUI 策略 (Phase 10)

**技术选型建议:** Vue 3 + Vuetify + TypeScript (与 AstrBot 一致)

**核心功能优先级:**

1. **仪表盘** — 来源/运行时/最近事件总览
2. **事件时间线** — 可视化事件流 + 过滤
3. **来源管理** — 启用/禁用/配置来源
4. **投递管理** — 配置投递处理器
5. **审批收件箱** — 待审批队列
6. **插件管理** — 列出/安装/启用/禁用插件
7. **作业监控** — 活跃作业状态 + 历史

---

## 五、框架集成的两种策略

### 策略 A: Phase 6 优先 (推荐)

```
Phase 6 完成 → 端到端管道可用
     ↓
Phase 7 PluginSystem → 在已验证的管道上扩展
     ↓
Phase 8-10 → 增量功能
```

**优点:**
- 每个阶段都有可验证的交付物
- 降低风险，发现问题早
- 架构迭代有真实数据支撑

**缺点:**
- WebUI/插件系统等"看起来酷"的功能延迟

### 策略 B: 同时搭建框架

```
最小框架:
  core/plugin/  (空壳 PluginManifest + PluginRegistry)
  dashboard/    (Vue 空白项目 + 路由)
  core/registry.py (统一注册中心)
       ↓
Phase 6 照常进行，在框架里填具体实现
```

**优点:**
- 早期就有项目结构感
- 可以并行推进

**缺点:**
- 框架可能需要根据 Phase 6 实装经验重构
- 早期复杂性增加

### 策略选择

**推荐策略 A (Phase 6 优先)**，理由:

1. 当前 Router/PolicyEngine 是死代码，先让它工作
2. Phase 6 的实装经验会告诉你框架真正需要什么
3. 每个阶段的交付物都可测试、可演示
4. AstrBot 的架构也是从"能跑的核心"逐步扩展的

---

## 六、关键架构差异与建议

| 差异点 | AstrBot 方案 | PulseRelay 建议 |
|--------|-------------|----------------|
| 消息组件 | MessageChain 14种组件 | 复用 DeliveryMessage 组件化 |
| 平台注册 | `@register_platform_adapter` 装饰器 | 复用 `SourceAdapter` + `SourceRegistry` |
| 事件对象 | `AstrMessageEvent` 统一收/发 | 分离 `EventEnvelope`(输入) + `DeliveryMessage`(输出) |
| 插件注册 | `__init_subclass__` 自动注册 | 复用 `SourceRegistry` + `DeliveryRegistry` 模式 |
| 配置管理 | `astrbot_config_mgr.py` | 已有 `ConfigLoader`，可扩展 |
| 热重载 | `ASTRBOT_RELOAD=1` + watchfiles | Phase 7 实现 |

---

## 七、Relay 架构设计 (Phase 6 扩展)

> 基于 astrbot_merge.md 调研，PulseRelay 作为**消息中继（Relay）**的架构设计。

### 7.1 Relay 核心定位

```
┌──────────────────────────────────────────────────────────────────────┐
│                         PulseRelay (Relay)                            │
│                                                                      │
│  消息服务 ──────────┐                                                │
│  (WeFlow/SSE/      │     ┌────────────────────────────────────────┐ │
│   Webhook/TG/     │     │            MessageRelay                 │ │
│   Slack...)        │     │                                        │ │
│                    │     │  EventBus ─► SessionManager            │ │
│                    └──► │           ─► Router + PolicyEngine      │ │
│                          │           ─► RuntimeDispatcher          │ │
│                          │           ─► AgentAdapter ─► Agent       │ │
│                          │           ─► DeliveryHandler            │ │
│                          └────────────────────────────────────────┘ │
│                                                                      │
│                          Agent (Claude Code / OpenClaw / MCP)        │
└──────────────────────────────────────────────────────────────────────┘
```

**Relay 的职责：**
- 管理与**消息服务**的连接（保持会话，接收消息）
- 管理与 **Agent 运行时**的连接（保持会话，发送请求）
- 中间做路由、聚合、策略、投递

### 7.2 SourceAdapter 接口扩展

**新增能力：**

```python
class SourceCapabilities:
    supports_websocket: bool = False      # 已有
    supports_webhook: bool = False        # 已有
    supports_streaming: bool = False      # 已有
    supports_ack: bool = False           # 新增：事件确认
    supports_replay: bool = False        # 新增：事件重放
    supports_webhook_reverse: bool = False  # 新增：反向 Webhook
```

**SourceAdapter 扩展接口：**

```python
class SourceAdapter(ABC):
    manifest: SourceManifest
    health: SourceHealth

    # 生命周期
    async def start(self): ...
    async def stop(self): ...
    async def run(self): ...           # 主事件循环

    # 流式接收 (SSE/WebSocket)
    async def on_stream_event(self, data: bytes | str): ...

    # Webhook 接收 (HTTP POST)
    async def on_webhook(self, request: Request) -> Response: ...

    # 确认与重放
    async def ack(self, event_id: str): ...
    async def replay(self, cursor: str, limit: int = 100) -> list[EventEnvelope]: ...

    # 连接管理
    async def connect(self): ...
    async def disconnect(self): ...
    async def reconnect(self): ...

    # 事件发布
    async def emit(self, event: EventEnvelope): ...
```

### 7.3 AgentAdapter 抽象接口

**核心设计：**

```python
@dataclass
class AgentRequest:
    id: str
    prompt: str
    system: str
    messages: list[dict]  # 对话历史
    context: dict        # source, sender, routing info
    temperature: float
    max_tokens: int
    streaming: bool

@dataclass
class AgentResponse:
    id: str
    content: str
    done: bool
    error: str
    delta: str           # 流式增量
    done_reason: str
    usage: dict

class AgentAdapter(ABC):
    manifest: AgentManifest
    health: AgentHealth

    async def connect(self) -> bool: ...
    async def disconnect(self): ...
    async def send_request(request: AgentRequest) -> AgentResponse: ...
    async def send_request_streaming(request: AgentRequest) -> AsyncIterator[AgentResponse]: ...
```

**已实现：**
- `OpenClawAdapter` — WebSocket 连接，支持流式
- `OpenAIAdapter` — API 调用（桩）

**待实现：**
- `ClaudeCodeAdapter` — stdio 子进程，保持持久会话

### 7.4 SessionManager 会话管理

```python
class SessionManager:
    """管理跨平台会话映射"""

    def get_or_create_session(
        self,
        platform: str,
        platform_session_id: str
    ) -> Session

    def resolve_conversation(self, event: EventEnvelope) -> str

    def get_session_history(
        self,
        conversation_id: str,
        limit: int = 50
    ) -> list[EventEnvelope]

class Session:
    id: str
    platform: str
    platform_session_id: str
    state: SessionState  # PENDING / ACTIVE / WAITING / TERMINATED
    created_at: datetime
    last_event_at: datetime
    metadata: dict
```

### 7.5 MessageRelay 核心流程

```
EventBus ─► relay_event() ─► SessionManager ─► Router.route()
                                              │
                                    PolicyEngine.evaluate()
                                              │
                              ┌───────────────┴───────────────┐
                              │                               │
                            blocked                        approved
                              │                               │
                              ▼                               ▼
                      DeliveryHandler                  AgentAdapter.send_request()
                      (rejection msg)                          │
                                                              ▼
                                                   SessionManager.wait_session()
                                                   SessionManager.add_response()
                                                   DeliveryHandler (response)
```

### 7.6 Webhook 服务端设计

```python
class WebhookRegistry:
    """Webhook 端点注册表"""

    def register(self, platform: str, handler: Callable): ...
    
    @property
    def router(self) -> APIRouter: ...

# 路由示例:
# POST /webhook/github
# POST /webhook/feishu
# POST /webhook/dingtalk
# POST /webhook/wecom
```

### 7.7 关键问题与修复计划

**验证阶段发现的关键问题（2026/05/29）：**

| 问题 | 严重度 | 位置 |
|------|--------|------|
| Router.classify() 未被调用 | HIGH | gateway.py:162 |
| PolicyEngine.evaluate() 未被调用 | HIGH | gateway.py:162 |
| RuntimeDispatcher.start() 未调用 | HIGH | gateway.py:229-320 |
| submit_job() 不入队 | HIGH | runtime_dispatcher.py:147-176 |
| 直接调用 agent 绕过 dispatcher | HIGH | gateway.py:162 |
| 无单元测试 | MEDIUM | N/A |

**修复计划：**

```
第一步: 修复 gateway.py 管道
├── 让 _handle_trigger_async 调用 Router.route()
├── 让 _handle_trigger_async 调用 PolicyEngine.evaluate()
├── 让 _handle_trigger_async 调用 RuntimeDispatcher.start()
└── 让 submit_job() 后调用 dispatch_job() 或使用回调

第二步: 补充单元测试
├── test_trigger_engine.py
├── test_router.py
├── test_policy.py
├── test_runtime_dispatcher.py
└── test_agent_adapter.py

第三步: 实现 ClaudeCodeAdapter (stdio)
```

---

## 八、参考资料

- AstrBot 仓库: https://github.com/AstrBotDevs/AstrBot
- AstrBot 文档: https://docs.astrbot.app/
- AstrBot 插件模板: https://github.com/Soulter/helloworld
- AstrBot 插件集合: https://github.com/AstrBotDevs/AstrBot_Plugins_Collection
