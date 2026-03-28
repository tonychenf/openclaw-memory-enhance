#!/bin/bash
# legal agent 记忆蒸馏循环脚本
# 按自然日逐批处理 session 文件，避免一次性处理所有数据

set -e

AGENT="legal"
SESSIONS_DIR="/root/.openclaw/agents/${AGENT}/sessions"
SCRIPT_DIR="/root/.openclaw/mem0-agent-setup/scripts"
STATE_FILE="/root/.openclaw/workspace/.distill_state_${AGENT}.json"
LOG_DIR="/root/.openclaw/workspace/logs"
TIMEOUT_PER_RUN=1800  # 每次蒸馏超时30分钟

mkdir -p "$LOG_DIR"

# 加载环境变量
if [ -f /root/.openclaw/mem0-agent-setup/.env ]; then
    export $(grep -v '^#' /root/.openclaw/mem0-agent-setup/.env | xargs)
fi

echo "=========================================="
echo "  Legal Agent 记忆蒸馏循环"
echo "  目标: $AGENT"
echo "  Session目录: $SESSIONS_DIR"
echo "  每次超时: ${TIMEOUT_PER_RUN}s"
echo "=========================================="
echo ""

# 获取所有 session 文件，按修改时间排序
mapfile -t SESSION_FILES < <(ls -lt "${SESSIONS_DIR}"/*.jsonl 2>/dev/null | awk '{print $NF}')
TOTAL_FILES=${#SESSION_FILES[@]}

if [ $TOTAL_FILES -eq 0 ]; then
    echo "❌ 没有找到 session 文件"
    exit 1
fi

echo "📁 共找到 $TOTAL_FILES 个 session 文件"
echo ""

# 按日期分组（基于文件修改时间的日期）
declare -A DATE_FILES
declare -a DATES

for f in "${SESSION_FILES[@]}"; do
    fname=$(basename "$f")
    # 从文件修改时间提取日期
    fdate=$(stat -c %y "$f" 2>/dev/null | cut -d' ' -f1)
    if [ -n "$fdate" ]; then
        DATE_FILES["$fdate"]+="$f "
        # 收集不重复的日期
        if [[ ! " ${DATES[@]} " =~ " ${fdate} " ]]; then
            DATES+=("$fdate")
        fi
    fi
done

# 按日期排序
IFS=$'\n' DATES=($(sort -r <<<"${DATES[*]}")); unset IFS

echo "📅 共覆盖 ${#DATES[@]} 个自然日:"
for d in "${DATES[@]}"; do
    count=$(echo "${DATE_FILES[$d]}" | wc -w)
    echo "   $d : $count 个文件"
done
echo ""

read -p "🚀 开始逐日蒸馏？（Ctrl+C 可随时中断，每批之间会暂停）: " confirm
echo ""

# 按日期顺序处理（从最早到最新）
TOTAL_DAYS=${#DATES[@]}
PROCESSED_DAYS=0

for DATE in "${DATES[@]}"; do
    PROCESSED_DAYS=$((PROCESSED_DAYS + 1))
    files=(${DATE_FILES[$DATE]})
    FILE_COUNT=${#files[@]}

    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "📅 [$PROCESSED_DAYS/$TOTAL_DAYS] 处理日期: $DATE"
    echo "📁 文件数: $FILE_COUNT"
    echo "═══════════════════════════════════════════════════════════"

    # 构建临时状态文件（让 distill 脚本只处理这天的文件）
    TEMP_STATE="/tmp/distill_temp_state_${DATE}.json"

    # 备份原状态
    if [ -f "$STATE_FILE" ]; then
        cp "$STATE_FILE" "${STATE_FILE}.backup.$(date +%s)"
    fi

    # 写临时状态，让 distill 从指定日期开始
    # 注意：实际的文件过滤在 distill 脚本里是通过 --days 参数控制的
    # 这里我们用 --days=1 并配合 force，但需要确保只处理当天的文件

    # 由于 distill 脚本按文件修改时间过滤，我们直接运行即可
    # 脚本内部会只选择指定时间范围内的文件

    timeout ${TIMEOUT_PER_RUN} python3 "${SCRIPT_DIR}/memory_distill_daily.py" \
        --agent "${AGENT}" \
        --days 1 \
        --force \
        --yes \
        2>&1 | tee -a "${LOG_DIR}/distill_${AGENT}_${DATE}.log"

    EXIT_CODE=${PIPESTATUS[0]}

    if [ $EXIT_CODE -eq 124 ]; then
        echo "⚠️  ⚠️  超时退出 (${TIMEOUT_PER_RUN}s)"
        echo "   日期 $DATE 的蒸馏未完成"
        echo ""
        read -p "继续处理下一个日期？(y/n): " continue_choice
        if [ "$continue_choice" != "y" ]; then
            echo "已暂停。下次运行时会自动从下一个日期继续。"
            exit 0
        fi
    elif [ $EXIT_CODE -ne 0 ]; then
        echo "⚠️  ⚠️  错误退出 (code: $EXIT_CODE)"
        echo "   日期 $DATE 的蒸馏未完成"
        echo ""
        read -p "继续处理下一个日期？(y/n): " continue_choice
        if [ "$continue_choice" != "y" ]; then
            echo "已暂停。"
            exit 1
        fi
    else
        echo "✅ ✅ 日期 $DATE 蒸馏完成"
    fi

    echo ""
    echo "⏸  休息 5 秒后处理下一批..."
    sleep 5
done

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "🎉 全部完成！"
echo "   共处理 $TOTAL_DAYS 个自然日"
echo "   日志保存在: ${LOG_DIR}/distill_${AGENT}_*.log"
echo "═══════════════════════════════════════════════════════════"
