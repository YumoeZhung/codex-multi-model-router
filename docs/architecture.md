# 请求路径与协议边界

```mermaid
flowchart TD
    C[Codex 模型菜单 / CLI] --> R[127.0.0.1 本地 Responses 路由]
    R -->|gpt-*，本机登录头| O[ChatGPT Codex 上游]
    R --> C[当前安装 Codex 的 bundled catalog]
    R -->|glm-5-3-flash-260828，独立请求头| A[工具发现适配器]
    R -->|deepseek-v4-1-flash-261017，独立请求头| A
    A --> G[用户配置的 GLM 网关]
    A --> D[用户配置的 DeepSeek 网关]
    S[可选多模型工作流] --> B[可选原生任务消息桥]
    B --> R
```

## 核心路由

路由只绑定 loopback，POST 和受保护的 GET 必须携带本机随机生成的 `X-Codex-Router-Token`。POST 仅允许 `/responses`、`/responses/compact`，`/v1/models` 返回动态合并目录，同时支持 `/v1` 前缀。GPT 上游地址固定为源实现的 ChatGPT Codex 地址；外部模型精确匹配 ID，其他名称拒绝，不静默回退到 GPT。

GPT 沿用本机请求的账号认证头，移除 hop-by-hop 和本地路由密钥。GLM / DeepSeek 重新构造请求头白名单；如配置了网关密钥，只从该网关专属环境变量生成 Authorization。重定向被禁用。

## 动态模型目录

安装器在 `[model_providers.multi_model_router]` 中设置 `model_catalog_url = "http://127.0.0.1:<port>/v1/models"`。Codex 启动时向本机路由请求完整目录，路由以 `codex debug models --bundled` 读取当前安装版本的 GPT 条目，再追加 GLM 5.3 Flash 与 DeepSeek V4.1 Flash。这样升级 Codex 后不需要重新生成静态菜单，也不会继续使用某个历史版本的 GPT 列表。

路由以 Codex 可执行文件的路径、修改时间和大小作为刷新依据，将最近一次成功合并结果写入安装目录的 `models.json`。临时刷新失败会返回这份缓存；首次安装就无法读取时返回 503。缓存是容错，不是版本同步机制，升级后的菜单与真实推理仍需要验收。模型可见性不等于账号授权，订阅到期或工作区限制会由 GPT 上游拒绝。

跨提供方历史兼容移除不可移植的 reasoning 和 item ID，保留普通消息、工具输入输出与 `call_id`。OpenAI 带 encrypted content 的 reasoning 仅在 GPT 路由保留。该历史处理是源方案已有行为；“GPT 不受工具发现适配影响”指新增适配器分支不改 GPT 请求/流，不代表整个代理逐字节透传所有历史。

## 工具发现

`tool_search` 是发现当前未加载工具的 client 协议，不是 `web_search`。

1. 将 Codex 声明的 client `tool_search` 转成普通函数 `codex_local_tool_search`。
2. 外部模型调用该函数后，将 Responses 输出项转成 `tool_search_call`。
3. Codex 完成发现，并在后续历史中送来 `tool_search_output`。
4. 适配器保留工具调用关联，只合并 Codex 实际返回的工具定义，继续交给模型调用。

适配器不搜索、不执行工具、不持久保存聊天。它支持单个 client search 声明、命名空间合并、函数参数分片、JSON 与 SSE 响应。server tool search、名称冲突、无 call_id 的搜索历史会拒绝。单个 SSE frame 的内存上限为 16 MiB。代码执行环境、授权及工具执行仍由 Codex 负责。

## 上下文循环

旧部署曾把 DeepSeek 有效窗口设为 115,200 token，而压缩后的工具定义与历史仍超过该预算，导致立即再次压缩。调整模型目录预算并减少每轮工具声明解决了历史测试中的循环。压缩聊天不一定缩小每次重复附带的工具 schema；不能只看模型标称窗口。

## 可选原生任务桥

桥只替换工作流明确注册、模型/收件人/发件人匹配的 `agent_message`，使用主 Agent 自己登记的明文任务。它不解密消息，不改未注册任务。UUID 任务名、过期时间和单次任务约束避免误匹配；这不是对恶意本机进程的隔离边界。

工具发现适配器和任务桥相互独立：前者解决工具协议，后者解决已登记原生任务的消息传递。Skill 可以单独卸载，保留手动模型菜单路由。
