# Mem0 Agent Setup - Agent 配置指引

> 本文档面向 AI Agent，帮助你理解 Mem0 向量记忆系统的架构、如何使用、以及如何在多 Agent 环境中进行检查和配置。

---

## 一、系统概述

Mem0 向量记忆系统让你的 AI Agent 拥有**跨会话记住用户偏好和重要信息**的能力。

```
用户对话 → session JSONL 文件 → watch 监控 → sync_to_mem0 写入 → Qdrant 向量数据库
                                              ↓
                              每天 04:00 distill 蒸馏精华
                                              ↓
                              每天 03:00 cleanup 清理低分记忆
```

**核心能力**：
- 🧠 语义记忆（理解含义，不是关键词匹配）
- 📝 自动同步（对话同时写入，无需人工介入）
- 🔍 智能检索（每次回复前自动搜索相关记忆）
- 🔒 多 Agent 隔离（每个 Agent 记忆互不干扰）
- 📎 来源追溯（每条记忆记录来自哪个 session 文件）

---

## 二、关键文件说明

### 2.1 核心脚本（共享目录）

所有 Agent 共用同一套脚本，位于 `/root/.openclaw/mem0-agent-setup/scripts/`：

| 文件 | 作用 | 调用方式 |
|------|------|---------|
| `watch_sessions.js` | 监听 session 目录变化，触发 sync | systemd 服务自动运行 |
| `sync_to_mem0.py` | 将对话实时写入 Qdrant | watch 调用 |
| `auto_recall.py` | 检索记忆（搜索） | 手动或 Agent 启动时调用 |
| `auto_memory.py` | 手动保存单条记忆 | 手动调用 |
| `memory_distill_daily.py` | 每日蒸馏（对话→精华 block） | cron（每天 04:00） |
| `memory_cleanup.py` | 清理低分记忆 | cron（每天 03:00） |
| `mem0-agent.py` | CLI 工具（stats/status） | 命令行 |

### 2.2 配置文件

| 文件 | 位置 | 内容 | 归属 |
|------|------|------|------|
| `.env` | `/root/.openclaw/mem0-agent-setup/.env` | API Key + 用户标识 | **所有 Agent 共用一份** |
| `config.yaml` | `/root/.openclaw/workspace/config.yaml` | Agent 元信息（main only） | 仅 main 使用 |

### 2.3 systemd 服务

| 服务名 | 监控的 session 目录 | 配置文件加载 |
|--------|---------------------|-------------|
| `openclaw-session-watch-main` | `agents/main/sessions` | `.env` |
| `openclaw-session-watch-capital` | `agents/capital/sessions` | `.env`（共用） |
| `openclaw-session-watch-dev` | `agents/dev/sessions` | `.env`（共用） |
| ... | ... | ... |

### 2.4 记忆存储

| 内容 | 存储位置 |
|------|---------|
| 向量数据库 | Qdrant（`localhost:6333`），collection 名 = `mem0_{agent}` |
| session 原始文件 | `/root/.openclaw/agents/{agent}/sessions/*.jsonl` |
| distill 状态文件 | `/root/.openclaw/workspace/.distill_state_{agent}.json` |
| distill 日志 | `/root/.openclaw/workspace/logs/distill_{agent}.log` |

---

## 三、配置引用关系

### 3.1 systemd 服务如何找到脚本和配置

```
systemd service 文件内容：
┌─────────────────────────────────────────────┐
│ [Service]                                    │
│ EnvironmentFile=/root/.openclaw/mem0-agent-  │
│   setup/.env    ← 共用 .env（API Key + user） │
│ ExecStart=/usr/bin/node /root/.openclaw/    │
│   mem0-agent-setup/scripts/watch_sessions.js│
│   {agent}           ← 共享脚本 + 传入 agent 参数 │
└─────────────────────────────────────────────┘
```

### 3.2 watch_sessions.js 如何工作

```
watch_sessions.js main
    ↓
读取 /root/.openclaw/agents/main/sessions/*.jsonl
    ↓
调用 sync_to_mem0.py（通过 stdin 传入消息）
    ↓
sync_to_mem0.py 从 EnvironmentFile 读取：
    - OPENAI_API_KEY（共用 .env）
    - AGENT_NAME=main（systemd 传入）
    - MEM0_USER_ID=fuge（共用 .env）
    ↓
写入 Qdrant collection: mem0_main
```

### 3.3 多 Agent 数据流向

```
main 的对话  →  agents/main/sessions  →  watch(main)  →  Qdrant collection: mem0_main
capital 的对话 →  agents/capital/sessions →  watch(capital) →  Qdrant collection: mem0_capital
dev 的对话    →  agents/dev/sessions    →  watch(dev)    →  Qdrant collection: mem0_dev
                                        ↓
                              全部使用同一个 .env（同一 API Key + 同一 user_id=fuge）
```

**注意**：每个 Agent 的记忆在**独立的 collection** 中隔离，但**共用同一个 Qdrant 实例和同一个 .env**。

---

## 四、多 Agent 环境检查清单

### 4.1 检查 systemd 服务状态

```bash
# 查看所有 Agent 的 watch 服务
systemctl list-units --all "*session-watch*" 2>/dev/null | grep openclaw

# 查看某个 Agent 是否在运行
systemctl is-active openclaw-session-watch-main
systemctl is-active openclaw-session-watch-capital
```

**正常状态**：所有服务显示 `active (running)`

### 4.2 检查 cron 任务

```bash
# 查看所有记忆相关 cron
crontab -l 2>/dev/null | grep memory

# 应该看到两类（per agent）：
# 03:00 memory_cleanup.py
# 04:00 memory_distill_daily.py --agent {agent}
```

### 4.3 检查 .env 配置

```bash
# 查看共用配置文件
cat /root/.openclaw/mem0-agent-setup/.env

# 应该包含：
# OPENAI_API_KEY=sk-xxx
# OPENAI_BASE_URL=https://api.siliconflow.cn/v1
# MEM0_USER_ID=fuge
```

### 4.4 测试记忆是否正常

```bash
# 先加载 .env
. /root/.openclaw/mem0-agent-setup/.env

# 查看记忆数量
python3 /root/.openclaw/mem0-agent-setup/scripts/mem0-agent.py stats

# 搜索记忆
python3 /root/.openclaw/mem0-agent-setup/scripts/auto_recall.py "测试关键词"

# 手动保存记忆
python3 /root/.openclaw/mem0-agent-setup/scripts/auto_memory.py "用户消息" "助手回复"
```

### 4.5 查看日志

```bash
# 查看某个 Agent 的 watch 日志
journalctl -u openclaw-session-watch-main --no-pager -n 30

# 正常日志示例：
# Agent: main
# Watching: /root/.openclaw/agents/main/sessions
# Found 7 session files
# Watching for new messages...

# 查看 distill 日志
cat /root/.openclaw/workspace/logs/distill_main.log | tail -20
```

---

## 五、新 Agent 如何接入 Mem0 系统

如果你是新创建的 Agent（已有独立 workspace），**无需额外配置**！

当主控执行 `bash install.sh --auto` 时，会自动完成以下步骤：

### 5.1 自动完成的事情（对新 Agent）

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | 扫描所有 Agent | 自动发现 `/root/.openclaw/agents/` 下的所有 Agent |
| 2 | 配置 systemd 服务 | 创建 `openclaw-session-watch-{agent}` 并启动 |
| 3 | 配置 cron | 设置 cleanup (03:00) 和 distill (04:00) |
| 4 | 共用 .env | 所有 Agent 共用同一份 `.env` 配置 |

**你只需要确保**：如果你的 workspace 需要独立配置（比如 capital 有自己的 API Key），在安装后手动修改 `/root/.openclaw/mem0-agent-setup/.env`。

### 5.2 如果需要手动干预

#### 情况 A：你的 Agent 想用独立的 API Key
修改共用配置会被所有 Agent 共享。如果需要独立：
1. 单独创建一个 `.env` 文件在你的 workspace
2. 修改你的 systemd service 的 `EnvironmentFile` 指向你的独立配置

#### 情况 B：你想立即测试记忆功能

```bash
# 加载 .env
. /root/.openclaw/mem0-agent-setup/.env

# 搜索关于你的记忆
python3 /root/.openclaw/mem0-agent-setup/scripts/auto_recall.py "你的名字"

# 查看你的 Agent 的记忆数量
python3 /root/.openclaw/mem0-agent-setup/scripts/mem0-agent.py stats
```

#### 情况 C：你的 Agent 想看 session 被监听的状态

```bash
# 查看对应服务状态
systemctl status openclaw-session-watch-{your_agent_name}

# 查看日志
journalctl -u openclaw-session-watch-{your_agent_name} --no-pager -f
```

---

## 六、架构决策说明

### 为什么要共用 .env？

- **单用户场景**：所有 Agent 服务同一个人，不需要按用户隔离
- **简化部署**：一个配置文件搞定所有 Agent
- **成本最优**：同一个 API Key，按用量计费

### collection 按 Agent 隔离的意义？

每个 Agent 有独立的 collection（`mem0_main` / `mem0_capital` 等），保证：
- 不同 Agent 的记忆互不污染
- 每个 Agent 的 distill/cleanup 只处理自己的数据
- 出现问题时隔离影响范围

### MEM0_USER_ID = fuge 的含义？

这是**用户标识**，用于在同一个 collection 内区分不同用户的记忆。当前设置为 `fuge`（孚哥），表示所有 Agent 的记忆都属于同一个用户，检索时会汇总该用户的所有记忆。

---

## 七、快速参考

```bash
# 检查服务状态
systemctl status openclaw-session-watch-{agent}

# 重启服务
systemctl restart openclaw-session-watch-{agent}

# 查看日志
journalctl -u openclaw-session-watch-{agent} -f

# 搜索记忆
. /root/.openclaw/mem0-agent-setup/.env && \
python3 /root/.openclaw/mem0-agent-setup/scripts/auto_recall.py "关键词"

# 手动触发 distill（dry run）
. /root/.openclaw/mem0-agent-setup/.env && \
python3 /root/.openclaw/mem0-agent-setup/scripts/memory_distill_daily.py \
  --agent {agent} --dry-run
```

---

*最后更新：2026-03-25 by 落雁 🦋*

---

## Active Recall 与 Context 拼接（v6 架构）

### 核心改进（相比 v5）

1. **扁平分组输出**：每层定义只出现一次，block 用 ` | ` 分隔独立成行
2. **Session 上下文内联**：原始 session 片段直接跟在 block 后面，不再嵌套缩进
3. **严谨性**：空值保护、异常捕获、超长文本截断
4. **可拓展性**：常量可配置、层级定义外部化、collection 自动检测

### 三层层级定义

| 层级 | 定义 | 触发场景 |
|------|------|---------|
| **Semantic 语义层** | "回答请符合用户偏好、沟通习惯、语言风格" | 涉及用户喜好、沟通方式 |
| **Episodic 事件层** | "回答请参考用户的历史决策、重大事件" | 涉及项目进展、决策历史 |
| **Procedural 程序层** | "回答请遵循用户认可的工作流程和操作步骤" | 涉及操作流程、方法论 |

### Block 存储格式（蒸馏时）

```
[层级:Episodic][score:5][distilled][sessions:2][files:/path/to/s1.jsonl,/path/to/s2.jsonl]
用户提到项目ABC需要在周五前完成测试报告
```

> 注意：层级定义不存进 block，recall 时由 `LAYER_DEFINITIONS` 硬编码注入。

### Recall 输出格式（v6）

```
## 📚 相关记忆

回答请参考用户的历史决策、重大事件：
  [事件]用户提到项目ABC需要在周五前完成测试报告 [score=5] | [session_xxx.jsonl]: 👤 用户说了什么 | 🤖 助手回复

回答请遵循用户认可的工作流程和操作步骤：
  [程序]修复了auto_recall.py的collection硬编码问题 [score=4] | [session_yyy.jsonl]: 🔧 工具结果
```

**格式规则：**
- 每层定义只出现一次（不是每个 block 都重复）
- 同一层内每条 block 独立一行，用 ` | ` 连接 block 和 session 上下文
- Session 上下文格式：`[filename]: icon text | icon text`
- 层级前缀：`[语义]` / `[事件]` / `[程序]`

### Agent ID 自动检测（v6 新增）

auto_recall.py 启动时自动检测当前 agent，优先级：

```
1. AGENT_NAME 环境变量（systemd watchdog 场景）
2. WORKSPACE_DIR/.env（gateway agent 场景）
3. 遍历已知 workspace 目录查找 .env
4. fallback: main
```

**每个 workspace 有独立的 `.env` 文件：**
```
/root/.openclaw/workspace/.env          → AGENT_ID=main
/root/.openclaw/workspace-capital/.env  → AGENT_ID=capital
/root/.openclaw/workspace-dev/.env     → AGENT_ID=dev
```

### 实施文件

- `memory_distill_daily.py` — 蒸馏时增加层级分类
- `auto_recall.py` — 扁平分组输出 + session 上下文内联 + agent ID 自动检测
