#!/usr/bin/env python3
"""
legal记忆蒸馏循环 v4（直接调用核心逻辑，不依赖文件mtime过滤）
1. 扫描所有对话，按内容中的日期分组
2. 逐日蒸馏（每日一批 LLM 调用）
3. 每批之间暂停，支持 Ctrl+C 中断
"""
import os, sys, re, json, time, argparse, uuid, requests
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Tuple

# ========== 配置 ==========
AGENT = "legal"
SESSIONS_DIR = f"/root/.openclaw/agents/{AGENT}/sessions"
STATE_FILE = f"/root/.openclaw/workspace/.distill_state_{AGENT}.json"
LOG_DIR = "/root/.openclaw/workspace/logs"
BATCH_SIZE = 80        # 每批对话数
SCORE_BATCH = 30       # 评分批大小
TIMEOUT_PER_DAY = 1800 # 每日期蒸馏超时 30 分钟
MIN_SCORE = 3          # 只存评分>=3的记忆

os.makedirs(LOG_DIR, exist_ok=True)

# 加载 .env
for line in open("/root/.openclaw/mem0-agent-setup/.env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = "https://api.siliconflow.cn/v1"

# ========== 工具函数 ==========

def extract_date(content: str):
    m = re.search(r'\[(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}', content)
    return m.group(1) if m else None

def extract_user_content(text: str):
    if text.startswith("System:"):
        m = re.search(r'Sender \(untrusted metadata\):[\s\S]+?\n\n([\s\S]+)$', text)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return text.strip()

def scan_conversations() -> Dict[str, List[dict]]:
    """扫描所有 session，按内容日期分组"""
    date_convs = defaultdict(list)
    files = list(Path(SESSIONS_DIR).glob("*.jsonl"))
    print(f"📁 扫描 {len(files)} 个 session 文件...")

    for f in files:
        try:
            with open(f) as fp:
                for line in fp:
                    if not (line := line.strip()):
                        continue
                    try:
                        obj = json.loads(line)
                        if obj.get("type") != "message":
                            continue
                        msg = obj.get("message", {})
                        role = msg.get("role", "")
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            content = " ".join(c.get("text","") for c in content if c.get("type")=="text")
                        if role not in ("user","assistant") or not content.strip():
                            continue
                        date = extract_date(content)
                        if not date:
                            continue
                        clean = extract_user_content(content) if role == "user" else content.strip()
                        if not clean or len(clean) < 5:
                            continue
                        date_convs[date].append({
                            "session": f.name,
                            "role": role,
                            "content": clean[:500]
                        })
                    except:
                        pass
        except Exception as e:
            print(f"  ⚠️  {f.name}: {e}")

    return dict(date_convs)

# ========== LLM 调用 ==========

def make_client():
    from openai import OpenAI
    return OpenAI(api_key=API_KEY, base_url=BASE_URL)

def distill_batch(convs: List[dict], client) -> List[Tuple[str, str, str]]:
    """蒸馏一批对话，返回 [(content, sessions, layer)]"""
    if not convs:
        return []

    sessions = list(set(c["session"] for c in convs))
    lines = [f"[{i+1}] [{c['session']}] {'User' if c['role']=='user' else 'Assistant'}: {c['content']}"
             for i, c in enumerate(convs)]
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
        resp = client.chat.completions.create(
            model="Qwen/Qwen2.5-7B-Instruct",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        text = resp.choices[0].message.content.strip()

        # 解析
        pattern = re.compile(r'\[层级:(\w+)\]\s*\n*([\s\S]+?)(?=\[层级:|$)', re.MULTILINE | re.DOTALL)
        results = []
        for m in pattern.finditer(text):
            layer = m.group(1)
            content = m.group(2).strip()
            if content:
                results.append((content, sessions, layer))
        return results
    except Exception as e:
        print(f"  LLM 错误: {e}")
        return []

def distill_conversations(convs: List[dict], client, batch_size=BATCH_SIZE) -> List[Tuple[str, str, str]]:
    """分批蒸馏"""
    all_blocks = []
    total = (len(convs) + batch_size - 1) // batch_size
    print(f"  共 {len(convs)} 条对话，分 {total} 批")

    for i in range(0, len(convs), batch_size):
        batch = convs[i:i+batch_size]
        batch_num = i // batch_size + 1
        print(f"  第 {batch_num}/{total} 批...")
        blocks = distill_batch(batch, client)
        print(f"    -> {len(blocks)} blocks")
        all_blocks.extend(blocks)
        if i + batch_size < len(convs):
            time.sleep(1)

    return all_blocks

def score_blocks(blocks: List[Tuple[str, str, str]], client, batch_size=SCORE_BATCH) -> List[Tuple[str, int, str, str]]:
    """评分 blocks, 返回 [(content, score, sessions, layer)]"""
    if not blocks:
        return []

    scored = []
    total = (len(blocks) + batch_size - 1) // batch_size
    print(f"  分 {total} 批评分")

    for bi in range(0, len(blocks), batch_size):
        batch = blocks[bi:bi+batch_size]
        bn = bi // batch_size + 1
        texts = [b[0] for b in batch]
        sess_all = [b[1] for b in batch]
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
            resp = client.chat.completions.create(
                model="Qwen/Qwen2.5-7B-Instruct",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1
            )
            text = resp.choices[0].message.content.strip()
            lines = [l.strip() for l in text.split("\n") if l.strip()]

            for line in lines:
                m = re.match(r'\[?(\d)\]?\s*(.+)', line)
                if m:
                    score = int(m.group(1))
                    block_text = m.group(2).strip()
                    for j, t in enumerate(texts):
                        if block_text == t or block_text in t:
                            scored.append((block_text, score, sess_all[j], layers[j]))
                            break
            print(f"    第{bn}批：评分了 {len([l for l in lines if re.match(r'\[?\d\]?', l)])} 个")
        except Exception as e:
            print(f"    第{bn}批 LLM 错误: {e}")

    return scored

def get_embedding(text: str) -> list:
    """调用 SiliconFlow embedding 接口"""
    resp = requests.post(
        f"{BASE_URL}/embeddings",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"model": "BAAI/bge-large-zh-v1.5", "input": text}
    )
    return resp.json()["data"][0]["embedding"]

def write_blocks(blocks: List[Tuple[str, int, str, str]], collection: str) -> int:
    """写入 Qdrant"""
    from qdrant_client import QdrantClient
    qclient = QdrantClient(url="http://localhost:6333")

    written = 0
    for item in blocks:
        block_text, score, sessions, layer = item
        if score < MIN_SCORE:
            continue

        files = ",".join([f"/root/.openclaw/agents/{AGENT}/sessions/{s}" for s in sessions])
        record = f"[层级:{layer}][score:{score}][distilled][sessions:{len(sessions)}][files:{files}]\n{block_text}"

        try:
            vec = get_embedding(record)
        except Exception as e:
            print(f"  Embedding 失败: {e}")
            continue

        payload = {
            "user_id": os.environ.get("MEM0_USER_ID", "fuge"),
            "agent_id": AGENT,
            "role": "user",
            "data": record,
            "hash": str(uuid.uuid4()),
            "created_at": datetime.now().isoformat(),
            "layer": layer,
        }

        point = {"id": str(uuid.uuid4()), "vector": vec, "payload": payload}
        try:
            qclient.upsert(collection_name=collection, points=[point])
            print(f"  ✓ [层级:{layer}][score:{score}] {block_text[:50]}...")
            written += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"  Qdrant 写入失败: {e}")

    return written

# ========== 主循环 ==========

def distill_one_day(date: str, convs: List[dict]) -> bool:
    """蒸馏单日对话"""
    if not convs:
        print(f"  无对话")
        return True

    print(f"  💬 {len(convs)} 条对话")

    client = make_client()

    # 1. 蒸馏
    print(f"  🔄 蒸馏...")
    blocks = distill_conversations(convs, client)
    if not blocks:
        print(f"  ⚠️  无 blocks 产出")
        return False

    # 2. 评分
    print(f"  🔄 评分...")
    scored = score_blocks(blocks, client)
    if not scored:
        print(f"  ⚠️  无评分结果")
        return False

    # 3. 统计
    print(f"\n  📊 评分结果:")
    for s in [5, 4, 3, 2, 1]:
        g = [x for x in scored if x[1] == s]
        if g:
            label = {5:"核心",4:"重要",3:"一般",2:"临时",1:"丢弃"}[s]
            print(f"    score={s} ({label}): {len(g)} 条")

    to_store = [x for x in scored if x[1] >= MIN_SCORE]
    print(f"\n  💾 将写入 {len(to_store)} 条 (score>={MIN_SCORE})")

    # 4. 写入
    print(f"  🔄 写入 Qdrant...")
    written = write_blocks(to_store, f"mem0_{AGENT}")
    print(f"  ✅ 写入完成: {written} 条")

    return True

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", "-y", action="store_true", help="自动确认")
    parser.add_argument("--dry-run", action="store_true", help="只显示，不执行")
    parser.add_argument("--start-from", type=str, default=None, help="从指定日期开始(YYYY-MM-DD)")
    args = parser.parse_args()

    print("=" * 60)
    print("  Legal记忆 蒸馏循环")
    print("=" * 60)

    # 1. 扫描
    date_convs = scan_conversations()
    if not date_convs:
        print("❌ 没有找到对话")
        return

    dates = sorted(date_convs.keys())
    total_convs = sum(len(v) for v in date_convs.values())

    print(f"\n📅 共 {len(dates)} 个自然日，{total_convs} 条对话:")
    for d in dates:
        print(f"   {d}: {len(date_convs[d])} 条")
    print()

    if args.dry_run:
        print("🔍 dry-run 模式，仅显示")
        for d in dates:
            print(f"   {d}: {len(date_convs[d])} 条")
        return

    if not args.yes:
        response = input("🚀 开始？（Ctrl+C 中断）: ").strip().lower()
        if response not in ("y", "yes", ""):
            print("取消")
            return
    else:
        print("✅ --yes 自动确认，开始执行...")

    # 2. 逐日处理
    SUCCESS, FAILED, SKIPPED = 0, [], 0

    # 跳过已处理的日期
    start_from = args.start_from
    if start_from:
        print(f"从 {start_from} 开始...\n")

    for i, date in enumerate(dates):
        if start_from and date < start_from:
            SKIPPED += 1
            print(f"  ⏭ [{i+1}/{len(dates)}] {date} - 跳过（< {start_from}）")
            continue

        print(f"\n{'='*60}")
        print(f"  [{i+1}/{len(dates)}] 📅 {date}")
        print(f"{'='*60}")

        convs = date_convs[date]
        sessions = set(c["session"] for c in convs)
        print(f"  📁 {len(sessions)} 个文件, {len(convs)} 条对话")

        ok = distill_one_day(date, convs)

        if ok:
            SUCCESS += 1
            # 更新 state
            with open(STATE_FILE, "w") as f:
                json.dump({"last_distilled_at": f"{date}T23:59:59"}, f)
        else:
            FAILED.append(date)

        print(f"\n⏸  休息 5秒...")
        time.sleep(5)

    # 3. 总结
    print(f"\n{'='*60}")
    print(f"📊 完成！")
    print(f"   成功: {SUCCESS}/{len(dates)}")
    print(f"   失败: {len(FAILED)}")
    if FAILED:
        print(f"   失败: {', '.join(FAILED)}")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
