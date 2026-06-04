# PulseRelay 项目约定

## 项目概览
- 这是一个基于 `FastAPI` 的消息中转与聚合服务。
- 核心流程是：WeFlow 等消息数据源进入 `Signals.queue`，由 `TriggerEngine` 聚合判断触发条件，再转发到 OpenClaw，并可选发送 Bark 通知。
- 根目录的 `gateway.py` 是主入口；`base.py` 定义数据源抽象；`core/`、`sources/`、`handlers/` 分别放触发逻辑、数据源实现和通知处理器。

## 目录职责
- `gateway.py`：FastAPI 应用、WebSocket 代理、触发调度、模板渲染、Bark 通知、健康/调试接口。
- `base.py`：`Signals` 和 `Module` 基类，所有数据源都应通过这里统一接入。
- `core/trigger_engine.py`：消息聚合、去重、触发条件判断、统计信息输出。
- `sources/weflow.py`：WeFlow SSE 数据源，实现消息消费与字段适配。
- `sources/lark.py`：飞书/Lark webhook 数据源，实现事件订阅回调、URL verification 和消息字段适配。
- `config.py`：JSON 配置加载与旧环境变量兼容覆盖。
- `handlers/bark.py`：Bark 推送封装。
- `scripts/wx_monitor.py`：旧式监控脚本，通过 `wx monitor --json` 读取消息并回传到网关。
- `templates/`：Jinja2 消息模板。
- `assets/`：静态资源。

## 运行方式
- 本项目使用 Python 运行，依赖见 `requirements.txt`。
- 启动主服务通常使用：
  - `uvicorn gateway:app --reload --port 8000`
- 运行前可在仓库根目录放置 `.env`，可参考 `.env.example`；`.env` 现在主要用于指定 `PULSERELAY_CONFIG` 或兼容旧部署。
- 运行配置默认读取 `data/pulserelay.json`，也可用 `PULSERELAY_CONFIG` 指向私有 JSON 配置文件。
- JSON 配置主要分区包括：
  - `openclaw`：OpenClaw WebSocket 地址、sender 和 token。
  - `handlers.bark`：Bark device key。
  - `aggregation`：最大字符数、最大消息条数、空闲超时、最小触发间隔和监控会话。
  - `templates.message`：聚合消息模板路径。
  - `sources.weflow`：WeFlow SSE 连接配置。
  - `sources.lark`：飞书/Lark 事件订阅 webhook 配置。
- 旧环境变量仍可作为覆盖项使用，便于兼容已有部署。

## 编码约定
- 尽量保持现有风格：模块内直接用轻量函数与类，不要无必要地重构为复杂框架。
- 新增数据源时，应继承 `base.Module`，通过 `Signals.put()` 发送标准化消息。
- 新增消息字段时，优先在数据源层完成适配，再交给 `TriggerEngine`。
- 触发判断相关改动应优先关注：
  - 去重键是否稳定
  - `min_trigger_interval` 是否仍然合理
  - 统计接口 `/stats` 是否保持一致
- 模板渲染默认使用 Jinja2；若模板渲染失败，代码会回退到默认格式，修改时要保留这个兜底逻辑。

## 修改时的注意事项
- 不要把敏感值写死进代码，尤其是 token、device key、WebSocket 地址。
- 修改 `.env.example` 时要保持示例值无害、可复制。
- 这个仓库没有现成测试时，改动核心逻辑后至少手动检查：
  - `gateway.py` 能否启动
  - `/stats`、`/ws-url`、`/notify` 是否可用
  - WeFlow / Lark 消息是否能正常进入聚合和触发流程
- 如果要删除文件，只能逐个删除明确路径的文件，不要做批量删除或递归删除。
- 不要使用以下命令进行批量清理：
  - `del /s`
  - `rd /s`
  - `rmdir /s`
  - `Remove-Item -Recurse`
  - `rm -rf`

## 变更建议
- 优先小步修改，保持 `gateway.py`、`core/trigger_engine.py`、`sources/weflow.py`、`sources/lark.py` 之间的数据流清晰。
- 如果需要新增接口，尽量和现有风格一致，返回结构简单明确。
- 如果修改消息格式，请同步检查模板 `templates/message_summary_example.j2` 和 `/stats` 的展示内容。
