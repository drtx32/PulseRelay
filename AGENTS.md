# PulseRelay 项目约定

## 项目概览
- 这是一个基于 `FastAPI` 的消息中转与聚合服务。
- 核心流程是：微信消息数据源进入 `Signals.queue`，由 `TriggerEngine` 聚合判断触发条件，再转发到 OpenClaw，并可选发送 Bark 通知。
- 根目录的 `gateway.py` 是主入口；`base.py` 定义数据源抽象；`core/`、`sources/`、`handlers/` 分别放触发逻辑、数据源实现和通知处理器。

## 目录职责
- `gateway.py`：FastAPI 应用、WebSocket 代理、触发调度、模板渲染、Bark 通知、健康/调试接口。
- `base.py`：`Signals` 和 `Module` 基类，所有数据源都应通过这里统一接入。
- `core/trigger_engine.py`：消息聚合、去重、触发条件判断、统计信息输出。
- `sources/wechat.py`：WeFlow SSE 数据源，实现微信消息消费与字段适配。
- `handlers/bark.py`：Bark 推送封装。
- `scripts/wx_monitor.py`：旧式监控脚本，通过 `wx monitor --json` 读取消息并回传到网关。
- `templates/`：Jinja2 消息模板。
- `assets/`：静态资源。

## 运行方式
- 本项目使用 Python 运行，依赖见 `requirements.txt`。
- 启动主服务通常使用：
  - `uvicorn gateway:app --reload --port 8000`
- 运行前需要在仓库根目录放置 `.env`，可参考 `.env.example`。
- 典型配置项包括：
  - `WS_HOST`、`WS_PORT`、`WS_PATH`
  - `SENDER_ID`、`SENDER_NAME`
  - `WS_TOKEN`
  - `BARK_DEVICE_KEY`
  - `WEFLOW_HOST`、`WEFLOW_PORT`、`WEFLOW_TOKEN`
  - `WX_MONITOR_CHATS`
  - `WX_CONTENT_THRESHOLD`、`WX_MESSAGE_THRESHOLD`、`WX_IDLE_TIMEOUT`
  - `WX_TEMPLATE`

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
  - 微信消息是否能正常进入聚合和触发流程
- 如果要删除文件，只能逐个删除明确路径的文件，不要做批量删除或递归删除。
- 不要使用以下命令进行批量清理：
  - `del /s`
  - `rd /s`
  - `rmdir /s`
  - `Remove-Item -Recurse`
  - `rm -rf`

## 变更建议
- 优先小步修改，保持 `gateway.py`、`core/trigger_engine.py`、`sources/wechat.py` 之间的数据流清晰。
- 如果需要新增接口，尽量和现有风格一致，返回结构简单明确。
- 如果修改消息格式，请同步检查模板 `templates/wx_template_example.j2` 和 `/stats` 的展示内容。
