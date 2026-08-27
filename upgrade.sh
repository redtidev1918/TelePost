#!/bin/bash
# TelePost 功能升级脚本（包含数据迁移）

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

# 备份数据
backup_data() {
    log_info "创建完整备份..."
    
    BACKUP_DIR="backups/upgrade_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$BACKUP_DIR"
    
    # 备份配置
    if [ -f "config.ini" ]; then
        cp config.ini "$BACKUP_DIR/"
    fi
    
    # 备份数据库
    if [ -d "data" ]; then
        cp -r data "$BACKUP_DIR/"
    fi
    
    # 备份日志
    if [ -d "logs" ]; then
        cp -r logs "$BACKUP_DIR/"
    fi
    
    log_success "备份保存在: $BACKUP_DIR"
    echo "$BACKUP_DIR" > .last_backup
}

# 检查搜索索引
check_search_index() {
    log_info "检查搜索索引..."
    
    if [ ! -d "data/search_index" ]; then
        log_warning "未找到搜索索引目录"
        mkdir -p data/search_index
        log_info "已创建索引目录"
    fi
    
    # 检查索引是否需要重建
    python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from utils.search_engine import PostSearchEngine
    se = PostSearchEngine()
    print('搜索索引状态正常')
except Exception as e:
    print(f'索引需要重建: {e}')
    sys.exit(1)
" || {
        log_warning "搜索索引需要重建"
        return 1
    }
    
    log_success "搜索索引检查通过"
    return 0
}

# 运行搜索迁移
migrate_search() {
    log_info "运行搜索功能迁移..."
    
    if [ ! -f "migrate_to_search.py" ]; then
        log_warning "未找到迁移脚本 migrate_to_search.py"
        return 0
    fi
    
    python3 migrate_to_search.py || {
        log_error "搜索迁移失败"
        return 1
    }
    
    log_success "搜索功能迁移完成"
}

# 检查数据库结构
check_database() {
    log_info "检查数据库结构..."
    
    python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from database.db_manager import check_db_schema
    if check_db_schema():
        print('数据库结构正常')
    else:
        print('数据库需要更新')
        sys.exit(1)
except ImportError:
    # 如果没有这个函数，跳过检查
    print('跳过数据库结构检查')
except Exception as e:
    print(f'数据库检查失败: {e}')
    sys.exit(1)
"
    
    log_success "数据库检查完成"
}

# 更新依赖
update_dependencies() {
    log_info "更新 Python 依赖..."
    
    if [ -f "requirements.txt" ]; then
        pip3 install -r requirements.txt --upgrade -q
        log_success "依赖已更新"
    fi
}

# 清理旧数据
cleanup_old_data() {
    log_info "清理过期数据..."
    
    python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from utils.database import cleanup_expired_sessions
    cleanup_expired_sessions()
    print('会话数据已清理')
except Exception as e:
    print(f'清理失败: {e}')
" || log_warning "数据清理遇到问题"
    
    # 清理旧日志（保留最近30天）
    if [ -d "logs" ]; then
        find logs -name "*.log.*" -mtime +30 -delete 2>/dev/null || true
        log_success "旧日志已清理"
    fi
}

# 优化搜索索引
optimize_index() {
    log_info "优化搜索索引..."
    
    python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from utils.search_engine import PostSearchEngine
    se = PostSearchEngine()
    se.optimize_index()
    print('索引优化完成')
except Exception as e:
    print(f'优化失败: {e}')
" || log_warning "索引优化遇到问题"
}

# 验证升级
verify_upgrade() {
    log_info "验证升级结果..."
    
    # 检查配置
    if [ -f "check_config.py" ]; then
        python3 check_config.py || {
            log_error "配置验证失败"
            return 1
        }
    fi
    
    # 检查核心模块
    python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from config.settings import TOKEN
    from utils.database import initialize_database
    from utils.search_engine import PostSearchEngine
    print('核心模块加载正常')
except Exception as e:
    print(f'模块加载失败: {e}')
    sys.exit(1)
" || {
        log_error "模块验证失败"
        return 1
    }
    
    log_success "升级验证通过"
}

# 重启机器人
restart_bot() {
    log_info "重启机器人..."
    
    if [ -f "restart.sh" ]; then
        ./restart.sh
    else
        log_warning "未找到 restart.sh，请手动重启"
    fi
}

# 显示升级摘要
show_summary() {
    echo ""
    echo -e "${GREEN}${BOLD}╔════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}${BOLD}║     升级成功完成！                    ║${NC}"
    echo -e "${GREEN}${BOLD}╚════════════════════════════════════════╝${NC}"
    echo ""
    
    if [ -f ".last_backup" ]; then
        BACKUP=$(cat .last_backup)
        echo -e "${CYAN}备份位置:${NC} $BACKUP"
    fi
    
    echo ""
    echo -e "${BOLD}新功能：${NC}"
    echo -e "  ✓ 全文搜索引擎（支持中文分词）"
    echo -e "  ✓ 文件名搜索"
    echo -e "  ✓ 热度统计和排行榜"
    echo -e "  ✓ 标签云可视化"
    echo -e "  ✓ 高级黑名单管理"
    echo ""
    
    echo -e "${BOLD}搜索命令：${NC}"
    echo -e "  /search <关键词>       搜索内容"
    echo -e "  /search <文件名>       搜索文件"
    echo -e "  /hot [时间范围]        热门排行"
    echo -e "  /tags [数量]           标签云"
    echo ""
    
    if [ -f "CHANGELOG.md" ]; then
        echo -e "${CYAN}详细更新日志：${NC}"
        head -n 20 CHANGELOG.md
        echo ""
        echo -e "完整日志: ${CYAN}cat CHANGELOG.md${NC}"
    fi
    
    echo ""
}

# 主函数
main() {
    echo ""
    echo -e "${CYAN}${BOLD}=== TelePost 功能升级 ===${NC}"
    echo ""
    
    log_info "开始升级流程..."
    echo ""
    
    # 备份
    backup_data
    echo ""
    
    # 更新依赖
    update_dependencies
    echo ""
    
    # 检查和迁移
    check_database
    echo ""
    
    if ! check_search_index; then
        migrate_search
        echo ""
    fi
    
    # 优化
    cleanup_old_data
    echo ""
    
    optimize_index
    echo ""
    
    # 验证
    verify_upgrade
    echo ""
    
    # 重启
    restart_bot
    
    # 显示摘要
    show_summary
}

main "$@"

