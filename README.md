# Codex Multi-Model Router

在 macOS Codex 的模型菜单中保留本机 GPT，并接入 **GLM 5.3 Flash** 和 **DeepSeek V4.1 Flash**。项目整合了三次会话最终产物：本地路由、跨提供方历史兼容、多模型工作流、上下文预算修复，以及只作用于 GLM / DeepSeek 的工具发现协议适配。

这是社区自定义适配项目。Codex 的 [provider 与模型目录配置](https://learn.chatgpt.com/docs/config-file/config-reference)提供接入基础，本文中的网关兼容性、安装器和验证结论属于本项目。

## 包含什么

| 组件 | 作用 |
|---|---|
| `router.py` | 仅监听 `127.0.0.1`；按模型分流；隔离 GPT 登录头；保留工具调用关联 |
| `tool_search_adapter.py` | 将 client `tool_search` 转为普通函数，再将结果转回 Codex 协议；支持 SSE 与非流式响应 |
| `models/` | 两个外部模型的目录模板；默认 `max`、1,048,576 上下文、95% 有效比例 |
| `manage.py` | 检查、安装、状态、工具发现开关、配置合并恢复、可选 Skill 安装 |
| `skills/multi-model-workflow/` | 主 Agent 决策与验收，辅助模型可配置；原生或 CLI 派发、限轮限时、失败停止、独立卸载 |
| `tests/`、`docs/evidence/` | 可重跑的离线回归、原会话验证摘要及来源哈希 |

模型 ID 为 `glm-5.3-flash` 与 `deepseek-v4-1-flash-260910`。GPT 条目在安装时从**接收者自己的模型缓存**生成，仓库不附带某个账号的 GPT 模型权限或提示词缓存。

## 安装

要求 macOS、Python **3.11+**、可用的 Codex 桌面与原生 ChatGPT 登录、支持 **Responses API** 的两个网关。运行测试还需要 Git。仅有 Chat Completions 接口的网关不能直接使用。

```sh
cp config/router.example.json router.local.json
# 编辑 router.local.json，填写自己的两个 base_url。
# URL 指向 /responses 之前的前缀，例如 https://gateway.example/v1。
# 网关需要 Bearer key 时填写 api_key_env，值为环境变量名，不要填写密钥。
python3.11 manage.py check --settings router.local.json
```

完成检查后保存工作，退出 Codex，在外部终端安装：

```sh
python3.11 manage.py install --settings router.local.json
python3.11 manage.py status
```

再打开 Codex，新建测试聊天，依次选择 GPT → GLM → DeepSeek → GPT，测试短回复和一次只读工具调用。`/health` 正常只证明路由进程可用。完整验证与代理设置见 [安装和验收](docs/installation.md)。

**如果本机已经装有旧路由，检查会拒绝覆盖。** 本项目整理过程不修改原来正在运行的服务；旧部署迁移请按 [迁移与恢复](docs/recovery.md)处理。

## 可选多模型工作流

```sh
python3.11 manage.py install-skill
```

该命令只复制 Skill，不自动改全局规则、不自动启用原生任务桥。模型选择、桥安装和撤销见 [工作流说明](docs/workflow.md)。辅助模型默认 GLM，可通过配置或每次运行参数改为 DeepSeek；不会把所有子 Agent 固定成同一个模型。

## 恢复

先结束使用路由的任务并退出 Codex。如已启用可选原生任务桥，先按工作流文档移除桥。

```sh
# 仅停用工具发现转换，两种外部模型改为全量工具加载
python3.11 manage.py disable-tool-search
# 再启用
python3.11 manage.py enable-tool-search

# 查看恢复计划；随后停用整个本项目路由，恢复安装前 GPT 设置
python3.11 manage.py plan-restore
python3.11 manage.py restore
```

恢复保留项目、MCP、插件等后续新增设置；本项目拥有的配置字段若被后续修改则停止自动覆盖，要求合并处理。备份和安装目录保留在本机。

## 验证与已知边界

```sh
python3.11 scripts/verify.py
python3.11 scripts/check_release.py
```

- 原会话验证过两种外部模型的工具发现 → 调用 → 压缩 → 继续任务，以及 GPT 协议回归；本次整合重跑的是离线测试与本地 HTTP 模拟上游测试，详见 [验证记录](docs/validation.md)。
- 1M 是当前模板配置，历史最大实测输入分别约为 DeepSeek 340K、GLM 257K，**未测试满 1M**；接入其他网关时应核对它自己的限额。
- 手机端选择 DeepSeek 后绕过本地路由的问题仍未解决；本项目交付范围是 macOS 本地部署。
- 工具发现不是联网搜索；适配器不执行工具、不代替 Codex 授权，也不保证每种第三方工具协议兼容。
- 切换提供方时可见聊天历史和工具结果会发给所选网关。GPT 登录凭据不会发送给 GLM / DeepSeek；若网关使用 HTTP，其链路没有 HTTPS 加密。详见 [数据与凭据](docs/security.md)。

项目来源、演进和本次可移植化改动见 [来源记录](docs/provenance.md)。
