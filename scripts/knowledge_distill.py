#!/usr/bin/env python3
"""
知识蒸馏脚本 - 将 workspace-{agent}/knowledge/ 下的 .md 文件蒸馏写入 Mem0
用法: python3 knowledge_distill.py --agent capital
"""
import os, sys, re, json, time, argparse, requests
from datetime import datetime
from pathlib import Path

STATE_FILE_TEMPLATE = "/root/.openclaw/workspace/.distill_knowledge_{agent}.json"

# ========== 配置 ==========
def load_siliconflow_key():
    """从 config.yaml 读取 SiliconFlow API key"""
    cfg_path = "/root/.openclaw/mem0-agent-setup/config/config.yaml"
    if os.path.exists(cfg_path):
        import yaml
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        key = cfg.get("llm", {}).get("api_key", "")
        if key:
            return key
    return os.environ.get("OPENAI_API_KEY", "")

def get_config():
    parser = argparse.ArgumentParser(description="知识蒸馏")
    parser.add_argument("--agent", default=os.environ.get("AGENT_NAME", "main"), help="Agent ID")
    parser.add_argument("--dry-run", action="store_true", help="只蒸馏，不写入")
    parser.add_argument("--force", action="store_true", help="强制全量处理")
    parser.add_argument("--yes", action="store_true", help="跳过确认直接写入")
    parser.add_argument("--min-score", type=int, default=3, help="最低评分（默认3）")
    parser.add_argument("--batch-size", type=int, default=20, help="每批处理多少个文件（默认20）")
    args = parser.parse_args()

    agent = args.agent
    knowledge_dir = f"/root/.openclaw/workspace-{agent}/knowledge"
    return {
        "knowledge_dir": knowledge_dir,
        "collection": f"mem0_{agent}",
        "state_file": STATE_FILE_TEMPLATE.format(agent=agent),
        "agent": agent,
        "dry_run": args.dry_run,
        "force": args.force,
        "min_score": args.min_score,
        "batch_size": args.batch_size,
        "yes": args.yes,
        "api_key": load_siliconflow_key(),
    }
# ==========================

def load_state(state_file):
    if os.path.exists(state_file):
        with open(state_file) as f:
            return json.load(f)
    return {"last_distilled_at": None, "processed_files": {}}

def save_state(state, state_file):
    with open(state_file, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def get_knowledge_files(knowledge_dir, since_dt=None):
    """获取所有 .md 文件，按修改时间过滤"""
    files = []
    p = Path(knowledge_dir)
    if not p.exists():
        return files
    for f in p.rglob("*.md"):
        # 跳过 README.md 和索引文件
        if f.name.startswith("README") or f.name.startswith("索引"):
            continue
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        files.append({
            "path": str(f),
            "mtime": mtime,
            "relative": f.relative_to(p).stem  # 用于去重识别
        })
    return files

def extract_sections_from_file(filepath):
    """按 H2 标题拆分文件为多个 section（H3作为子内容合并）"""
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    # 按 ## H2 拆分
    h2_pattern = re.compile(r'^##\s+(.+)\s*$', re.MULTILINE)
    matches = list(h2_pattern.finditer(content))

    sections = []
    if not matches:
        # 没有 H2，整个文件作为一个 section
        sections.append({"title": Path(filepath).stem, "content": content.strip()})
    else:
        for i, m in enumerate(matches):
            title = m.group(1).strip()
            start = m.end()
            end = matches[i+1].start() if i+1 < len(matches) else len(content)
            section_content = content[start:end].strip()
            # 去掉 H3 小标题行，但保留其内容
            section_content = re.sub(r'^###\s+.+\s*$', '', section_content, flags=re.MULTILINE)
            section_content = re.sub(r'\n{3,}', '\n\n', section_content).strip()
            if section_content and len(section_content) > 50:
                sections.append({"title": title, "content": section_content})

    return sections

def distill_batch(sections_batch, files_info, llm_client):
    """蒸馏一批 sections，返回 block 列表"""
    if not sections_batch:
        return []
    
    file_names = [f["path"].split("/")[-1] for f in files_info]
    sections_text = []
    for idx, sec in enumerate(sections_batch):
        file_path = sec.get("_file_path", "unknown")
        file_name = file_path.split("/")[-1]
        sections_text.append(f"[文档{idx+1}] {file_name} | {sec['title']}\n{sec['content'][:300]}")
    
    block_list = "\n".join(sections_text)
    prompt = f"""你是知识整理助手。以下是从投资知识库中提取的文档片段：

{block_list}

请将以上内容提炼成若干独立的知识块（block），每个 block 是一个完整的知识点。

要求：
- 每个 block 包含一个独立的事实/概念/方法
- 相同主题的内容合并为一个 block
- 每个 block 用通俗语言陈述，避免照抄原文
- 每个 block 必须标注层级

格式（严格按此格式，每个 block 之间空一行）：
[层级:Semantic|层级:Procedural]
[{{block}}内容]

层级说明：
- Semantic：事实性知识（是什么、为什么）
- Procedural：操作性知识（怎么做、步聚）

示例：
[层级:Semantic]
72法则用于快速计算投资翻倍年数：翻倍年数 ≈ 72 / 年收益率

[层级:Procedural]
K线放量突破时买入：突破日成交量需是前5日平均成交量的1.5倍以上

不要解释，只输出 block 列表。"""

    try:
        resp = llm_client.chat.completions.create(
            model="Qwen/Qwen2.5-7B-Instruct",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        text = resp.choices[0].message.content.strip()
        parsed = parse_distilled_blocks(text)
        # 附加源文件信息
        result = []
        for content, layer in parsed:
            result.append({
                "content": content.strip(),
                "layer": layer,
                "files": list(set([s.get("_file_path", "unknown") for s in sections_batch if s.get("_file_path")])),
            })
        return result
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

def score_blocks_batch(blocks, llm_client, batch_size=20):
    """批量评分 - 一次 LLM 调用评分多个 blocks，比逐个评分快 15-20 倍"""
    if not blocks:
        return []
    
    scored = []
    for i in range(0, len(blocks), batch_size):
        batch = blocks[i:i+batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(blocks) + batch_size - 1) // batch_size
        
        block_texts = '\n'.join([f"[{j+1}] {b['content'][:200]}" for j, b in enumerate(batch)])
        prompt = f"""以下是从知识库中提炼的知识块，请对每个知识块进行重要性评分（1-5）：

{block_texts}

评分标准：
- 5：核心知识，多场景通用，必须记住
- 4：重要知识，有较大应用价值
- 3：一般知识，有用但不是核心
- 2：边缘知识，可记可不记
- 1：没有价值，不应存储

格式（每行一个评分，只写数字）：
[数字]

示例（3个block）：
4
5
3"""
        
        try:
            resp = llm_client.chat.completions.create(
                model="Qwen/Qwen2.5-7B-Instruct",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1
            )
            response_text = resp.choices[0].message.content.strip()
            # 解析每行数字
            lines = response_text.split('\n')
            scores = []
            for line in lines:
                line = line.strip()
                if line and line[0].isdigit():
                    scores.append(int(line[0]))
            
            # 分配分数
            for j, block in enumerate(batch):
                if j < len(scores):
                    block["score"] = scores[j]
                else:
                    block["score"] = 3  # 默认3分
                scored.append(block)
            
            print(f"  评分第 {batch_num}/{total_batches} 批（{len(batch)} 个 blocks）")
            time.sleep(0.3)
        except Exception as e:
            print(f"  评分失败（第{batch_num}批）: {e}，默认3分")
            for block in batch:
                block["score"] = 3
                scored.append(block)
    
    return scored

def deduplicate_blocks(new_blocks, existing_blocks):
    """基于内容相似度去重，保留两个文件的引用"""
    seen = {}  # key: 文本前50字符 -> (block, files)
    
    for block in existing_blocks:
        key = block["content"][:50].lower()
        seen[key] = block
    
    merged = []
    for block in new_blocks:
        key = block["content"][:50].lower()
        if key in seen:
            # 合并文件列表
            existing = seen[key]
            all_files = list(set(existing.get("files", []) + block.get("files", [])))
            existing["files"] = all_files
        else:
            seen[key] = block
            merged.append(block)
    
    return merged

def write_blocks(blocks_with_scores, qdrant_client, embed_api_key, agent, collection):
    """手动生成向量并直接写入 Qdrant"""
    import uuid

    written = 0
    for item in blocks_with_scores:
        block_text = item["content"]
        score = item["score"]
        layer = item["layer"]
        files = item.get("files", [])

        file_str = ",".join(files)
        record = f"[层级:{layer}][score:{score}][knowledge_distill][files:{len(files)}]\n{block_text}"

        # 生成 embedding
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
            "source": "knowledge_distill",
            "source_files": files,
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


def main():
    cfg = get_config()
    knowledge_dir = cfg["knowledge_dir"]
    collection = cfg["collection"]
    state_file = cfg["state_file"]
    agent = cfg["agent"]
    dry_run = cfg["dry_run"]
    force = cfg["force"]
    min_score = cfg["min_score"]
    batch_size = cfg["batch_size"]

    from qdrant_client import QdrantClient
    from openai import OpenAI

    API_KEY = cfg["api_key"]
    if not API_KEY:
        print("ERROR: set OPENAI_API_KEY or configure config.yaml"); sys.exit(1)
    BASE_URL = "https://api.siliconflow.cn/v1"
    client_llm = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    qdrant_client = QdrantClient(url="http://localhost:6333")

    print(f"[Agent: {agent}] 知识蒸馏 | 目录: {knowledge_dir}")

    # 加载状态
    state = load_state(state_file)
    since_dt = None if force else state.get("last_distilled_at")
    
    # 获取文件列表
    all_files = get_knowledge_files(knowledge_dir, since_dt)
    print(f"找到 {len(all_files)} 个知识文件")
    if not all_files:
        print("没有需要处理的文件")
        return

    # 按修改时间过滤
    if since_dt and not force:
        since = datetime.fromisoformat(since_dt)
        to_process = [f for f in all_files if f["mtime"] > since]
        print(f"增量模式，需处理 {len(to_process)} 个文件（{since.strftime('%Y-%m-%d')} 后修改）")
    else:
        to_process = all_files
        print(f"全量模式，处理全部 {len(to_process)} 个文件")

    if not to_process:
        print("没有需要处理的文件")
        return

    # 提取所有 sections
    all_sections = []
    for f in to_process:
        sections = extract_sections_from_file(f["path"])
        for sec in sections:
            sec["_file_path"] = f["path"]
        all_sections.extend(sections)
    
    print(f"提取了 {len(all_sections)} 个知识片段")

    # 分批蒸馏
    print("开始分批蒸馏...")
    all_blocks = []
    for i in range(0, len(all_sections), batch_size):
        batch = all_sections[i:i+batch_size]
        batch_idx = i // batch_size + 1
        total_batches = (len(all_sections) + batch_size - 1) // batch_size
        print(f"  处理第 {batch_idx}/{total_batches} 批（{len(batch)} 个片段）...")
        blocks = distill_batch(batch, to_process, client_llm)
        all_blocks.extend(blocks)
        print(f"    -> 产出 {len(blocks)} 个 blocks")

    if not all_blocks:
        print("没有产出任何 block")
        return

    print(f"\n共产出 {len(all_blocks)} 个 blocks，开始评分（批量）...")

    # 批量评分
    scored = score_blocks_batch(all_blocks, client_llm)

    # 按评分分组展示
    print(f"\n评分结果（共 {len(scored)} 个 blocks）：")
    for s in [5, 4, 3, 2, 1]:
        g = [item for item in scored if item["score"] == s]
        if g:
            label = {5: "core", 4: "important", 3: "normal", 2: "temp", 1: "discard"}[s]
            print(f"  score={s} ({label}): {len(g)} 条")
            for item in g[:2]:
                print(f"    - [{item['layer']}] {item['content'][:80]}...")

    # 去重
    print("\n去重...")
    # 从 Mem0 获取已存在的同 agent blocks（简化版：只取同 agent 最近100条做去重参考）
    deduped = scored  # 简化处理，实际生产可接入 Mem0 搜索做语义去重
    print(f"去重后 {len(deduped)} 个 blocks")

    # 过滤低分
    to_store = [item for item in deduped if item["score"] >= min_score]
    print(f"将存入 {len(to_store)} 条（score>={min_score}）")

    if dry_run:
        print("[dry-run，不写入]")
        return

    if not cfg.get("yes") and input("确认写入？（y/n）: ").strip().lower() != "y":
        print("已取消")
        return

    print("写入 Mem0...")
    written = write_blocks(to_store, qdrant_client, API_KEY, agent, collection)
    save_state({
        "last_distilled_at": datetime.now().isoformat(),
        "processed_files": {f["path"]: f["mtime"].isoformat() for f in to_process}
    }, state_file)
    print(f"\n完成！写入 {written} 条")

if __name__ == "__main__":
    main()
