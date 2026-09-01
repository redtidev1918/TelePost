"""
日志配置模块
"""
import os
import time
import glob
import logging
from logging.handlers import RotatingFileHandler

class TimeoutMessageFilter(logging.Filter):
    """
    超时消息过滤器，将超时错误降级为警告
    """
    def filter(self, record):
        # 如果是ERROR级别且消息中包含超时关键词
        if record.levelno == logging.ERROR and any(kw in record.getMessage().lower() 
                                                  for kw in ["timeout", "超时", "timed out"]):
            # 将级别降为WARNING
            record.levelno = logging.WARNING
            record.levelname = "WARNING"
        return True

def cleanup_old_logs(log_dir, days_to_keep=7):
    """
    清理过期的日志文件
    
    Args:
        log_dir: 日志目录
        days_to_keep: 保留最近几天的日志
    """
    try:
        # 获取当前时间
        now = time.time()
        # 删除时间阈值（秒）
        threshold = now - (days_to_keep * 86400)  # 86400秒 = 1天
        
        # 获取所有日志文件
        log_files = glob.glob(os.path.join(log_dir, "*.log*"))
        
        logger = logging.getLogger(__name__)
        logger.info(f"开始清理日志文件，保留最近 {days_to_keep} 天的日志")
        deleted_count = 0
        
        for log_file in log_files:
            # 获取文件修改时间
            file_time = os.path.getmtime(log_file)
            # 如果文件修改时间早于阈值，则删除
            if file_time < threshold:
                try:
                    os.remove(log_file)
                    deleted_count += 1
                    logger.info(f"删除过期日志文件: {log_file}")
                except Exception as e:
                    logger.error(f"删除日志文件 {log_file} 失败: {e}")
        
        logger.info(f"日志清理完成，共删除 {deleted_count} 个过期文件")
    except Exception as e:
        logger.error(f"清理日志文件时出错: {e}")

# 配置基本日志
def setup_logging():
    """
    设置日志配置
    """
    # 创建logs目录（如果不存在）
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # 清理过期日志
    cleanup_old_logs(log_dir)
    
    # 创建日志格式
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # 降噪与安全：httpx 的 INFO 日志会把带 Bot Token 的完整请求 URL
    # 打进日志（每次轮询一条），构成凭据泄露面，统一只记 WARNING 以上
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # 配置根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # 清除已有的处理器
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # 添加控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # 添加文件处理器（每个文件最大5MB，保留3个备份文件）
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "bot.log"),
        maxBytes=5*1024*1024,  # 5MB
        backupCount=3,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    # 添加错误日志文件处理器，仅记录ERROR及以上级别
    error_file_handler = RotatingFileHandler(
        os.path.join(log_dir, "error.log"),
        maxBytes=3*1024*1024,  # 3MB
        backupCount=3,
        encoding='utf-8'
    )
    error_file_handler.setFormatter(formatter)
    error_file_handler.setLevel(logging.ERROR)
    root_logger.addHandler(error_file_handler)
    
    # 配置超时过滤器
    timeout_filter = TimeoutMessageFilter()
    for handler in root_logger.handlers:
        handler.addFilter(timeout_filter)
    
    logger = logging.getLogger(__name__)
    logger.info("日志系统已初始化，日志文件保存在 %s 目录", os.path.abspath(log_dir))
    logger.info("日志配置: 普通日志文件最大5MB，错误日志文件最大3MB，各保留3个备份文件，超过7天的日志将被自动清理")
    
    return logger