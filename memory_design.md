# 记忆系统设计方案 - v4

## 更新日志

### v4 (2026-03-28) — Per-Session 断点续传
- 每个 session 文件独立记录已处理行数，不再按时间戳
- 状态文件移到各 agent 自己的 workspace
- 新增 `--cleanup` 参数
- Cron 分批时间表（17 agent 分 6 批，04:00-04:25）

### v3 (2026-03-26)
- 支持 17 个 agent 同时蒸馏
- 批量处理优化
- 每日分批执行

### v3.1 (更早)
- Rerank 二次排序
- 过期清理策略

---

## 当前状态

### 保存流程 (auto_save)
- ✅ 关键词触发
- ✅ LLM 判断触发
- ✅ 重要性评分 (1-5分)
- ✅ 记忆分类 (episodic/semantic/procedural)
- 位置: `/root/.openclaw/mem0-agent-setup/scripts/auto_memory.py`

### 读取流程 (auto_recall)
- ✅ 语义搜索
- ✅ 分数过滤 (默认<2分过滤)
- ✅ 类型过滤
- ✅ **Rerank 二次排序**
- 位置: `/root/.openclaw/mem0-agent-setup/scripts/auto_recall.py`

### 兜底机制
- ✅ 关键词快速同步: `memory_sync.py`
- ✅ **过期清理**: `memory_cleanup.py`

### 每日蒸馏 (memory_distill_daily.py v4)
- ✅ Per-session 断点续传
- ✅ 状态文件各 agent 独立
- ✅ 分批 cron 执行
- ✅ `--cleanup` 清理过期 session 记录

---

## v4 Per-Session 断点续传

### 旧版问题（v3 及之前）

```json
{
  "last_distilled_at": "2026-03-26T15:10:19"
}
```

**问题**：
- 只能精确到"时间点"
- 同一 session 的新消息会被重复处理
- 无法区分哪些文件已被处理

### 新版方案（v4）

```json
{
  "sessions": {
    "7c86da32-ea18-4a3a-90b7-5d65bb1c2f53.jsonl": {
      "processed_lines": 142,
      "distilled_at": "2026-03-28T04:30:19",
      "current_lines": 142
    }
  },
  "global_last_run": "2026-03-28T04:35:00"
}
```

### 处理流程

```
对于每个 session 文件：
├── 检查 state["sessions"][filename] 是否存在
│   ├── 不存在（新 session）→ processed_lines = 0
│   └── 已存在 → 读取 stored processed_lines
│
├── 读取当前文件的总行数 current_lines
│
├── 如果 current_lines > processed_lines
│   → 只读取 [processed_lines:] 的新行
│   → 蒸馏新行
│   → 更新 processed_lines = current_lines
│
└── 如果 current_lines <= processed_lines
    → 跳过（无新内容）
```

### 迁移逻辑

脚本启动时自动检测旧格式，一次性迁移：

```python
if "last_distilled_at" in state and "sessions" not in state:
    # 旧格式 → 新格式
    state = {"sessions": {}, "global_last_run": state["last_distilled_at"]}
    save_state(state, state_file)
```

---

## 过期清理策略

```
score=1 → 30天删除
score=2 → 90天删除
score=3 → 180天删除
score>=4 → 永不过期
```

---

## 文件清单

| 文件 | 功能 |
|------|------|
| watch_sessions.js | 监听 session，触发 sync |
| sync_to_mem0.py | 实时写入向量库 |
| auto_recall.py | 检索记忆（含 rerank） |
| auto_memory.py | 手动保存记忆 |
| memory_distill_daily.py | 每日蒸馏 v4 |
| memory_cleanup.py | 清理过期记忆 |
| memory_sync.py | 批量同步历史 |
| mem0-agent.py | CLI 工具 |
| memory_design.md | 设计文档 |

---

## Cron 时间表

| 时间 | 任务 | Agent |
|------|------|-------|
| 03:00 | 记忆清理 | main |
| 04:00 | 记忆蒸馏 | main, capital, dev |
| 04:05 | 记忆蒸馏 | bingbu, gongbu |
| 04:10 | 记忆蒸馏 | legal, ops |
| 04:15 | 记忆蒸馏 | libu_hr, menxia, rich |
| 04:20 | 记忆蒸馏 | xingbu |
| 04:25 | 记忆蒸馏 | zaochao, zhongshu, shangshu, taizi, hubu, libu |
