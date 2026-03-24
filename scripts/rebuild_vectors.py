#!/usr/bin/env python3
"""
重建 embedding 向量
读取 mem0_main 里所有记录，重新生成向量并写回 Qdrant
"""
import os, sys, re, time, json
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from openai import OpenAI

os.environ['OPENAI_API_KEY'] = os.environ.get('OPENAI_API_KEY', '')
API_KEY = os.environ.get('OPENAI_API_KEY', '')
if not API_KEY:
    print("ERROR: set OPENAI_API_KEY"); sys.exit(1)

OPENAI_BASE_URL = 'https://api.siliconflow.cn/v1'
EMBEDDING_MODEL = 'BAAI/bge-large-zh-v1.5'
EMBEDDING_DIM = 1024
BATCH_SIZE = 20
BATCH_DELAY = 2

QDRANT_HOST, QDRANT_PORT = 'localhost', 6333
COLLECTION = 'mem0_main'

def get_embedding(text: str) -> list:
    """调用 SiliconFlow API 获取 embedding 向量"""
    client = OpenAI(api_key=API_KEY, base_url=OPENAI_BASE_URL)
    resp = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text[:2000]  # 限制长度
    )
    return resp.data[0].embedding

def extract_clean_text(data: str) -> str:
    """去掉分类前缀，还原原文"""
    return re.sub(r'^\[.*?\]\[score:\d+\]\s*', '', data).strip()

def main():
    client = QdrantClient(url=f'http://{QDRANT_HOST}:{QDRANT_PORT}')

    # 读取所有记录
    print("📖 读取所有记录...")
    all_records = []
    offset = None
    while True:
        result = client.scroll(collection_name=COLLECTION, limit=200, offset=offset)
        if not result[0]:
            break
        for p in result[0]:
            data = p.payload.get('data', '')
            if data:
                all_records.append({
                    'id': p.id,
                    'data': data,
                    'payload': dict(p.payload)
                })
        offset = result[1]
        if offset is None:
            break

    total = len(all_records)
    print(f"共 {total} 条记录")

    # 统计已有向量的
    with_vectors = sum(1 for r in all_records if r.get('has_vector'))
    print(f"已有向量: {with_vectors}")

    total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    processed = 0

    for batch_idx in range(total_batches):
        s = batch_idx * BATCH_SIZE
        e = min(s + BATCH_SIZE, total)
        batch = all_records[s:e]

        print(f"\n[{batch_idx+1}/{total_batches}] 生成 {s+1}-{e} 条向量...")

        points = []
        for record in batch:
            clean = extract_clean_text(record['data'])
            if not clean:
                continue

            try:
                vector = get_embedding(clean)
                point = PointStruct(
                    id=record['id'],
                    vector=vector,
                    payload=record['payload']
                )
                points.append(point)
                print(f"  ✅ {str(record['id'])[:8]}... ({len(vector)}维)")
            except Exception as e:
                print(f"  ❌ {str(record['id'])[:8]}... error: {e}", file=sys.stderr)

        if points:
            # 先删再重建（因为向量变了）
            ids_to_delete = [p.id for p in points]
            from qdrant_client.models import PointIdsList
            client.delete(collection_name=COLLECTION, points_selector=PointIdsList(points=[str(i) for i in ids_to_delete]))
            time.sleep(0.5)
            client.upsert(collection_name=COLLECTION, points=points)
            processed += len(points)
            print(f"  💾 已写入 {len(points)} 条向量")

        if batch_idx < total_batches - 1:
            print(f"  ⏳ 等待 {BATCH_DELAY}s...")
            time.sleep(BATCH_DELAY)

    print(f"\n🎉 完成! 已处理 {processed}/{total} 条")

    # 验证
    info = client.get_collection(COLLECTION)
    print(f"\n验证: {info.points_count} 条记录, {info.indexed_vectors_count} 条有向量")

if __name__ == '__main__':
    main()
