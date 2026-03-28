# Mem0 Agent Setup - Agent 配置指引

> 本文档面向 AI Agent，帮助你理解 Mem0 向量记忆系统的架构、如何使用、以及如何在多 Agent 环境中进行检查和配置。

---

## 一、系统概述

Mem0 向量记忆系统让你的 AI Agent 拥有**跨会话记住用户偏好和重要信息**的能力。

```
用户对话 → session JSONL 文件 → watch 监控 → sync_to_mem0 写入 → Qdrant 向量数据库
                                              ↓
              每天 04:00-04:25 分批 distill（memory_distill_daily.py v4）
                                              ↓
                              每天 03:00 cleanup（memory_cleanup.py）
```

**核心能力**：
- 🧠 语义记忆（理解含义，不是关键词匹配）
- 📝 自动同步（对话同时写入，无需人工介入）
- 🔍 智能检索（每次回复前自动搜索相关记忆）
- 🔒 多 Agent 隔离（每个 Agent 记忆互不干扰）
- 📎 来源追溯（每条记忆记录来自哪个 session 文件）
- ⚡ Per-session 断点续传（v4，不重复处理同一 session）

---

## 二、关键文件说明

### 核心脚本（共享目录）

所有 Agent 共用同一套脚本，位于 `/root/.openclaw/mem0-agent-setup/scripts/`：

| 脚本 | 作用 | 触发方式 |
|------|------|---------|
| `watch_sessions.js` | 监听 session 目录变化，触发 sync | node 进程（自动运行） |
| `sync_to_mem0.py` | 将对话实时写入 Qdrant | watch 调用 |
| `auto_recall.py` | 检索记忆 + session 上下文 | 每次回复前调用 |
| `auto_memory.py` | 手动保存单条记忆 | 手动调用 |
| `memory_distill_daily.py` | 每日蒸馏（对话→精华 block，v4） | cron（每天 04:00-04:25） |
| `memory_cleanup.py` | 清理低分记忆 | cron（每天 03:00） |
| `mem0-agent.py` | CLI 工具（stats/status） | 命令行 |

### 运行中的 watch 进程

```
main: 3个进程 (Mar24启动)
capital, dev, legal, ops, bingbu, hubu, gongbu, libu, libu_hr, menxia, rich,
shangshu, taizi, xingbu, zaochao, zhongshu: 各1个进程 (Mar25启动)
```

共 17 个 agent，全部在监听。

### 记忆存储

| 内容 | 存储位置 |
|------|---------|
| 向量数据库 | Qdrant（`localhost:6333`），collection 名 = `mem0_{agent}` |
| session 原始文件 | `/root/.openclaw/agents/{agent}/sessions/*.jsonl` |
| distill 状态文件 | `/root/.openclaw/workspace/.distill_state_main.json`（main）|
| | `/root/.openclaw/workspace-{agent}/.distill_state.json`（其他agent）|

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

### 4.1 检查 watch 进程状态

```bash
# 查看所有 agent 的 watch 进程
ps aux | grep watch_sessions | grep -v grep

# 应该看到 17+ 个进程在运行
```

### 4.2 检查 cron 任务

```bash
openclaw cron list | grep -E "记忆蒸馏|记忆清理"
```

**正常状态**：看到 1 个 cleanup + 多个 distill cron，状态为 idle/in progress

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

### 4.5 检查向量库状态

```bash
# 查看所有 collection 的 point 数量
curl -s http://localhost:6333/collections | python3 -c "
import sys,json
d=json.load(sys.stdin)
for c in d['result']['collections']:
    name=c['name']
    if name.startswith('mem0_'):
        import requests
        r=requests.get(f'http://localhost:6333/collections/{name}')
        cnt=r.json()['result']['points_count']
        print(f'{name}: {cnt} points')
"
```

### 4.6 检查 distill 状态

```bash
# 查看某 agent 的 distill 状态
cat /root/.openclaw/workspace/.distill_state_main.json
cat /root/.openclaw/workspace-capital/.distill_state.json
```

---

## 五、Cron 任务详解

### 每日分批时间表

| 时间 | 任务 | Agent |
|------|------|-------|
| 03:00 | 记忆清理 | main |
| 04:00 | 记忆蒸馏 | main, capital, dev |
| 04:05 | 记忆蒸馏 | bingbu, gongbu |
| 04:10 | 记忆蒸馏 | legal, ops |
| 04:15 | 记忆蒸馏 | libu_hr, menxia, rich |
| 04:20 | 记忆蒸馏 | xingbu |
| 04:25 | 记忆蒸馏 | zaochao, zhongshu, shangshu, taizi, hubu, libu |

### memory_distill_daily.py v4 参数

```bash
python3 memory_distill_daily.py \
  --agent <agent_id>      # agent ID（必需）
  --force                  # 强制全量处理（跳过断点）
  --yes                    # 跳过确认直接写入
  --dry-run                # 只蒸馏，不写入
  --cleanup                # 清理过期 session 记录（配合 --days）
  --days <N>               # 清理 N 天前未活跃的 session 记录
  --batch-size <N>         # 每批处理多少条对话（默认80）
```

### 断点续传逻辑

v4 按 session 文件+行数追踪进度，不再按时间戳：

```json
{
  "sessions": {
    "session_uuid.jsonl": {
      "processed_lines": 142,    // 已处理到这个行数
      "distilled_at": "2026-03-28T04:30:19"
    }
  },
  "global_last_run": "2026-03-28T04:35:00"
}
```

---

## 六、新 Agent 如何接入

当执行 `bash install.sh --auto` 时，会自动完成以下步骤：

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | 扫描所有 Agent | 自动发现 `/root/.openclaw/agents/` 下的所有 Agent |
| 2 | 启动 watch 进程 | 每个 agent 一个 node 进程 |
| 3 | 配置 cron | 设置 cleanup (03:00 main) 和 distill (分批 04:00-04:25) |
| 4 | 共用 .env | 所有 Agent 共用同一份 `.env` 配置 |

---

## 七、架构决策说明

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

## 八、快速参考

```bash
# 检查 watch 进程
ps aux | grep watch_sessions | grep -v grep | wc -l

# 查看某 agent 的 distill 状态
cat /root/.openclaw/workspace/.distill_state_main.json

# 手动触发 distill（dry run）
. /root/.openclaw/mem0-agent-setup/.env && \
python3 /root/.openclaw/mem0-agent-setup/scripts/memory_distill_daily.py \
  --agent main --dry-run --yes

# 手动清理过期 session 记录（30天未活跃）
. /root/.openclaw/mem0-agent-setup/.env && \
python3 /root/.openclaw/mem0-agent-setup/scripts/memory_distill_daily.py \
  --agent main --cleanup --days 30 --yes

# 搜索记忆
. /root/.openclaw/mem0-agent-setup/.env && \
python3 /root/.openclaw/mem0-agent-setup/scripts/auto_recall.py "关键词"
```

---

*最后更新：2026-03-28 by 落雁 🦋*
