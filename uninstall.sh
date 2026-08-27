#!/bin/bash
# TelePost 卸载脚本

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

# 警告确认
confirm_uninstall() {
    echo ""
    echo -e "${RED}${BOLD}⚠️  警告：即将卸载 TelePost${NC}"
    echo ""
    echo -e "${YELLOW}以下数据将被处理：${NC}"
    echo -e "  • 机器人进程将被停止"
    echo -e "  • 数据库将被备份（如果选择）"
    echo -e "  • 配置文件将被备份（如果选择）"
    echo -e "  • 可选择完全删除或仅停止服务"
    echo ""
    
    read -p "确认要继续吗？(yes/no) " -r
    echo
    if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        log_info "已取消卸载"
        exit 0
    fi
}

# 选择卸载模式
choose_mode() {
    echo ""
    echo -e "${BOLD}请选择卸载模式：${NC}\n"
    echo -e "  ${GREEN}1${NC}) ${BOLD}仅停止服务${NC} - 保留所有数据和配置"
    echo -e "  ${YELLOW}2${NC}) ${BOLD}备份并卸载${NC} - 备份数据后删除所有文件"
    echo -e "  ${RED}3${NC}) ${BOLD}完全删除${NC} - 删除所有内容（不可恢复）"
    echo -e "  ${BLUE}0${NC}) ${BOLD}取消${NC}\n"
    
    while true; do
        read -p "请选择 [1-3 或 0]: " choice
        case $choice in
            1)
                MODE="stop"
                break
                ;;
            2)
                MODE="backup"
                break
                ;;
            3)
                MODE="delete"
                echo ""
                log_warning "完全删除模式将永久删除所有数据！"
                read -p "再次确认完全删除？(yes/no) " -r
                echo
                if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
                    log_info "已取消完全删除"
                    continue
                fi
                break
                ;;
            0)
                log_info "已取消卸载"
                exit 0
                ;;
            *)
                log_error "无效选择"
                ;;
        esac
    done
}

# 停止机器人
stop_bot() {
    log_info "停止机器人进程..."
    
    if pgrep -f "python.*main.py" > /dev/null; then
        pkill -f "python.*main.py" || true
        sleep 2
        
        # 强制停止
        if pgrep -f "python.*main.py" > /dev/null; then
            pkill -9 -f "python.*main.py" || true
        fi
        
        log_success "机器人已停止"
    else
        log_info "未发现运行中的进程"
    fi
    
    # 停止 Docker 容器（如果有）
    if command -v docker-compose &> /dev/null; then
        if docker-compose ps -q 2>/dev/null | grep -q .; then
            log_info "停止 Docker 容器..."
            docker-compose down
            log_success "Docker 容器已停止"
        fi
    fi
}

# 备份数据
backup_data() {
    log_info "创建最终备份..."
    
    BACKUP_DIR="backups/uninstall_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$BACKUP_DIR"
    
    # 备份配置
    if [ -f "config.ini" ]; then
        cp config.ini "$BACKUP_DIR/"
        log_success "配置已备份"
    fi
    
    # 备份数据库
    if [ -d "data" ]; then
        cp -r data "$BACKUP_DIR/"
        log_success "数据库已备份"
    fi
    
    # 备份日志
    if [ -d "logs" ]; then
        cp -r logs "$BACKUP_DIR/"
        log_success "日志已备份"
    fi
    
    # 创建备份说明
    cat > "$BACKUP_DIR/README.txt" << EOF
TelePost 卸载备份
备份时间: $(date)
备份内容:
- config.ini: 机器人配置文件
- data/: 数据库和搜索索引
- logs/: 运行日志

恢复方法:
1. 重新安装 TelePost
2. 将备份的文件复制回相应位置
3. 运行 ./start.sh 启动机器人
EOF
    
    echo ""
    log_success "备份保存在: $BACKUP_DIR"
    echo ""
}

# 删除文件
delete_files() {
    log_info "删除程序文件..."
    
    # 删除数据
    if [ -d "data" ]; then
        rm -rf data
        log_success "数据目录已删除"
    fi
    
    # 删除日志
    if [ -d "logs" ]; then
        rm -rf logs
        log_success "日志目录已删除"
    fi
    
    # 删除配置
    if [ -f "config.ini" ]; then
        rm -f config.ini
        log_success "配置文件已删除"
    fi
    
    # 删除 Python 缓存
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    log_success "Python 缓存已清理"
}

# 删除依赖（可选）
remove_dependencies() {
    echo ""
    read -p "是否同时卸载 Python 依赖？(y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        log_info "卸载 Python 依赖..."
        if [ -f "requirements.txt" ]; then
            pip3 uninstall -r requirements.txt -y -q || true
            log_success "依赖已卸载"
        fi
    fi
}

# 显示摘要
show_summary() {
    echo ""
    echo -e "${CYAN}${BOLD}==================================${NC}"
    
    case $MODE in
        stop)
            echo -e "${GREEN}${BOLD}服务已停止${NC}"
            echo ""
            echo -e "所有数据已保留："
            echo -e "  • 配置文件: config.ini"
            echo -e "  • 数据目录: data/"
            echo -e "  • 日志目录: logs/"
            echo ""
            echo -e "重新启动: ${CYAN}./start.sh${NC}"
            ;;
        backup)
            echo -e "${GREEN}${BOLD}卸载完成（已备份）${NC}"
            echo ""
            echo -e "备份位置: ${CYAN}$BACKUP_DIR${NC}"
            echo ""
            echo -e "如需恢复，请参考备份目录中的 README.txt"
            ;;
        delete)
            echo -e "${RED}${BOLD}完全卸载完成${NC}"
            echo ""
            echo -e "所有数据已删除"
            echo ""
            echo -e "如需重新安装: ${CYAN}./install.sh${NC}"
            ;;
    esac
    
    echo -e "${CYAN}${BOLD}==================================${NC}"
    echo ""
}

# 主函数
main() {
    echo ""
    echo -e "${CYAN}${BOLD}=== TelePost 卸载 ===${NC}"
    
    confirm_uninstall
    choose_mode
    
    echo ""
    log_info "开始卸载流程..."
    echo ""
    
    # 停止服务
    stop_bot
    echo ""
    
    case $MODE in
        stop)
            # 仅停止，不删除
            ;;
        backup)
            # 备份并删除
            backup_data
            delete_files
            remove_dependencies
            ;;
        delete)
            # 完全删除
            delete_files
            remove_dependencies
            ;;
    esac
    
    show_summary
    
    echo -e "${GREEN}${BOLD}感谢使用 TelePost！${NC}\n"
}

main "$@"

