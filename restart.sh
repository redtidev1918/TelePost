#!/bin/bash
# TelePost 重启脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 停止机器人
stop_bot() {
    log_info "停止机器人..."
    
    # 检查是否有运行的进程
    if pgrep -f "python.*main.py" > /dev/null; then
        pkill -f "python.*main.py"
        sleep 2
        
        # 强制停止（如果还在运行）
        if pgrep -f "python.*main.py" > /dev/null; then
            log_warning "正常停止失败，强制终止..."
            pkill -9 -f "python.*main.py"
            sleep 1
        fi
        
        log_success "机器人已停止"
    else
        log_warning "未发现运行中的机器人进程"
    fi
}

# 启动机器人
start_bot() {
    log_info "启动机器人..."
    
    # 检查配置文件
    if [ ! -f "config.ini" ]; then
        log_error "未找到 config.ini 配置文件"
        log_info "请先运行: ./install.sh"
        exit 1
    fi
    
    # 创建日志目录
    mkdir -p logs
    
    # 后台启动
    nohup python3 main.py > logs/bot.log 2>&1 &
    
    PID=$!
    sleep 2
    
    # 验证是否启动成功
    if ps -p $PID > /dev/null 2>&1; then
        log_success "机器人启动成功！PID: $PID"
        log_info "查看日志: tail -f logs/bot.log"
    else
        log_error "机器人启动失败！"
        log_info "查看错误信息: cat logs/bot.log"
        exit 1
    fi
}

# 主函数
main() {
    echo ""
    log_info "=== TelePost 重启 ==="
    echo ""
    
    # 检查参数
    if [ "$1" = "--stop" ]; then
        stop_bot
        echo ""
        log_info "仅停止模式，不重新启动"
        echo ""
        exit 0
    fi
    
    # 重启流程
    stop_bot
    echo ""
    start_bot
    
    echo ""
    log_success "重启完成！"
    echo ""
}

main "$@"

