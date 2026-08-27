#!/bin/bash
# TelePost 配置模式切换脚本
# 作用：在不同内存/性能模式之间快速切换

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config.ini"

show_help() {
    cat << EOF
================================================
  TelePost 配置模式切换工具
================================================

使用方法:
  $0 <模式> [选项]

可用模式:
  极致省内存 | minimal    - 最低内存占用 (~80-120 MB)
  均衡       | balanced   - 平衡性能与内存 (~150-200 MB)  
  性能优先   | performance- 最佳搜索体验 (~200-350 MB)
  禁用搜索   | nosearch   - 禁用搜索功能 (~60-100 MB)

选项:
  --no-rebuild           不自动重建索引（需要手动重建）
  --backup-suffix <名称> 自定义备份文件后缀

示例:
  $0 minimal              # 切换到极致省内存模式
  $0 balanced             # 切换到均衡模式
  $0 performance          # 切换到性能优先模式
  $0 nosearch             # 禁用搜索功能
  $0 balanced --no-rebuild # 切换但不重建索引

当前配置:
  $(grep "^ANALYZER" $CONFIG_FILE 2>/dev/null || echo "  ANALYZER = (未设置)")
  $(grep "^HIGHLIGHT" $CONFIG_FILE 2>/dev/null || echo "  HIGHLIGHT = (未设置)")
  $(grep "^CACHE_SIZE_KB" $CONFIG_FILE 2>/dev/null || echo "  CACHE_SIZE_KB = (未设置)")

================================================
EOF
}

# 检查参数
if [ $# -eq 0 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    show_help
    exit 0
fi

MODE="$1"
REBUILD=true

# 解析选项
shift
while [ $# -gt 0 ]; do
    case "$1" in
        --no-rebuild)
            REBUILD=false
            shift
            ;;
        --backup-suffix)
            BACKUP_SUFFIX="$2"
            shift 2
            ;;
        *)
            echo "❌ 未知选项: $1"
            show_help
            exit 1
            ;;
    esac
done

# 检查配置文件
if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ 错误: 未找到 config.ini"
    echo "   请先复制 config.ini.example 并配置"
    exit 1
fi

# 备份配置
BACKUP_SUFFIX="${BACKUP_SUFFIX:-$(date +%Y%m%d_%H%M%S)}"
BACKUP_FILE="${CONFIG_FILE}.backup.${BACKUP_SUFFIX}"
cp "$CONFIG_FILE" "$BACKUP_FILE"
echo "✅ 已备份配置到: $BACKUP_FILE"
echo ""

# 设置配置的函数
set_config() {
    local section=$1
    local key=$2
    local value=$3
    
    # 检查配置是否存在
    if grep -q "^${key}" "$CONFIG_FILE"; then
        # 更新现有配置
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|^${key}.*|${key} = ${value}|" "$CONFIG_FILE"
        else
            sed -i "s|^${key}.*|${key} = ${value}|" "$CONFIG_FILE"
        fi
    else
        # 添加新配置到对应段落
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "/^\[${section}\]/a\\
${key} = ${value}
" "$CONFIG_FILE"
        else
            sed -i "/^\[${section}\]/a ${key} = ${value}" "$CONFIG_FILE"
        fi
    fi
}

# 确保 [DB] 段存在
ensure_db_section() {
    if ! grep -q "^\[DB\]" "$CONFIG_FILE"; then
        echo "" >> "$CONFIG_FILE"
        echo "[DB]" >> "$CONFIG_FILE"
        echo "# SQLite page cache 大小（KB）" >> "$CONFIG_FILE"
    fi
}

# 根据模式设置配置
case "$MODE" in
    极致省内存|minimal)
        echo "🎯 切换到: 极致省内存模式"
        echo "   - 内存占用: ~80-120 MB"
        echo "   - 搜索质量: 基础 (simple 分词)"
        echo "   - 适用场景: 内存受限环境"
        echo ""
        
        set_config "SEARCH" "ENABLED" "true"
        set_config "SEARCH" "ANALYZER" "simple"
        set_config "SEARCH" "HIGHLIGHT" "false"
        ensure_db_section
        set_config "DB" "CACHE_SIZE_KB" "1024"
        
        ANALYZER="simple"
        MODE_NAME="极致省内存"
        ;;
        
    均衡|balanced)
        echo "🎯 切换到: 均衡模式"
        echo "   - 内存占用: ~150-200 MB"
        echo "   - 搜索质量: 优秀 (jieba 中文分词)"
        echo "   - 适用场景: 一般使用，推荐配置"
        echo ""
        
        set_config "SEARCH" "ENABLED" "true"
        set_config "SEARCH" "ANALYZER" "jieba"
        set_config "SEARCH" "HIGHLIGHT" "false"
        ensure_db_section
        set_config "DB" "CACHE_SIZE_KB" "2048"
        
        ANALYZER="jieba"
        MODE_NAME="均衡"
        ;;
        
    性能优先|performance)
        echo "🎯 切换到: 性能优先模式"
        echo "   - 内存占用: ~200-350 MB"
        echo "   - 搜索质量: 最佳 (jieba + 高亮)"
        echo "   - 适用场景: 内存充足，追求最佳体验"
        echo ""
        
        set_config "SEARCH" "ENABLED" "true"
        set_config "SEARCH" "ANALYZER" "jieba"
        set_config "SEARCH" "HIGHLIGHT" "true"
        ensure_db_section
        set_config "DB" "CACHE_SIZE_KB" "4096"
        
        ANALYZER="jieba"
        MODE_NAME="性能优先"
        ;;
        
    禁用搜索|nosearch)
        echo "🎯 切换到: 禁用搜索模式"
        echo "   - 内存占用: ~60-100 MB"
        echo "   - 搜索功能: 完全禁用"
        echo "   - 适用场景: 极限省内存，不需要搜索"
        echo ""
        
        set_config "SEARCH" "ENABLED" "false"
        ensure_db_section
        set_config "DB" "CACHE_SIZE_KB" "512"
        
        ANALYZER="none"
        MODE_NAME="禁用搜索"
        REBUILD=false  # 禁用搜索时不需要重建索引
        ;;
        
    *)
        echo "❌ 未知模式: $MODE"
        echo ""
        show_help
        exit 1
        ;;
esac

echo "✅ 配置已更新"
echo ""

# 重建索引（如果需要）
if [ "$REBUILD" = true ] && [ "$ANALYZER" != "none" ]; then
    echo "🔄 重建搜索索引以应用新配置..."
    echo ""
    
    python3 - <<PY
import os
import sys
import asyncio

sys.path.insert(0, '$SCRIPT_DIR')
os.environ['SEARCH_ANALYZER'] = '$ANALYZER'

try:
    from utils.search_engine import init_search_engine
    from utils.index_manager import IndexManager
    
    print("  → 初始化搜索引擎...")
    init_search_engine('data/search_index', from_scratch=True)
    
    print("  → 重建索引...")
    async def rebuild():
        manager = IndexManager()
        result = await manager.rebuild_index(clear_first=False)
        if result['success']:
            print(f"  ✓ 成功: {result['added']} 个文档")
            if result['failed'] > 0:
                print(f"  ⚠ 失败: {result['failed']} 个")
        else:
            print(f"  ✗ 重建失败")
            sys.exit(1)
    
    asyncio.run(rebuild())
    
except Exception as e:
    print(f"  ✗ 索引重建出错: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
PY
    
    if [ $? -eq 0 ]; then
        echo ""
        echo "  ✅ 索引重建完成"
    else
        echo ""
        echo "  ❌ 索引重建失败"
        echo "     可以稍后手动运行: python3 utils/index_manager.py rebuild"
    fi
fi

echo ""
echo "================================================"
echo "  ✅ 已切换到 ${MODE_NAME} 模式"
echo "================================================"
echo ""
echo "📋 新配置:"
grep "^ENABLED" $CONFIG_FILE | head -1
grep "^ANALYZER" $CONFIG_FILE | head -1 || echo "  ANALYZER = (已禁用搜索)"
grep "^HIGHLIGHT" $CONFIG_FILE | head -1 || echo "  HIGHLIGHT = (未设置)"
grep "^CACHE_SIZE_KB" $CONFIG_FILE | head -1
echo ""
echo "🚀 下一步:"
echo "  1. 重启 Bot 使配置生效"
echo "     Docker: docker-compose restart"
echo "     直接运行: ./restart.sh"
echo ""
echo "  2. 使用 /debug 命令验证新配置"
echo ""
echo "💾 恢复备份:"
echo "  cp $BACKUP_FILE $CONFIG_FILE"
echo ""

