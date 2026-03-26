# Mem0 Agent Setup - Agent 配置指引

> 本文档面向 AI Agent，帮助你理解 Mem0 向量记忆系统的架构、如何使用、以及如何在多 Agent 环境中进行检查和配置。

---

## 一、系统概述

Mem0 向量记忆系统让你的 AI Agent 拥有**跨会话记住用户偏好和重要信息**的能力。

```
用户对话 → session JSONL 文件 → watch 监控 → sync_to_mem0 写入 → Qdrant 向量数据库
                                              ↓
                              每天 04:00 distill 蒸馏精华（memory_distill_daily.py）
                                              ↓
                              每天 03:00 cleanup 清理低分记忆（memory_cleanup.py）
```

**核心能力**：
- 🧠 语义记忆（理解含义，不是关键词匹配）
- 📝 自动同步（对话同时写入，无需人工介入）
- 🔍 智能检索（每次回复前自动搜索相关记忆）
- 🔒 多 Agent 隔离（每个 Agent 记忆互不干扰）
- 📎 来源追溯（每条记忆记录来自哪个 session 文件）

---

## 二、关键文件说明

### 核心脚本（共享目录）

所有 Agent 共用同一套脚本，位于 `/root/.openclaw/mem0-agent-setup/scripts/`：

| 脚本 | 作用 | 触发方式 |
|------|------|---------|
| `watch_sessions.js` | 监听 session 目录变化，触发 sync | systemd 服务自动运行 |
| `sync_to_mem0.py` | 将对话实时写入 Qdrant | watch 调用 |
| `auto_recall.py` | 检索记忆 + session 上下文 | 每次回复前调用 |
| `auto_memory.py` | 手动保存单条记忆 | 手动调用 |
| `memory_distill_daily.py` | 每日蒸馏（对话→精华 block） | cron（每天 04:00） |
| `memory_cleanup.py` | 清理低分记忆 | cron（每天 03:00） |
| `mem0-agent.py` | CLI 工具（stats/status） | 命令行 |

### systemd 服务

| 服务名 | 监控的 session 目录 | 说明 |
|--------|---------------------|------|
| `openclaw-session-watch-main` | `agents/main/sessions` | main agent |
| `openclaw-session-watch-capital` | `agents/capital/sessions` | capital agent |
| `openclaw-session-watch-dev` | `agents/dev/sessions` | dev agent |
| ... | ... | 可扩展 |

### 记忆存储

| 内容 | 存储位置 |
|------|---------|
| 向量数据库 | Qdrant（`localhost:6333`），collection 名 = `mem0_{agent}` |
| session 原始文件 | `/root/.openclaw/agents/{agent}/sessions/*.jsonl` |
| distill 日志 | `/root/.openclaw/workspace/logs/distill_{agent}.log` |

---

## 三、多 Agent 数据流向

```
main 的对话      →  agents/main/sessions    →  watch(main)  →  Qdrant collection: mem0_main
capital 的对话   →  agents/capital/sessions →  watch(capital) →  Qdrant collection: mem0_capital
dev 的对话       →  agents/dev/sessions     →  watch(dev)    →  Qdrant collection: mem0_dev
                                    ↓
              全部使用同一个 .env（同一 API Key + 同一 user_id=fuge）
```

**每个 Agent 的记忆在独立的 collection 中隔离，但共用同一个 Qdrant 实例和同一个 .env。**

---

## 四、环境检查清单

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
cat /root/.openclaw/mem0-agent-setup/.env

# 应该包含：
# OPENAI_API_KEY=sk-xxx
# OPENAI_BASE_URL=https://api.siliconflow.cn/v1
# MEM0_USER_ID=fuge
```

### 4.4 测试记忆是否正常

```bash
# 加载 .env
. /root/.openclaw/mem0-agent-setup/.env

# 搜索记忆
python3 /root/.openclaw/mem0-agent-setup/scripts/auto_recall.py "测试关键词"

# 查看记忆数量
python3 /root/.openclaw/mem0-agent-setup/scripts/mem0-agent.py stats
```

### 4.5 查看日志

```bash
# 查看某个 Agent 的 watch 日志
journalctl -u openclaw-session-watch-main --no-pager -n 30

# 查看 distill 日志
cat /root/.openclaw/workspace/logs/distill_main.log | tail -20
```

---

## 五、新 Agent 如何接入

当执行 `bash install.sh --auto` 时，会自动完成以下步骤：

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | 扫描所有 Agent | 自动发现 `/root/.openclaw/agents/` 下的所有 Agent |
| 2 | 配置 systemd 服务 | 创建 `openclaw-session-watch-{agent}` 并启动 |
| 3 | 配置 cron | 设置 cleanup (03:00) 和 distill (04:00) |
| 4 | 共用 .env | 所有 Agent 共用同一份 `.env` 配置 |

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

**用户标识**，用于在同一个 collection 内区分不同用户的记忆。当前设置为 `fuge`（孚哥），表示所有 Agent 的记忆都属于同一个用户。

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

*最后更新：2026-03-26 by 落雁 🦋*
