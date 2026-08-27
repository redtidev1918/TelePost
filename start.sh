#!/bin/bash
# TelePost 启动脚本

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

# 检查 Python 版本
check_python() {
    if ! command -v python3 &> /dev/null; then
        log_error "Python3 未安装！"
        exit 1
    fi
    
    PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
    REQUIRED_VERSION="3.9"
    
    if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
        log_error "Python 版本 $PYTHON_VERSION 过低，需要 >= $REQUIRED_VERSION"
        exit 1
    fi
    
    log_success "Python $PYTHON_VERSION 检查通过"
}

# 检查配置文件
check_config() {
    if [ ! -f "config.ini" ]; then
        log_warning "未找到 config.ini"
        if [ -f "config.ini.example" ]; then
            log_info "从模板创建配置文件..."
            cp config.ini.example config.ini
            log_warning "请编辑 config.ini 填入以下信息："
            log_info "  - TOKEN: 从 @BotFather 获取的 Bot Token"
            log_info "  - CHANNEL_ID: 频道 ID（如 @channel_name）"
            log_info "  - OWNER_ID: 您的 Telegram User ID"
            log_info ""
            log_info "配置完成后请重新运行: ./start.sh"
            exit 0
        else
            log_error "未找到配置模板文件 config.ini.example"
            exit 1
        fi
    fi
    
    # 使用 Python 检查配置
    if [ -f "check_config.py" ]; then
        log_info "检查配置文件..."
        python3 check_config.py
    fi
    
    log_success "配置文件检查通过"
}

# 检查依赖
check_dependencies() {
    log_info "检查 Python 依赖..."
    
    if [ -f "requirements.txt" ]; then
        # 检查关键依赖是否安装
        if ! python3 -c "import telegram" 2>/dev/null; then
            log_warning "依赖未安装，正在安装..."
            pip3 install -r requirements.txt
        else
            log_success "依赖已安装"
        fi
    else
        log_error "未找到 requirements.txt"
        exit 1
    fi
}

# 创建必要目录
create_directories() {
    log_info "创建必要目录..."
    mkdir -p data logs backups data/search_index
    log_success "目录创建完成"
}

# 检查进程
check_running() {
    if pgrep -f "python.*main.py" > /dev/null; then
        log_warning "机器人已在运行中"
        log_info "如需重启，请先运行: pkill -f 'python.*main.py'"
        exit 0
    fi
}

# 启动机器人
start_bot() {
    log_info "启动机器人..."
    
    # 后台运行
    nohup python3 main.py > logs/bot.log 2>&1 &
    
    PID=$!
    sleep 2
    
    if ps -p $PID > /dev/null; then
        log_success "机器人启动成功！PID: $PID"
        log_info "查看日志: tail -f logs/bot.log"
        log_info "停止机器人: pkill -f 'python.*main.py'"
    else
        log_error "机器人启动失败！"
        log_info "请查看日志: cat logs/bot.log"
        exit 1
    fi
}

# 主函数
main() {
    echo ""
    log_info "=== TelePost 启动 ==="
    echo ""
    
    check_python
    check_config
    check_dependencies
    create_directories
    check_running
    start_bot
    
    echo ""
    log_success "启动完成！"
    echo ""
}

main "$@"

