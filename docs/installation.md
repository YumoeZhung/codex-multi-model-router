# 安装和本机验收

## 准备配置

1. 原生 Codex 登录并成功运行一次 GPT，生成本机 `models_cache.json`。模型目录中的条目不是账号权限证明。
2. 使用 Python 3.11+。运行 `python3 --version` 核对实际版本，后续命令可替换成该解释器的绝对路径。标准库即可运行，不需要 `pip install`。
3. 将 `config/router.example.json` 复制为仓库根目录的 `router.local.json`。替换两个占位 URL；程序拒绝 `.invalid` 示例地址、含用户名/密码、查询参数或 fragment 的 URL。
4. 网关应接受 `POST <base_url>/responses`，压缩还可能使用 `/responses/compact`。不是将 Chat Completions 自动转换成 Responses 的代理。
5. 若需要网关自己的 Bearer key，配置 `api_key_env` 为变量名，并在执行安装的终端准备该变量。不要把密钥写入源文件或提交 Git。网关无鉴权时保持空字符串。

`context_window`、`effective_context_window_percent` 同时用于两个外部模型；GPT 模型目录完全沿用本机缓存。默认 1M / 95% 来自源部署修复，其他网关需要重新验证。`tool_search=true` 会同步启用两种外部模型的目录声明和路由转换。

```sh
python3.11 manage.py check --settings router.local.json
# 保存工作并退出 Codex 后：
python3.11 manage.py install --settings router.local.json
```

端口默认 17864，可使用 `--port 17865`。安装器拒绝占用端口、已有目标目录、已有自定义 provider 或模型目录。`--codex-home /absolute/path` 可覆盖默认 `CODEX_HOME` / `~/.codex`；后续管理命令必须使用同一值。

代理默认读取安装机器的设置，也可明确指定 `--proxy http://127.0.0.1:PORT`。代理必须能访问实际使用的上游；不要照搬他人端口。URL 中不要嵌入需要公开的凭据。LaunchAgent 记录安装时的代理设置，后续系统代理改变不会自动同步到已运行服务。

## 安装内容

| 位置 | 内容 |
|---|---|
| `$CODEX_HOME/multi-model-router/` | 路由、适配器、生成的模型目录、随机本地密钥、私有配置、事件日志与安装记录 |
| `$CODEX_HOME/config.toml.before-multi-model-router-*` | 安装前的原始配置 |
| `~/Library/LaunchAgents/io.github.codex-multi-model-router.plist` | 当前用户的 launchd 服务 |
| `$CODEX_HOME/config.toml` | 路由 provider、模型目录及必要的根级字段 |

先备份，再启动服务并健康检查，最后修改 Codex provider。安装期间配置被其他进程改动时，撤销安装并保留并发修改。安装器不读取 `auth.json`，不会覆盖其他机器的账号或聊天记录。

## 必须区分的四层验收

1. **文件与进程**：`manage.py status`、服务健康检查。只能证明本地服务启动。
2. **真实推理**：重启 Codex 后分别发起三种模型的短请求。检查回复完成和本机 `events.jsonl` 的模型、路由与 HTTP 状态。
3. **工具与切换**：准备无敏感内容的临时文件；同一聊天依次 GPT → GLM → DeepSeek → GPT，分别读取并返回其中随机标记；确认结果与路由一致。
4. **工具发现与压缩**：使用只读合成 MCP 工具，观察发现、实际调用、第二轮复用、手动压缩后继续执行。某个 HTTP 200 或菜单名称不能代替完整验证。

事件日志的 `transport_complete=true` 只表示转发流正常读完，不等于模型任务成功；还需检查 Codex 最终状态和任务结果。图片、网页搜索、语音、长时间任务和每个第三方插件需要各自验收。

安装期间应退出 Codex，生命周期操作应在任务停止后执行。恢复/开关操作会检查路由当前活动请求，但该检查不能阻止别的进程在检查后发起新请求。
