#!/usr/bin/env python3
"""
每日记忆精炼脚本 - v4 (per-session checkpoint)
将 session 文件中的对话蒸馏成结构化记忆 blocks，评分后写入 Qdrant

改进点 v4:
- Per-session 断点续传：每个 session 文件独立记录已处理行数
- 不再重复处理同一 session 的历史对话
- 状态文件存在各 agent 自 workspace 中
- 自动迁移旧格式状态
"""
import os, sys, re, json, time, argparse, requests
from datetime import datetime, timedelta
from pathlib import Path

SESSIONS_DIR = "/root/.openclaw/agents/main/sessions"
STATE_FILE = "/root/.openclaw/workspace/.distill_state.json"
COLLECTION = "mem0_main"

# ========== 配置 ==========
def get_config():
    parser = argparse.ArgumentParser(description="每日记忆精炼")
    parser.add_argument("--agent", default=os.environ.get("AGENT_NAME", "main"), help="Agent ID")
    parser.add_argument("--days", type=int, default=1, help="处理最近多少天（默认1）")
    parser.add_argument("--dry-run", action="store_true", help="只蒸馏，不写入")
    parser.add_argument("--force", action="store_true", help="强制全量处理")
    parser.add_argument("--yes", action="store_true", help="跳过确认直接写入")
    parser.add_argument("--batch-size", type=int, default=80, help="每批处理多少条对话（默认80）")
    parser.add_argument("--cleanup", action="store_true", help="清理超过N天未活跃的session记录（需配合 --days 指定天数）")
    args = parser.parse_args()

    agent = args.agent
    
    # 状态文件路径：各 agent 存在各自 workspace
    if agent == "main":
        state_file = f"/root/.openclaw/workspace/.distill_state.json"
    else:
        state_file = f"/root/.openclaw/workspace-{agent}/.distill_state.json"

    return {
        "sessions_dir": f"/root/.openclaw/agents/{agent}/sessions",
        "collection": f"mem0_{agent}",
        "state_file": state_file,
        "agent": agent,
        "dry_run": args.dry_run,
        "force": args.force,
        "days": args.days,
        "batch_size": args.batch_size,
        "yes": args.yes,
        "cleanup": args.cleanup,
    }

def get_state_path(cfg):
    """获取状态文件路径，确保目录存在"""
    state_file = cfg["state_file"]
    state_dir = os.path.dirname(state_file)
    if state_dir and not os.path.exists(state_dir):
        os.makedirs(state_dir, exist_ok=True)
    return state_file

# ========== 状态管理 ==========
def load_state(state_file):
    """加载状态，自动迁移旧格式"""
    if os.path.exists(state_file):
        with open(state_file) as f:
            state = json.load(f)
        # 旧格式迁移：{"last_distilled_at": "..."} -> {"sessions": {}, "global_last_run": "..."}
        if "last_distilled_at" in state and "sessions" not in state:
            print(f"[迁移] 检测到旧格式状态文件，自动迁移到新格式")
            old_ts = state["last_distilled_at"]
            state = {
                "sessions": {},
                "global_last_run": old_ts,
                "migrated_from_timestamp": True,
                "migrated_at": datetime.now().isoformat()
            }
            # 写回新格式
            save_state(state, state_file)
            print(f"[迁移] 已迁移，last_distilled_at={old_ts}")
        return state
    return {"sessions": {}, "global_last_run": None}

def save_state(state, state_file):
    """保存状态"""
    state_file = get_state_path({"state_file": state_file})
    with open(state_file, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def cleanup_stale_sessions(state, sessions_dir, days=30):
    """清理超过N天未活跃的 session 记录"""
    cutoff = datetime.now() - timedelta(days=days)
    cleaned = []
    sessions = state.get("sessions", {})
    
    for fname, info in list(sessions.items()):
        distilled_at = info.get("distilled_at")
        if not distilled_at:
            continue
        try:
            dt = datetime.fromisoformat(distilled_at)
            if dt < cutoff:
                del sessions[fname]
                cleaned.append(fname)
        except:
            pass
    
    if cleaned:
        print(f"[清理] 移除 {len(cleaned)} 个过期 session 记录: {cleaned}")
    return state, cleaned

# ========== Session 文件扫描（断点续传） ==========
def get_session_with_progress(sessions_dir, state, force=False):
    """
    返回需要处理的 session 文件列表及其起始行数
    
    Returns:
        list of (filepath, start_line) — start_line 为已处理行数（下次从该行之后继续）
    """
    p = Path(sessions_dir)
    if not p.exists():
        return []
    
    sessions_state = state.get("sessions", {})
    global_last_run = state.get("global_last_run")
    if global_last_run:
        try:
            last_run_dt = datetime.fromisoformat(global_last_run)
        except:
            last_run_dt = None
    else:
        last_run_dt = None
    
    result = []
    
    for f in p.glob("*.jsonl"):
        fname = f.name
        current_lines = count_lines(f)
        
        if fname in sessions_state:
            # 已有记录：检查是否有增量
            processed_lines = sessions_state[fname].get("processed_lines", 0)
            if current_lines > processed_lines:
                result.append((str(f), processed_lines))
                # 更新总行数（便于后续追踪）
                sessions_state[fname]["current_lines"] = current_lines
            else:
                # 无增量，跳过
                pass
        else:
            # 新 session 或 force 模式
            if force:
                result.append((str(f), 0))
            elif last_run_dt:
                # 只处理 global_last_run 之后修改的文件
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime > last_run_dt:
                    result.append((str(f), 0))
                # 否则跳过（认为没有新内容）
            else:
                # 没有任何记录，当新文件处理
                result.append((str(f), 0))
    
    return result

def count_lines(filepath):
    """快速统计文件行数"""
    try:
        with open(filepath, 'rb') as f:
            return sum(1 for _ in f)
    except:
        return 0

def read_sessions_from_file(filepath, start_line=0):
    """读取指定文件的对话，从 start_line 之后开始读取"""
    conversations = []
    with open(filepath) as fp:
        for i, line in enumerate(fp):
            if i < start_line:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("type") == "message":
                    msg = obj.get("message", {})
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        content = " ".join(c.get("text", "") for c in content if c.get("type") == "text")
                    if role in ("user", "assistant") and content.strip():
                        clean = extract_user_content(content) if role == "user" else content.strip()
                        if clean and len(clean) > 5:
                            conversations.append({
                                "session": Path(filepath).name, 
                                "role": role, 
                                "content": clean[:500]
                            })
            except:
                pass
    return conversations

# ========== 原有函数（保持不变） ==========
def extract_user_content(text):
    if text.startswith("System:"):
        m = re.search(r'Sender \(untrusted metadata\):[\s\S]+?\n\n([\s\S]+)$', text)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return text.strip()

def distill_batch(conversations_batch, llm_client):
    """蒸馏一批对话，返回 block 列表"""
    if not conversations_batch:
        return []
    sessions = list(set(c["session"] for c in conversations_batch))
    lines = [f"[{i+1}] [{c['session']}] {'User' if c['role']=='user' else 'Assistant'}: {c['content']}" for i, c in enumerate(conversations_batch)]
    block_list = "\n".join(lines)
    prompt = f"""你是记忆整理助手。以下是一批对话记录，涉及 session 文件：{', '.join(sessions)}

{block_list}

请将以上对话提炼成若干独立的记忆块（block），每个 block 是完整的自然语言陈述。

要求：
- 每个 block 包含一个独立的事实/事件/方法
- 相同主题的内容合并为一个 block
- 不重要的闲聊忽略
- 每个 block 必须标注层级

格式（严格按此格式，每个 block 之间空一行）：
[层级:Semantic|层级:Episodic|层级:Procedural]
[{{block}}内容]

示例：
[层级:Episodic]
用户提到项目ABC需要在周五前完成测试报告

不要解释，只输出 block 列表。"""
    try:
        resp = llm_client.chat.completions.create(
            model="Qwen/Qwen2.5-7B-Instruct",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        text = resp.choices[0].message.content.strip()
        parsed = parse_distilled_blocks(text)
        return [(b.strip(), sessions, layer) for b, layer in parsed]
    except Exception as e:
        print(f"  LLM 错误: {e}")
        return []

def parse_distilled_blocks(text):
    """解析带层级分类的block文本"""
    pattern = re.compile(
        r'\[层级:(\w+)\]\s*\n*([\s\S]+?)(?=\[层级:|$)',
        re.MULTILINE | re.DOTALL
    )
    results = []
    for m in pattern.finditer(text):
        layer = m.group(1)
        content = m.group(2).strip()
        if content:
            results.append((content, layer))
    return results

def distill_conversations_batched(conversations, llm_client, batch_size=80):
    """分批蒸馏，合并结果"""
    all_blocks = []
    total_batches = (len(conversations) + batch_size - 1) // batch_size
    print(f"  共 {len(conversations)} 条对话，分 {total_batches} 批处理（每批 {batch_size} 条）")
    for i in range(0, len(conversations), batch_size):
        batch = conversations[i:i+batch_size]
        batch_num = i // batch_size + 1
        print(f"  处理第 {batch_num}/{total_batches} 批（{len(batch)} 条）...")
        blocks = distill_batch(batch, llm_client)
        print(f"    -> 产出 {len(blocks)} 个 blocks")
        all_blocks.extend(blocks)
        if i + batch_size < len(conversations):
            time.sleep(1)
    print(f"  共生成 {len(all_blocks)} 个 blocks")
    return all_blocks

def score_blocks(blocks_with_layers, llm_client, batch_size=30):
    """对 blocks 评分，保持层级信息（批量处理避免 prompt 过长）"""
    if not blocks_with_layers:
        return []
    
    scored = []
    total_batches = (len(blocks_with_layers) + batch_size - 1) // batch_size
    print(f"  分 {total_batches} 批评分（每批 {batch_size} 条）")
    
    for batch_idx in range(total_batches):
        start = batch_idx * batch_size
        end = min(start + batch_size, len(blocks_with_layers))
        batch = blocks_with_layers[start:end]
        
        texts = [b[0] for b in batch]
        sessions_all = [b[1] for b in batch]
        layers = [b[2] for b in batch]
        
        prompt = """以下是从对话中提炼的记忆 block，请对每个评分（1-5分，5分最重要）：
1分：闲聊、无关内容
2分：一般信息
3分：有价值的信息
4分：重要信息
5分：关键信息（如决策、承诺、偏好、重要事件）

评分格式（严格一行一个）：
[分数] block内容

block列表：
""" + "\n".join([f"[{i+1}] {t}" for i, t in enumerate(texts)])

        try:
            resp = llm_client.chat.completions.create(
                model="Qwen/Qwen2.5-7B-Instruct",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1
            )
            text = resp.choices[0].message.content.strip()
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            batch_scored = 0
            for line in lines:
                m = re.match(r'\[?(\d)\]?\s*(.+)', line)
                if m:
                    score = int(m.group(1))
                    block_text = m.group(2).strip()
                    for j, t in enumerate(texts):
                        if block_text == t or block_text in t:
                            scored.append((block_text, score, sessions_all[j], layers[j]))
                            batch_scored += 1
                            break
            print(f"    第{batch_idx+1}批：评分了 {batch_scored} 个")
        except Exception as e:
            print(f"    第{batch_idx+1}批 LLM 错误: {e}")
    
    print(f"  共评分了 {len(scored)} 个 blocks")
    return scored

def write_blocks(blocks_with_scores, qdrant_client, embed_api_key, agent, collection, min_score=3):
    """手动生成向量并直接写入 Qdrant"""
    import uuid, requests

    written = 0
    for item in blocks_with_scores:
        if len(item) != 4:
            continue
        block_text, score, sessions, layer = item[:4]
        if score < min_score:
            continue
        files = ",".join([f"/root/.openclaw/agents/{agent}/sessions/{s}" for s in sessions])
        record = f"[层级:{layer}][score:{score}][distilled][sessions:{len(sessions)}][files:{files}]\n{block_text}"

        try:
            resp = requests.post(
                "https://api.siliconflow.cn/v1/embeddings",
                headers={"Authorization": f"Bearer {embed_api_key}"},
                json={"model": "BAAI/bge-large-zh-v1.5", "input": record}
            )
            data = resp.json()
            vec = data["data"][0]["embedding"]
        except Exception as e:
            print(f"  Embedding 失败: {e}")
            continue

        payload = {
            "user_id": os.environ.get("MEM0_USER_ID", "fuge"),
            "agent_id": agent,
            "role": "user",
            "data": record,
            "hash": str(uuid.uuid4()),
            "created_at": datetime.now().isoformat(),
            "layer": layer,
        }

        point = {
            "id": str(uuid.uuid4()),
            "vector": vec,
            "payload": payload,
        }
        try:
            qdrant_client.upsert(collection_name=collection, points=[point])
            print(f"  OK [层级:{layer}][score:{score}] {block_text[:60]}...")
            written += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"  Qdrant 写入失败: {e}")
    return written

# ========== 主流程 ==========
def main():
    cfg = get_config()
    sessions_dir = cfg["sessions_dir"]
    collection = cfg["collection"]
    state_file = cfg["state_file"]
    agent = cfg["agent"]
    dry_run = cfg["dry_run"]
    force = cfg["force"]
    batch_size = cfg["batch_size"]
    cleanup_days = cfg["days"] if cfg.get("cleanup") else None

    os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "")
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: set OPENAI_API_KEY"); sys.exit(1)

    from qdrant_client import QdrantClient
    from openai import OpenAI

    API_KEY = os.environ["OPENAI_API_KEY"]
    BASE_URL = "https://api.siliconflow.cn/v1"
    client_llm = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    qdrant_client = QdrantClient(url="http://localhost:6333")

    print(f"[Agent: {agent}] Collection: {collection}  Sessions: {sessions_dir}")
    print(f"[State] {state_file}")

    state = load_state(state_file)
    
    # 清理过期 session 记录
    if cleanup_days:
        state, cleaned = cleanup_stale_sessions(state, sessions_dir, days=cleanup_days)
        save_state(state, state_file)

    # 获取需要处理的文件及起始行
    files_to_process = get_session_with_progress(sessions_dir, state, force=force)
    
    if not files_to_process:
        print("没有需要处理的 session 文件")
        return

    print(f"发现 {len(files_to_process)} 个 session 需要处理")
    
    # 统计总对话数
    total_convs = 0
    for filepath, start_line in files_to_process:
        convs = read_sessions_from_file(filepath, start_line)
        total_convs += len(convs)
        print(f"  {Path(filepath).name}: 从第{start_line}行开始，{len(convs)}条新对话")

    if total_convs == 0:
        print("没有新对话可处理")
        return

    # 读取所有对话
    all_convs = []
    for filepath, start_line in files_to_process:
        convs = read_sessions_from_file(filepath, start_line)
        all_convs.extend(convs)

    print(f"共 {len(all_convs)} 条对话片段")

    print("开始分批蒸馏...")
    blocks = distill_conversations_batched(all_convs, client_llm, batch_size=batch_size)
    if not blocks:
        return

    print("开始评分...")
    scored = score_blocks(blocks, client_llm)
    if not scored:
        return

    print(f"\n评分结果（共 {len(scored)} 个 blocks）：")
    for s in [5, 4, 3, 2, 1]:
        g = [item for item in scored if item[1] == s]
        if g:
            label = {5: "core", 4: "important", 3: "normal", 2: "temp", 1: "discard"}[s]
            layer_count = {}
            for item in g:
                layer = item[3] if len(item) > 3 else "?"
                layer_count[layer] = layer_count.get(layer, 0) + 1
            print(f"  score={s} ({label}): {len(g)} 条 {layer_count}")
            for item in g[:3]:
                print(f"    - [{item[3]}] {item[0][:80]}...")

    to_store = [item for item in scored if item[1] >= 3]
    print(f"\n将存入 {len(to_store)} 条（score>=3）")

    if dry_run:
        print("[dry-run，不写入]")
        return

    if not cfg.get("yes") and input("确认写入？（y/n）: ").strip().lower() != "y":
        print("已取消")
        return

    print("写入 mem0...")
    written = write_blocks(to_store, qdrant_client, API_KEY, agent, collection)
    
    # 更新状态：每个 session 的处理进度
    now = datetime.now().isoformat()
    for filepath, start_line in files_to_process:
        fname = Path(filepath).name
        current_lines = count_lines(filepath)
        if fname not in state.get("sessions", {}):
            state.setdefault("sessions", {})
        state["sessions"][fname] = {
            "processed_lines": current_lines,
            "distilled_at": now,
            "current_lines": current_lines
        }
    
    state["global_last_run"] = now
    save_state(state, state_file)
    
    print(f"\n完成！写入 {written} 条")
    print(f"状态已更新: {len(files_to_process)} 个 session")

if __name__ == "__main__":
    main()
