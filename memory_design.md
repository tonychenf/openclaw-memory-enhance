# 记忆系统设计方案 - v3.1

## 当前状态

### 保存流程 (auto_save)
- ✅ 关键词触发
- ✅ LLLM判断触发
- ✅ 重要性评分 (1-5分)
- ✅ 记忆分类 (episodic/semantic/procedural)
- 位置: `/root/.openclaw/workspace/auto_memory.py`

### 读取流程 (auto_recall)
- ✅ 语义搜索
- ✅ 分数过滤 (默认<2分过滤)
- ✅ 类型过滤
- ✅ **Rerank 二次排序**
- 位置: `/root/.openclaw/workspace/auto_recall.py`

### 兜底机制
- ✅ 关键词快速同步: `memory_sync.py`
- ✅ **过期清理**: `memory_cleanup.py`

## v3.1 新增功能

### Rerank 二次排序
- 用 LLM 对搜索结果重新排序
- 提升相关性，优先展示最相关的记忆

### 过期清理策略
```
score=1 → 30天删除
score=2 → 90天删除
score=3 → 180天删除
score>=4 → 永不过期
```

## 使用方法

```bash
# 手动保存记忆
python3 auto_memory.py "用户消息" "助手回复"

# 读取记忆
python3 auto_recall.py "用户消息"

# 清理过期记忆
python3 memory_cleanup.py [天数]

# 同步历史记忆
python3 memory_sync.py [天数]
```

## 文件清单

| 文件 | 功能 |
|------|------|
| auto_memory.py | 保存记忆（含评分分类） |
| auto_recall.py | 读取记忆（含rerank） |
| memory_sync.py | 批量同步历史 |
| memory_cleanup.py | 清理过期记忆 |
| memory_design.md | 设计文档 |
