#!/usr/bin/env python3
"""
is_noise.py - 公共噪音判断函数
被 sync_to_mem0.py 和 memory_distill_daily.py 共用
"""

def is_noise_content(text: str) -> bool:
    """
    判断内容是否为噪音（系统/cron/agent指令），这些不值得写入 realtime 层
    
    判断标准：
    - Cron任务输出：[cron:xxx] 标记
    - Agent指令：短文本 + "你是" + "agent" + "请执行"
    - Heartbeat：HEARTBEAT_OK / HEARTBEAT_TIMEOUT
    - OpenClaw内部上下文泄露
    - System包装残留（Conversation info / message_id）
    - Exec completed 通知
    - session reset 文件路径
    """
    t = text.strip()
    
    # 1. Cron任务输出
    if '[cron:' in t:
        return True
    # 2. Agent指令：短文本 + 关键词组合
    if len(t) < 400 and '你是' in t and 'agent' in t.lower() and '请执行' in t:
        return True
    # 3. Heartbeat
    if t in ('HEARTBEAT_OK', 'HEARTBEAT_TIMEOUT'):
        return True
    # 4. OpenClaw内部上下文泄露
    if '<<<BEGIN_OPENCLAW' in t:
        return True
    if t.startswith('[Internal') or t.startswith('Queued #'):
        return True
    # 5. System包装残留
    if 'Conversation info' in t and 'message_id' in t:
        return True
    # 6. Exec completed 通知（收紧到200字符，避免误伤正常短消息）
    if 'Exec completed' in t and len(t) < 200:
        return True
    # 7. session reset 文件路径
    if '.jsonl.reset.' in t:
        return True
    
    return False
