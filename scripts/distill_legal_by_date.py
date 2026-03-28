#!/usr/bin/env python3
"""
legal agent 记忆蒸馏循环脚本 v2
按自然日逐批处理对话内容中的日期，而非文件修改时间
"""
import os, sys, re, json, time, subprocess, argparse
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Tuple

# ========== 配置 ==========
AGENT = "legal"
SESSIONS_DIR = f"/root/.openclaw/agents/{AGENT}/sessions"
SCRIPT_DIR = "/root/.openclaw/mem0-agent-setup/scripts"
DISTILL_SCRIPT = f"{SCRIPT_DIR}/memory_distill_daily.py"
STATE_FILE = f"/root/.openclaw/workspace/.distill_state_{AGENT}.json"
LOG_DIR = "/root/.openclaw/workspace/logs"
TIMEOUT_PER_RUN = 1800  # 30分钟超时

os.makedirs(LOG_DIR, exist_ok=True)

# 加载 .env
ENV_FILE = "/root/.openclaw/mem0-agent-setup/.env"
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

# ========== 解析 session 文件，提取每条消息的日期 ==========

def extract_timestamp_from_content(content: str) -> str:
    """从 System: 头提取时间戳，返回 YYYY-MM-DD 格式"""
    m = re.search(r'\[(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2} GMT', content)
    if m:
        return m.group(1)
    return None

def extract_user_content(text: str) -> str:
    """提取用户消息内容"""
    if text.startswith("System:"):
        m = re.search(r'Sender \(untrusted metadata\):[\s\S]+?\n\n([\s\S]+)$', text)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return text.strip()

def read_sessions_by_date(sessions_dir: str) -> Dict[str, List[dict]]:
    """读取所有 session 文件，按对话日期分组"""
    date_convs = defaultdict(list)  # date -> list of conversation dicts
    session_files = list(Path(sessions_dir).glob("*.jsonl"))

    print(f"📁 发现 {len(session_files)} 个 session 文件")

    for sf in session_files:
        try:
            with open(sf) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if obj.get("type") != "message":
                            continue

                        msg = obj.get("message", {})
                        role = msg.get("role", "")
                        content = msg.get("content", "")

                        if isinstance(content, list):
                            content = " ".join(c.get("text", "") for c in content if c.get("type") == "text")

                        if role not in ("user", "assistant") or not content.strip():
                            continue

                        date = extract_timestamp_from_content(content)
                        if not date:
                            continue

                        clean = extract_user_content(content) if role == "user" else content.strip()
                        if not clean or len(clean) < 5:
                            continue

                        date_convs[date].append({
                            "session": sf.name,
                            "role": role,
                            "content": clean[:500],
                            "date": date
                        })
                    except Exception as e:
                        pass
        except Exception as e:
            print(f"  ⚠️  读取 {sf.name} 出错: {e}")

    return dict(date_convs)

def distill_by_date(date: str, conversations: List[dict], session_files: List[str]) -> bool:
    """蒸馏指定日期的对话"""
    if not conversations:
        print(f"  日期 {date} 没有对话，跳过")
        return True

    print(f"  📝 共 {len(conversations)} 条对话")

    # 创建临时 session 文件
    tmp_file = f"/tmp/legal_distill_temp_{date}.jsonl"
    with open(tmp_file, "w") as f:
        for conv in conversations:
            obj = {
                "type": "message",
                "message": {
                    "role": conv["role"],
                    "content": conv["content"]
                }
            }
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    print(f"  📄 临时文件: {tmp_file}")

    # 写临时状态文件，让 distill 从指定日期开始
    # 使用 force 模式 + --days=1，distill 会处理该日期的文件
    # 但我们需要确保它只处理当天的
    # 策略：先获取该日期范围内的文件列表，替换 distill 脚本的逻辑

    # 直接调用 distill，但需要修改调用方式
    # distill 按文件修改时间过滤，我们用 touch 确保只有对应文件被处理

    # 1. 先备份原状态
    state_backup = None
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state_backup = json.load(f)

    # 2. 写新状态：让 last_distilled_at 为前一天，这样 --days=1 会包含当天
    target_date = datetime.strptime(date, "%Y-%m-%d")
    cutoff_date = target_date - timedelta(days=1)
    temp_state = {"last_distilled_at": cutoff_date.isoformat()}

    with open(STATE_FILE, "w") as f:
        json.dump(temp_state, f)

    # 3. Touch 临时文件，使其修改时间在目标日期
    os.utime(tmp_file, (target_date.timestamp(), target_date.timestamp()))

    # 4. 运行 distill
    print(f"  🔄 运行蒸馏...")
    cmd = [
        "timeout", str(TIMEOUT_PER_RUN),
        "python3", DISTILL_SCRIPT,
        "--agent", AGENT,
        "--days", "1",
        "--force",
        "--yes"
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_PER_RUN + 30,
            cwd=SCRIPT_DIR
        )

        if result.returncode == 124:
            print(f"  ⚠️  超时 ({TIMEOUT_PER_RUN}s)")
            return False

        if result.returncode != 0:
            print(f"  ⚠️  distill 退出码: {result.returncode}")
            if result.stderr:
                print(f"     stderr: {result.stderr[:500]}")

        # 写日志
        log_file = f"{LOG_DIR}/distill_{AGENT}_{date}.log"
        with open(log_file, "w") as f:
            f.write(f"# Distill for {date}\n")
            f.write(f"# Conversations: {len(conversations)}\n")
            f.write(f"# Return code: {result.returncode}\n\n")
            f.write("## STDOUT:\n")
            f.write(result.stdout)
            f.write("\n## STDERR:\n")
            f.write(result.stderr)

        return result.returncode == 0

    except subprocess.TimeoutExpired:
        print(f"  ⚠️  子进程超时")
        return False
    finally:
        # 5. 恢复原状态
        if state_backup:
            with open(STATE_FILE, "w") as f:
                json.dump(state_backup, f)
        else:
            os.remove(STATE_FILE)

        # 清理临时文件
        if os.path.exists(tmp_file):
            os.remove(tmp_file)

def main():
    print("=" * 60)
    print("  Legal Agent 记忆蒸馏循环 (按自然日)")
    print("=" * 60)
    print()

    # 读取所有对话并按日期分组
    date_convs = read_sessions_by_date(SESSIONS_DIR)

    if not date_convs:
        print("❌ 没有找到任何对话")
        return

    # 按日期排序（从早到晚）
    dates = sorted(date_convs.keys())
    TOTAL_DAYS = len(dates)

    print(f"\n📅 共覆盖 {TOTAL_DAYS} 个自然日:")
    for d in dates:
        print(f"   {d}: {len(date_convs[d])} 条对话")

    print()
    print("=" * 60)

    # 确认开始
    print("\n🚀 开始逐日蒸馏（Ctrl+C 可随时中断）")
    print()

    PROCESSED = 0
    FAILED = []

    for i, date in enumerate(dates):
        PROCESSED += 1
        print()
        print(f"╔═══════════════════════════════════════════════════════════╗")
        print(f"║ 📅 [{PROCESSED}/{TOTAL_DAYS}] 处理日期: {date}                       ║")
        print(f"╚═══════════════════════════════════════════════════════════╝")

        convs = date_convs[date]
        session_files = list(set(c["session"] for c in convs))
        print(f"   📁 涉及 {len(session_files)} 个 session 文件")
        print(f"   💬 {len(convs)} 条对话")

        success = distill_by_date(date, convs, session_files)

        if success:
            print(f"   ✅ 完成")
        else:
            print(f"   ⚠️  有问题，继续下一批")
            FAILED.append(date)

        print()
        print(f"⏸  休息 5 秒...")
        time.sleep(5)

    # 最终总结
    print()
    print("=" * 60)
    print("🎉 全部完成！")
    print(f"   总天数: {TOTAL_DAYS}")
    print(f"   成功: {TOTAL_DAYS - len(FAILED)}")
    print(f"   失败: {len(FAILED)}")
    if FAILED:
        print(f"   失败日期: {', '.join(FAILED)}")
    print(f"   日志: {LOG_DIR}/distill_{AGENT}_*.log")
    print("=" * 60)

if __name__ == "__main__":
    main()
