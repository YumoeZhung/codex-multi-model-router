# 迁移、停用与恢复

## 本项目新安装

先结束所有使用路由的任务，保存工作并退出 Codex。

如启用过工作流原生桥，先移除它，让服务重新直接运行 `router.py`：

```sh
python3.11 ~/.codex/skills/multi-model-workflow/scripts/manage.py remove-bridge
```

完整恢复：

```sh
python3.11 manage.py plan-restore
python3.11 manage.py restore
```

恢复只还原本项目安装器拥有的根级 `model`、`model_provider`、`model_catalog_json`、`model_reasoning_summary`，并移除 `model_providers.multi_model_router`。不改账号、项目、MCP、插件、其他 provider 或全局推理强度。安装后若改变了上述拥有字段、备份被改动，或某个 profile 仍使用该路由，则停止自动处理。

停用服务前备份当前配置和 plist。服务停止失败时恢复刚刚修改的配置。成功后删除 LaunchAgent，保留安装目录及备份。需要重装时，先核对无服务、配置已恢复，再将保留目录移出安装位置保存；不要直接删除唯一的恢复记录。

## 仅停用工具发现

```sh
python3.11 manage.py disable-tool-search
python3.11 manage.py enable-tool-search
```

两种操作均保存设置与目录的快照，仅改变两种外部模型的 `supports_search_tool` 和路由转换开关。GPT 目录与窗口配置不变。重启失败会恢复文件并重启原配置。完成后重启 Codex 并新建测试聊天，避免旧进程或旧历史仍携带工具发现项。

停用会增加工具定义输入量。**本项目的关闭行为将 GLM 和 DeepSeek 都改为全量加载**；原第三个会话的逐字节卸载会回到当时 GLM search=true、DeepSeek search=false 的现场。两者不是同一个恢复目标。本项目不伪造其他机器的旧安装记录。

## 已经存在的旧部署

本项目没有自动迁移任何正在运行的旧实例，也不复用它的本地密钥或 install manifest。旧版可能使用 `model-router` / `company-model-router`、不同 provider 和 LaunchAgent 名称。

处理流程：

1. 从当前 TOML 和 LaunchAgent 确认实际路径、provider、端口与启动程序；备份。
2. 若已有 Skill 桥，使用该实例的管理器卸载桥，保留原路由。
3. 使用旧实例自己的恢复说明和备份停用旧服务，确认原生 GPT 可用。
4. 再执行本项目的检查和安装，并完成三模型实际验证。

三个源会话的最后部署附有 `restore_default_route.py` 和 `manage_tool_search.py`；它们依赖那台机器的原始 manifest，不能把这些 manifest 复制到另一台机器。本仓库用统一可移植管理器承接其生命周期职责，原文件位置只属于来源记录。

如果旧配置已经包含后续更改，应基于拥有字段合并恢复，不应拿很久以前的整个 TOML 覆盖现有文件。遇到冲突时保留现状和备份，先核实具体差异。
