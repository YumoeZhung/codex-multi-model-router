# 可选多模型工作流

本目录中的 Skill 是第二个源会话的完整产物，版本 0.2.0。代码和测试保留；[原始验证说明](../skills/multi-model-workflow/tests/VALIDATION.md)属于当时的历史记录，其“DeepSeek 未验收”等描述以当时范围为准，不能替代后续集成结果。

## 使用边界

主 Agent 先判断任务是否明确、结果是否容易核验、错误后果是否可控、交接是否划算。辅助模型可取证、提出反例、扩充候选或执行明确任务，最终判断与验收留给主 Agent。模型意见一致不构成正确性证据。

默认辅助模型与强度来自 Skill 的 `defaults.json`，再由 `$CODEX_HOME/multi-model-workflow.json` 或每次 `--assistant-model` 覆盖。精确可用 ID 包括 `glm-5.3-flash`、`deepseek-v4-1-flash-260910`。默认 3 轮、单次 300 秒、总计 1200 秒；失败不自动换 GPT。

## 安装与启用

```sh
python3.11 manage.py install-skill
```

已存在同名 Skill 时拒绝覆盖。安装后直接阅读安装目录的 `SKILL.md`，遵循其中的输入契约、证据引用、轮数状态与独立验收规则。

如果确实需要全局委派规则，以下命令只添加带归属标记的一段规则：

```sh
python3.11 ~/.codex/skills/multi-model-workflow/scripts/manage.py enable-policy
```

原生任务消息兼容桥需要单独启用。先结束路由中的任务，再执行：

```sh
python3.11 ~/.codex/skills/multi-model-workflow/scripts/manage.py install-bridge \
  --service ~/Library/LaunchAgents/io.github.codex-multi-model-router.plist
```

桥在新路由入口外包一层，继续传递 `--settings` 等参数，不改 provider、模型目录和 router 源码。操作后需实测一次只读原生委派，检查模型、路由、任务传递和实际结果，不以进程健康代替验收。

使用自定义 `CODEX_HOME` 时，在上述 Skill 管理命令显式传入 `--home /absolute/path`，并调整 Skill 的路径。工作流执行器读取同一 `CODEX_HOME`。

## 执行

原生与 CLI 只能为同一子任务选择一条通道。原生入口先生成注册记录，再使用宿主派发工具；不能把脚本准备成功当成辅助 Agent 已执行。CLI 入口在本机 Codex CLI 进程中运行，提供子进程超时控制。

详细命令与输入范例位于：

- [SKILL.md](../skills/multi-model-workflow/SKILL.md)
- [输入和决策契约](../skills/multi-model-workflow/references/contracts.md)
- [原生派发与桥](../skills/multi-model-workflow/references/native-dispatch.md)
- [委派规则](../skills/multi-model-workflow/references/delegation-policy.md)

原生子 Agent 继承宿主权限，“只读”依赖指令及验收，不是 OS 强制隔离；CLI 才使用对应的 shell sandbox。调用方必须处理原生任务超时后的中断与存活检查。

## 卸载，保留手动菜单路由

所有工作流必须已完成或明确放弃，并确认辅助进程已停止：

```sh
python3.11 ~/.codex/skills/multi-model-workflow/scripts/manage.py uninstall
```

恢复原服务启动参数，移除自己添加的全局规则，将 Skill 和运行记录归档。用户后续的无关修改保留；有冲突或未结束任务时拒绝。运行目录中的 `remove_skill.py` 可在 Skill 文件夹丢失时使用。

单独卸载 Skill 后，三模型手动菜单和工具发现适配继续存在。若还要停用整个路由，再执行仓库根目录的 `manage.py restore`。
