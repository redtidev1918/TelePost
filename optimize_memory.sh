#!/bin/bash
# TeleSubmit v2 内存优化一键脚本
# 作用：应用推荐的内存优化配置并重建索引

set -e

echo "=================================================="
echo "  TeleSubmit v2 内存优化脚本"
echo "=================================================="
echo ""

# 检查 config.ini 是否存在
if [ ! -f "config.ini" ]; then
    echo "❌ 错误: 未找到 config.ini 文件"
    echo "   请先复制 config.ini.example 并配置后再运行此脚本"
    exit 1
fi

echo "📋 当前配置检查..."
echo ""

# 备份原配置
BACKUP_FILE="config.ini.backup.$(date +%Y%m%d_%H%M%S)"
cp config.ini "$BACKUP_FILE"
echo "✅ 已备份配置到: $BACKUP_FILE"
echo ""

# 检查是否已有优化配置
HAS_ANALYZER=$(grep -c "^ANALYZER" config.ini || true)
HAS_HIGHLIGHT=$(grep -c "^HIGHLIGHT" config.ini || true)
HAS_DB_SECTION=$(grep -c "^\[DB\]" config.ini || true)

echo "🔧 应用内存优化配置..."
echo ""

# 添加或更新 ANALYZER
if [ "$HAS_ANALYZER" -eq 0 ]; then
    # 在 [SEARCH] 段后添加
    sed -i.tmp '/^\[SEARCH\]/a\
ANALYZER = simple
' config.ini && rm -f config.ini.tmp
    echo "  ✓ 已添加 ANALYZER = simple"
else
    # 更新现有值
    sed -i.tmp 's/^ANALYZER.*/ANALYZER = simple/' config.ini && rm -f config.ini.tmp
    echo "  ✓ 已更新 ANALYZER = simple"
fi

# 添加或更新 HIGHLIGHT
if [ "$HAS_HIGHLIGHT" -eq 0 ]; then
    sed -i.tmp '/^\[SEARCH\]/a\
HIGHLIGHT = false
' config.ini && rm -f config.ini.tmp
    echo "  ✓ 已添加 HIGHLIGHT = false"
else
    sed -i.tmp 's/^HIGHLIGHT.*/HIGHLIGHT = false/' config.ini && rm -f config.ini.tmp
    echo "  ✓ 已更新 HIGHLIGHT = false"
fi

# 添加或更新 [DB] 段
if [ "$HAS_DB_SECTION" -eq 0 ]; then
    echo "" >> config.ini
    echo "[DB]" >> config.ini
    echo "# SQLite page cache 大小（KB）" >> config.ini
    echo "CACHE_SIZE_KB = 1024" >> config.ini
    echo "  ✓ 已添加 [DB] 配置段"
else
    # 检查是否有 CACHE_SIZE_KB
    HAS_CACHE=$(grep -c "^CACHE_SIZE_KB" config.ini || true)
    if [ "$HAS_CACHE" -eq 0 ]; then
        sed -i.tmp '/^\[DB\]/a\
CACHE_SIZE_KB = 1024
' config.ini && rm -f config.ini.tmp
        echo "  ✓ 已添加 CACHE_SIZE_KB = 1024"
    else
        sed -i.tmp 's/^CACHE_SIZE_KB.*/CACHE_SIZE_KB = 1024/' config.ini && rm -f config.ini.tmp
        echo "  ✓ 已更新 CACHE_SIZE_KB = 1024"
    fi
fi

echo ""
echo "🔄 重建搜索索引以应用新配置..."
echo ""

# 重建索引
python3 - <<'PY'
import os
import sys
import asyncio

sys.path.insert(0, '.')
os.environ['SEARCH_ANALYZER'] = 'simple'

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
            print(f"  ✓ 成功添加 {result['added']} 个文档")
            if result['failed'] > 0:
                print(f"  ⚠ 失败 {result['failed']} 个")
        else:
            print(f"  ✗ 重建失败")
            for err in result.get('errors', []):
                print(f"     {err}")
    
    asyncio.run(rebuild())
    
except Exception as e:
    print(f"  ✗ 索引重建出错: {e}")
    sys.exit(1)
PY

if [ $? -eq 0 ]; then
    echo ""
    echo "=================================================="
    echo "  ✅ 内存优化完成！"
    echo "=================================================="
    echo ""
    echo "📊 优化内容："
    echo "  • 分词器: jieba → simple (节省 ~140 MB)"
    echo "  • 高亮: 关闭 (减少临时内存)"
    echo "  • SQLite cache: 1024 KB (节省 ~19 MB)"
    echo ""
    echo "🚀 下一步："
    echo "  1. 重启 Bot 使配置生效"
    echo "     Docker: docker-compose restart"
    echo "     直接运行: ./restart.sh"
    echo ""
    echo "  2. 使用 /debug 命令查看内存占用"
    echo ""
    echo "📚 更多优化选项请查看:"
    echo "  - MEMORY_OPTIMIZATION_REPORT.md"
    echo "  - docs/PERFORMANCE.md"
    echo ""
else
    echo ""
    echo "❌ 索引重建失败，请查看上方错误信息"
    echo "   可以尝试手动重建: python3 utils/index_manager.py rebuild"
    exit 1
fi

