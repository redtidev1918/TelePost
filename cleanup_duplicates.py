#!/usr/bin/env python3
"""
清理根目录下的重复文件
将数据迁移到 data/ 目录

使用方法：
python3 cleanup_duplicates.py
"""
import os
import shutil
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def cleanup():
    """清理重复文件并迁移数据"""
    
    print("=" * 60)
    print("  TelePost 数据清理和迁移脚本")
    print("=" * 60)
    print()
    
    # 创建data目录
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    logger.info("✅ data/ 目录已就绪")
    
    # 迁移数据库文件
    db_files = ["submissions.db", "user_sessions.db"]
    for db_file in db_files:
        src = Path(db_file)
        dst = data_dir / db_file
        
        if src.exists():
            # 检查目标文件是否存在且非空
            if dst.exists() and dst.stat().st_size > 0:
                logger.info(f"⚠️  {db_file}: 目标文件已存在且非空")
                
                # 如果源文件也非空，询问用户
                if src.stat().st_size > 0:
                    logger.warning(f"   源文件大小: {src.stat().st_size} 字节")
                    logger.warning(f"   目标文件大小: {dst.stat().st_size} 字节")
                    choice = input(f"   是否用根目录的 {db_file} 覆盖 data/ 中的文件？(yes/NO): ").strip().lower()
                    if choice in ['yes', 'y']:
                        shutil.move(str(src), str(dst))
                        logger.info(f"✅ {db_file}: 已迁移到 data/")
                    else:
                        # 删除根目录的空文件或旧文件
                        src.unlink()
                        logger.info(f"🗑️  {db_file}: 已删除根目录的文件，保留 data/ 中的文件")
                else:
                    # 源文件为空，直接删除
                    src.unlink()
                    logger.info(f"🗑️  {db_file}: 根目录文件为空，已删除")
            else:
                # 目标文件不存在或为空，直接迁移
                src_size = src.stat().st_size
                if src_size > 0:
                    shutil.move(str(src), str(dst))
                    logger.info(f"✅ {db_file}: 已迁移到 data/ ({src_size} 字节)")
                else:
                    src.unlink()
                    logger.info(f"🗑️  {db_file}: 文件为空，已删除")
        else:
            logger.info(f"ℹ️  {db_file}: 不存在于根目录")
    
    print()
    
    # 处理搜索索引目录
    src_index = Path("search_index")
    dst_index = data_dir / "search_index"
    
    if src_index.exists() and src_index.is_dir():
        # 统计文件数
        src_files = list(src_index.glob("*"))
        dst_files = list(dst_index.glob("*")) if dst_index.exists() else []
        
        logger.info(f"⚠️  search_index/: 发现根目录搜索索引")
        logger.info(f"   根目录文件数: {len(src_files)}")
        logger.info(f"   data/ 文件数: {len(dst_files)}")
        
        if len(dst_files) > 0:
            print()
            logger.warning("   两个位置都有搜索索引！")
            logger.warning("   建议使用 data/search_index（Docker持久化位置）")
            choice = input("   是否删除根目录的 search_index/？(yes/NO): ").strip().lower()
            if choice in ['yes', 'y']:
                shutil.rmtree(src_index)
                logger.info("🗑️  search_index/: 已删除根目录索引")
                logger.info("💡 提示: 如需重建索引，请运行: python migrate_to_search.py")
            else:
                logger.info("⏭️  search_index/: 已跳过")
        else:
            # data/ 中没有索引，迁移根目录的索引
            dst_index.mkdir(parents=True, exist_ok=True)
            for item in src_files:
                shutil.move(str(item), str(dst_index / item.name))
            src_index.rmdir()
            logger.info(f"✅ search_index/: 已迁移到 data/ ({len(src_files)} 个文件)")
    else:
        logger.info("ℹ️  search_index/: 不存在于根目录")
    
    print()
    print("=" * 60)
    print("  清理完成！")
    print("=" * 60)
    print()
    print("📁 当前数据结构:")
    print("   data/")
    print("   ├── submissions.db")
    print("   ├── user_sessions.db")
    print("   └── search_index/")
    print()
    print("💡 下一步:")
    print("   1. 检查配置: python check_config.py")
    print("   2. 重建索引: python migrate_to_search.py")
    print("   3. 启动机器人: python main.py")
    print()

if __name__ == "__main__":
    try:
        cleanup()
    except KeyboardInterrupt:
        print("\n\n❌ 操作已取消")
    except Exception as e:
        logger.error(f"\n❌ 错误: {e}", exc_info=True)

