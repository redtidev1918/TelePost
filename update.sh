#!/bin/bash
# TelePost 更新脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
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

# 检查 Git
check_git() {
    if ! command -v git &> /dev/null; then
        log_error "未安装 Git"
        exit 1
    fi
    
    if [ ! -d ".git" ]; then
        log_error "当前目录不是 Git 仓库"
        log_info "如果是手动下载的代码，请使用 git clone 重新获取"
        exit 1
    fi
}

# 备份配置和数据
backup_data() {
    log_info "备份配置和数据..."
    
    BACKUP_DIR="backups/update_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$BACKUP_DIR"
    
    # 备份配置文件
    if [ -f "config.ini" ]; then
        cp config.ini "$BACKUP_DIR/"
        log_success "已备份配置文件"
    fi
    
    # 备份数据库
    if [ -d "data" ]; then
        cp -r data "$BACKUP_DIR/"
        log_success "已备份数据目录"
    fi
    
    log_success "备份保存在: $BACKUP_DIR"
}

# 检查本地修改
check_local_changes() {
    log_info "检查本地修改..."
    
    if [ -n "$(git status --porcelain)" ]; then
        log_warning "检测到本地修改："
        git status --short
        echo ""
        read -p "是否继续更新？本地修改将被保留 (y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "更新已取消"
            exit 0
        fi
    fi
}

# 拉取最新代码
pull_updates() {
    log_info "拉取最新代码..."
    
    # 保存当前分支
    CURRENT_BRANCH=$(git branch --show-current)
    
    # 拉取更新
    git fetch origin
    
    # 检查是否有更新
    LOCAL=$(git rev-parse @)
    REMOTE=$(git rev-parse @{u} 2>/dev/null || echo "")
    
    if [ -z "$REMOTE" ]; then
        log_warning "无法获取远程更新信息"
        return
    fi
    
    if [ "$LOCAL" = "$REMOTE" ]; then
        log_success "已是最新版本"
        return
    fi
    
    # 设置默认 pull 策略（避免“需要指定如何调和偏离的分支”）
    git config pull.rebase false >/dev/null 2>&1 || true
    # 执行更新（显式使用 --no-rebase，保持合并策略）
    git pull --no-rebase origin "$CURRENT_BRANCH" || {
        log_error "代码拉取失败"
        log_info "可能存在冲突，请手动解决"
        exit 1
    }
    
    log_success "代码更新完成"
}

# 更新依赖
update_dependencies() {
    log_info "检查依赖更新..."
    
    if [ -f "requirements.txt" ]; then
        pip3 install -r requirements.txt --upgrade -q
        log_success "依赖已更新"
    else
        log_warning "未找到 requirements.txt"
    fi
}

# 运行数据库迁移
run_migrations() {
    log_info "检查数据库更新..."
    
    # 如果有迁移脚本，在这里运行
    if [ -f "migrate_to_search.py" ]; then
        python3 migrate_to_search.py || {
            log_warning "数据库迁移遇到问题（可能已完成）"
        }
    fi
    
    log_success "数据库检查完成"
}

# 重启机器人
restart_bot() {
    log_info "重启机器人..."
    
    if [ -f "restart.sh" ]; then
        ./restart.sh
    else
        log_warning "未找到 restart.sh，尝试手动重启..."
        
        # 停止
        if pgrep -f "python.*main.py" > /dev/null; then
            pkill -f "python.*main.py"
            sleep 2
        fi
        
        # 启动
        nohup python3 main.py > logs/bot.log 2>&1 &
        sleep 2
        
        if pgrep -f "python.*main.py" > /dev/null; then
            log_success "机器人已重启"
        else
            log_error "重启失败，请查看日志"
            exit 1
        fi
    fi
}

# 显示更新日志
show_changelog() {
    log_info "最近更新："
    echo ""
    
    if [ -f "CHANGELOG.md" ]; then
        head -n 30 CHANGELOG.md
        echo ""
        log_info "完整更新日志: cat CHANGELOG.md"
    else
        # 显示最近的 Git 提交
        git log --oneline -n 5
    fi
}

# 主函数
main() {
    echo ""
    log_info "=== TelePost 更新 ==="
    echo ""
    
    check_git
    backup_data
    echo ""
    check_local_changes
    echo ""
    pull_updates
    echo ""
    update_dependencies
    echo ""
    run_migrations
    echo ""
    restart_bot
    
    echo ""
    log_success "更新完成！"
    echo ""
    
    show_changelog
}

main "$@"

