#!/bin/bash
# TelePost 快速启动向导

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
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

# 打印欢迎信息
print_welcome() {
    clear
    echo -e "${CYAN}${BOLD}"
    cat << "EOF"
╔════════════════════════════════════════════════╗
║                                                ║
║      TelePost 快速启动向导               ║
║      Telegram Channel Submission Bot          ║
║                                                ║
╚════════════════════════════════════════════════╝
EOF
    echo -e "${NC}\n"
}

# 检测环境
detect_environment() {
    echo -e "${BOLD}正在检测系统环境...${NC}\n"
    
    # 检测操作系统
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        OS="Linux"
        OS_ICON="🐧"
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macOS"
        OS_ICON="🍎"
    else
        OS="Unknown"
        OS_ICON="❓"
    fi
    
    # 检测 Python
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 --version | awk '{print $2}')
        PYTHON_OK="✓"
    else
        PYTHON_VERSION="未安装"
        PYTHON_OK="✗"
    fi
    
    # 检测 Docker
    if command -v docker &> /dev/null; then
        DOCKER_VERSION=$(docker --version | awk '{print $3}' | tr -d ',')
        DOCKER_OK="✓"
    else
        DOCKER_VERSION="未安装"
        DOCKER_OK="✗"
    fi
    
    # 检测 Git
    if command -v git &> /dev/null; then
        GIT_VERSION=$(git --version | awk '{print $3}')
        GIT_OK="✓"
    else
        GIT_VERSION="未安装"
        GIT_OK="✗"
    fi
    
    # 显示环境信息
    echo -e "${CYAN}系统环境检测结果：${NC}\n"
    echo -e "  ${OS_ICON}  操作系统: ${BOLD}$OS${NC}"
    echo -e "  [$PYTHON_OK] Python:   ${BOLD}$PYTHON_VERSION${NC}"
    echo -e "  [$DOCKER_OK] Docker:   ${BOLD}$DOCKER_VERSION${NC}"
    echo -e "  [$GIT_OK] Git:      ${BOLD}$GIT_VERSION${NC}"
    echo ""
}

# 选择部署方式
choose_deployment() {
    echo -e "${BOLD}请选择部署方式：${NC}\n"
    
    echo -e "  ${GREEN}1${NC}) ${BOLD}一键安装${NC} - 完整的交互式安装（推荐新手）"
    echo -e "     • 自动安装依赖"
    echo -e "     • 配置向导"
    echo -e "     • 数据库初始化"
    echo -e "     • 自动启动\n"
    
    echo -e "  ${BLUE}2${NC}) ${BOLD}直接启动${NC} - 快速启动机器人"
    echo -e "     • 适合已配置环境"
    echo -e "     • 后台运行"
    echo -e "     • 日志输出\n"
    
    if [ "$DOCKER_OK" = "✓" ]; then
        echo -e "  ${CYAN}3${NC}) ${BOLD}Docker 部署${NC} - 容器化部署（推荐生产环境）"
        echo -e "     • 环境隔离"
        echo -e "     • 易于管理"
        echo -e "     • 自动重启\n"
    fi
    
    echo -e "  ${YELLOW}4${NC}) ${BOLD}仅检查配置${NC} - 验证配置文件"
    echo -e "  ${RED}0${NC}) ${BOLD}退出${NC}\n"
    
    while true; do
        read -p "请选择 [1-4 或 0]: " choice
        case $choice in
            1)
                deploy_install
                break
                ;;
            2)
                deploy_start
                break
                ;;
            3)
                if [ "$DOCKER_OK" = "✓" ]; then
                    deploy_docker
                    break
                else
                    log_error "Docker 未安装，请选择其他方式"
                fi
                ;;
            4)
                check_config_only
                break
                ;;
            0)
                log_info "退出向导"
                exit 0
                ;;
            *)
                log_error "无效选择，请重新输入"
                ;;
        esac
    done
}

# 方式 1: 一键安装
deploy_install() {
    echo ""
    log_info "执行一键安装..."
    echo ""
    
    if [ ! -f "install.sh" ]; then
        log_error "未找到 install.sh"
        exit 1
    fi
    
    chmod +x install.sh
    ./install.sh
}

# 方式 2: 直接启动
deploy_start() {
    echo ""
    log_info "执行直接启动..."
    echo ""
    
    # 检查配置
    if [ ! -f "config.ini" ]; then
        log_error "未找到 config.ini"
        log_info "请先运行一键安装: ./quickstart.sh 选择选项 1"
        exit 1
    fi
    
    if [ ! -f "start.sh" ]; then
        log_error "未找到 start.sh"
        exit 1
    fi
    
    chmod +x start.sh
    ./start.sh
}

# 方式 3: Docker 部署
deploy_docker() {
    echo ""
    log_info "执行 Docker 部署..."
    echo ""
    
    if [ ! -f "deploy.sh" ]; then
        log_error "未找到 deploy.sh"
        exit 1
    fi
    
    chmod +x deploy.sh
    ./deploy.sh
}

# 方式 4: 仅检查配置
check_config_only() {
    echo ""
    log_info "检查配置..."
    echo ""
    
    if [ ! -f "config.ini" ]; then
        log_error "未找到 config.ini"
        log_info "请先创建配置文件: cp config.ini.example config.ini"
        exit 1
    fi
    
    if [ -f "check_config.py" ]; then
        python3 check_config.py
    else
        log_warning "未找到 check_config.py"
        log_info "显示当前配置："
        grep -v "^#" config.ini | grep -v "^$"
    fi
    
    echo ""
    log_success "配置检查完成"
}

# 显示帮助信息
show_help() {
    echo ""
    echo -e "${BOLD}${CYAN}其他有用命令：${NC}\n"
    echo -e "  ${GREEN}管理${NC}"
    echo -e "    ./start.sh              启动机器人"
    echo -e "    ./restart.sh            重启机器人"
    echo -e "    ./restart.sh --stop     停止机器人"
    echo ""
    echo -e "  ${BLUE}维护${NC}"
    echo -e "    ./update.sh             更新代码"
    echo -e "    ./upgrade.sh            功能升级"
    echo -e "    tail -f logs/bot.log    查看日志"
    echo ""
    echo -e "  ${YELLOW}其他${NC}"
    echo -e "    cat README.md           查看文档"
    echo -e "    cat config.ini          查看配置"
    echo ""
}

# 主函数
main() {
    print_welcome
    detect_environment
    echo ""
    choose_deployment
    show_help
    
    echo -e "${GREEN}${BOLD}感谢使用 TelePost！${NC}\n"
}

main "$@"

