#!/usr/bin/env python3
"""
cleanup_noise_realtime.py
清理 mem0 实时记忆中的噪音记录（Cron任务输出、Agent指令、HEARTBEAT等）
"""
import requests
import sys

def cleanup_collection(collection):
    url = f'http://localhost:6333/collections/{collection}/points/scroll'
    all_pts = []
    offset = None

    # 读取所有 layer=realtime 的记录（无上限）
    while True:
        body = {
            'limit': 100,
            'offset': offset,
            'with_payload': True,
            'with_vectors': False,
            'filter': {'must': [{'key': 'layer', 'match': {'value': 'realtime'}}]}
        }
        try:
            r = requests.post(url, headers={'Content-Type': 'application/json'}, json=body)
            result = r.json()
            if result.get('status') != 'ok':
                print(f'Error: {result}'); break
            pts = result.get('result', {}).get('points', [])
            if not pts: break
            all_pts.extend(pts)
            offset = result.get('result', {}).get('next_page_offset')
            if offset is None: break
        except Exception as e:
            print(f'Scroll error: {e}'); break

    def is_noise(d):
        d_stripped = d.strip()
        # 1. Cron任务特征：data中包含[cron:xxx]标记
        if '[cron:' in d:
            return True
        # 2. 短文本的agent指令（纯指令，无实质对话内容）
        if len(d_stripped) < 400 and '你是' in d_stripped and 'agent' in d_stripped.lower() and '请执行' in d_stripped:
            return True
        # 3. heartbeat
        if d_stripped in ('HEARTBEAT_OK', 'HEARTBEAT_TIMEOUT'):
            return True
        # 4. OpenClaw内部标记
        if d_stripped.startswith('<<<BEGIN_OPENCLAW'):
            return True
        if d_stripped.startswith('[Internal') or d_stripped.startswith('Queued #'):
            return True
        # 5. System: 包装的消息内容（session包装泄露）
        if d_stripped.startswith('System (untrusted)'):
            return True
        if d_stripped.startswith('System:') and ('Conversation info' in d_stripped or 'message_id' in d_stripped):
            return True
        # 6. Exec completed 通知
        if 'Exec completed' in d_stripped and len(d_stripped) < 500:
            return True
        return False

    noisy_ids = []
    clean = 0
    for p in all_pts:
        d = p['payload'].get('data', '')
        if is_noise(d):
            noisy_ids.append(p['id'])
        else:
            clean += 1

    total = len(all_pts)
    print(f'[{collection}] Total realtime: {total}, Clean: {clean}, Noisy: {len(noisy_ids)}')

    if not noisy_ids:
        print('  Nothing to clean.'); return

    # 批量删除（Qdrant单次最多100条）
    delete_url = f'http://localhost:6333/collections/{collection}/points/delete'
    batch_size = 50
    deleted = 0
    for i in range(0, len(noisy_ids), batch_size):
        batch = noisy_ids[i:i+batch_size]
        body = {'points': batch}
        try:
            r = requests.post(delete_url, headers={'Content-Type': 'application/json'}, json=body)
            result = r.json()
            if result.get('status') == 'ok':
                deleted += len(batch)
                print(f'  Deleted batch {i//batch_size + 1}: {len(batch)} records')
            else:
                print(f'  Delete failed: {result}')
        except Exception as e:
            print(f'  Delete error: {e}')

    print(f'  Total deleted: {deleted}/{len(noisy_ids)}')

if __name__ == '__main__':
    collections = [
        'mem0_main',
        'mem0_dev',
        'mem0_capital',
        'mem0_legal',
        'mem0_ops',
        'mem0_bingbu',
        'mem0_gongbu',
        'mem0_libu',
        'mem0_libu_hr',
        'mem0_menxia',
        'mem0_rich',
        'mem0_xingbu',
        'mem0_zaochao',
        'mem0_zhongshu',
        'mem0_shangshu',
        'mem0_taizi',
        'mem0_hubu',
    ]

    if len(sys.argv) > 1:
        collections = [sys.argv[1]]

    for col in collections:
        try:
            cleanup_collection(col)
        except Exception as e:
            print(f'[{col}] Error: {e}')
