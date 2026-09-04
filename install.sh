#!/bin/bash
# TelePost 一键安装脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

log_step() {
    echo -e "\n${CYAN}${BOLD}▶ $1${NC}\n"
}

# 打印欢迎信息
print_welcome() {
    clear
    echo -e "${CYAN}${BOLD}"
    echo "╔════════════════════════════════════════╗"
    echo "║   TelePost 一键安装向导          ║"
    echo "║   Telegram 频道投稿机器人             ║"
    echo "╚════════════════════════════════════════╝"
    echo -e "${NC}"
}

# 检测操作系统
detect_os() {
    log_step "检测系统环境"
    
    OS="unknown"
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        OS="linux"
        log_success "检测到 Linux 系统"
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
        log_success "检测到 macOS 系统"
    else
        log_error "不支持的操作系统: $OSTYPE"
        exit 1
    fi
}

# 检查 Python
check_python() {
    log_step "检查 Python 环境"
    
    if ! command -v python3 &> /dev/null; then
        log_error "未找到 Python3！"
        log_info "请先安装 Python 3.9 或更高版本"
        
        if [ "$OS" = "macos" ]; then
            log_info "macOS 安装命令: brew install python3"
        elif [ "$OS" = "linux" ]; then
            log_info "Ubuntu/Debian: sudo apt install python3 python3-pip"
            log_info "CentOS/RHEL: sudo yum install python3 python3-pip"
        fi
        exit 1
    fi
    
    PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
    log_success "Python $PYTHON_VERSION"
    
    # 检查版本
    if [ "$(printf '%s\n' "3.9" "$PYTHON_VERSION" | sort -V | head -n1)" != "3.9" ]; then
        log_error "Python 版本过低（需要 >= 3.9）"
        exit 1
    fi
    
    # 检查 pip
    if ! command -v pip3 &> /dev/null; then
        log_warning "未找到 pip3，尝试安装..."
        python3 -m ensurepip --default-pip || {
            log_error "pip3 安装失败"
            exit 1
        }
    fi

    log_success "pip3 已安装"

    # 检查 venv 模块（Debian/Ubuntu 的 python3 不含 venv，需单独装 python3-venv）
    if ! python3 -c 'import venv' &>/dev/null; then
        if [ -f /etc/debian_version ]; then
            log_warning "缺少 venv 模块，尝试安装 python3-venv ..."
            sudo apt-get update -qq && sudo apt-get install -y -qq python3-venv || {
                log_error "python3-venv 安装失败，请手动执行：sudo apt install python3-venv"
                exit 1
            }
        else
            log_error "缺少 venv 模块。Debian/Ubuntu: sudo apt install python3-venv；CentOS/RHEL: sudo yum install python3"
            exit 1
        fi
    fi
    log_success "venv 模块可用"
}

# 创建目录结构
create_directories() {
    log_step "创建目录结构"
    
    mkdir -p data logs backups data/search_index
    log_success "目录创建完成"
}

# 安装依赖
install_dependencies() {
    log_step "安装 Python 依赖"
    
    if [ ! -f "requirements.txt" ]; then
        log_error "未找到 requirements.txt"
        exit 1
    fi
    
    log_info "这可能需要几分钟..."
    pip3 install -r requirements.txt -q
    
    log_success "依赖安装完成"
}

# 配置向导
configure_bot() {
    log_step "配置机器人"
    
    # 检查是否已有配置
    if [ -f "config.ini" ]; then
        log_warning "发现现有配置文件"
        read -p "是否覆盖现有配置？(y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "保留现有配置"
            return
        fi
    fi
    
    # 从模板创建
    if [ ! -f "config.ini.example" ]; then
        log_error "未找到配置模板 config.ini.example"
        exit 1
    fi
    
    cp config.ini.example config.ini
    
    echo ""
    echo -e "${BOLD}请提供以下配置信息：${NC}"
    echo ""
    
    # Bot Token
    while true; do
        read -p "Bot Token (从 @BotFather 获取): " BOT_TOKEN
        if [ ! -z "$BOT_TOKEN" ]; then
            break
        fi
        log_error "Token 不能为空！"
    done
    
    # Channel ID
    while true; do
        read -p "频道 ID (如 @mychannel 或 -100123456789): " CHANNEL_ID
        if [ ! -z "$CHANNEL_ID" ]; then
            break
        fi
        log_error "频道 ID 不能为空！"
    done
    
    # Owner ID
    while true; do
        read -p "管理员 ID (向 @userinfobot 发消息获取): " OWNER_ID
        if [[ "$OWNER_ID" =~ ^[0-9]+$ ]]; then
            break
        fi
        log_error "请输入有效的数字 ID！"
    done
    
    # 运行模式选择
    echo ""
    echo -e "${BOLD}选择运行模式：${NC}"
    echo "  1) Auto 模式 - 有公网 HTTPS 地址时 Webhook，否则 Polling（推荐）"
    echo "  2) Polling 模式 (轮询) - 用于本地或无公网服务器"
    echo "  3) Webhook 模式 - 强制使用 Webhook"
    echo ""
    while true; do
        read -p "请选择 (1/2/3) [默认: 1]: " RUN_MODE_CHOICE
        RUN_MODE_CHOICE=${RUN_MODE_CHOICE:-1}
        if [[ "$RUN_MODE_CHOICE" == "1" ]]; then
            RUN_MODE="AUTO"
            log_success "已选择 Auto 模式"
            echo ""
            log_info "如有公网 HTTPS 地址请填写；留空将自动使用 Polling"
            while true; do
                read -p "Webhook URL (可留空): " WEBHOOK_URL
                if [ -z "$WEBHOOK_URL" ] || [[ "$WEBHOOK_URL" =~ ^https:// ]]; then
                    break
                fi
                log_error "Webhook URL 必须以 https:// 开头"
            done
            break
        elif [[ "$RUN_MODE_CHOICE" == "2" ]]; then
            RUN_MODE="POLLING"
            log_success "已选择 Polling 模式"
            WEBHOOK_URL=""
            break
        elif [[ "$RUN_MODE_CHOICE" == "3" ]]; then
            RUN_MODE="WEBHOOK"
            log_success "已选择 Webhook 模式"
            echo ""
            log_info "Webhook 模式需要一个公网 HTTPS 地址"
            log_info "示例: https://your-domain.com 或 https://your-app.fly.dev"
            echo ""
            while true; do
                read -p "Webhook URL (可留空稍后配置): " WEBHOOK_URL
                if [ -z "$WEBHOOK_URL" ]; then
                    log_warning "Webhook URL 未设置，需要稍后在 config.ini 中配置"
                    break
                elif [[ "$WEBHOOK_URL" =~ ^https:// ]]; then
                    log_success "Webhook URL: $WEBHOOK_URL"
                    break
                else
                    log_error "Webhook URL 必须以 https:// 开头"
                fi
            done
            break
        else
            log_error "无效选择，请输入 1、2 或 3"
        fi
    done
    
    # 写入配置
    if [ "$OS" = "macos" ]; then
        sed -i '' "s/TOKEN = .*/TOKEN = $BOT_TOKEN/" config.ini
        sed -i '' "s/CHANNEL_ID = .*/CHANNEL_ID = $CHANNEL_ID/" config.ini
        sed -i '' "s/OWNER_ID = .*/OWNER_ID = $OWNER_ID/" config.ini
        sed -i '' "s/RUN_MODE = .*/RUN_MODE = $RUN_MODE/" config.ini
        if [ ! -z "$WEBHOOK_URL" ]; then
            sed -i '' "s|URL = .*|URL = $WEBHOOK_URL|" config.ini
        fi
    else
        sed -i "s/TOKEN = .*/TOKEN = $BOT_TOKEN/" config.ini
        sed -i "s/CHANNEL_ID = .*/CHANNEL_ID = $CHANNEL_ID/" config.ini
        sed -i "s/OWNER_ID = .*/OWNER_ID = $OWNER_ID/" config.ini
        sed -i "s/RUN_MODE = .*/RUN_MODE = $RUN_MODE/" config.ini
        if [ ! -z "$WEBHOOK_URL" ]; then
            sed -i "s|URL = .*|URL = $WEBHOOK_URL|" config.ini
        fi
    fi
    
    log_success "配置已保存到 config.ini"
    
    # 显示配置摘要
    echo ""
    echo -e "${CYAN}${BOLD}配置摘要：${NC}"
    echo -e "  运行模式: ${BOLD}$RUN_MODE${NC}"
    if [ "$RUN_MODE" != "POLLING" ] && [ ! -z "$WEBHOOK_URL" ]; then
        echo -e "  Webhook URL: ${BOLD}$WEBHOOK_URL${NC}"
    fi
    echo ""
}

# 验证配置
verify_config() {
    log_step "验证配置"
    
    if [ -f "check_config.py" ]; then
        python3 check_config.py || {
            log_error "配置验证失败，请检查 config.ini"
            exit 1
        }
    fi
    
    log_success "配置验证通过"
}

# 初始化数据库
init_database() {
    log_step "初始化数据库"
    
    python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from database.db_manager import init_db
    init_db()
    print('数据库初始化成功')
except Exception as e:
    print(f'数据库初始化失败: {e}')
    sys.exit(1)
" || {
        log_error "数据库初始化失败"
        exit 1
    }
    
    log_success "数据库已初始化"
}

# 询问是否启动
ask_start() {
    log_step "安装完成"
    
    echo ""
    echo -e "${GREEN}${BOLD}✓ 安装成功完成！${NC}"
    echo ""
    
    read -p "是否立即启动机器人？(y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        log_info "启动机器人..."
        ./start.sh
    else
        echo ""
        log_info "稍后可以使用以下命令启动："
        echo -e "  ${CYAN}./start.sh${NC}"
        echo ""
    fi
}

# 显示帮助信息
show_help() {
    echo ""
    echo -e "${BOLD}常用命令：${NC}"
    echo -e "  ${CYAN}./start.sh${NC}              - 启动机器人"
    echo -e "  ${CYAN}./restart.sh${NC}            - 重启机器人"
    echo -e "  ${CYAN}./update.sh${NC}             - 更新代码"
    echo -e "  ${CYAN}tail -f logs/bot.log${NC}    - 查看日志"
    echo ""
    echo -e "${BOLD}获取帮助：${NC}"
    echo -e "  文档: ${CYAN}cat README.md${NC}"
    echo -e "  配置: ${CYAN}cat config.ini${NC}"
    echo ""
}

# 主函数
main() {
    print_welcome
    
    detect_os
    check_python
    create_directories
    install_dependencies
    configure_bot
    verify_config
    init_database
    ask_start
    show_help
    
    echo -e "${GREEN}${BOLD}感谢使用 TelePost！${NC}\n"
}

# 运行主函数
main "$@"
