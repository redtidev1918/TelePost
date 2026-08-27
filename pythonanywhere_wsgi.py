"""
PythonAnywhere WSGI 配置文件
用于在 Webhook 模式下运行 TelePost

使用说明:
1. 将此文件复制到 /var/www/yourusername_pythonanywhere_com_wsgi.py
2. 或在 PythonAnywhere Web 配置中设置 WSGI 文件路径指向此文件
3. 确保已正确配置 config.ini 中的 WEBHOOK 设置
"""
import sys
import os
import asyncio
import logging
from pathlib import Path

# ⭐ 重要：替换为你的 PythonAnywhere 用户名
USERNAME = 'yourusername'  # 修改这里！

# 添加项目路径
project_home = f'/home/{USERNAME}/TelePost'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# 切换工作目录到项目根目录
os.chdir(project_home)

# 确保 logs 目录存在
logs_dir = os.path.join(project_home, 'logs')
os.makedirs(logs_dir, exist_ok=True)

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'{project_home}/logs/wsgi.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 初始化标志
_app_initialized = False
_application = None

def get_application():
    """获取或创建 Telegram Bot Application 实例"""
    global _app_initialized, _application
    
    if not _app_initialized:
        logger.info("初始化 Telegram Bot Application...")
        
        try:
            # 导入必要的模块
            from telegram.ext import Application
            from config.settings import TOKEN, RUN_MODE
            
            # 验证运行模式
            if RUN_MODE != 'WEBHOOK':
                logger.error(f"错误：RUN_MODE 必须设置为 WEBHOOK，当前值: {RUN_MODE}")
                raise ValueError("RUN_MODE must be WEBHOOK for PythonAnywhere deployment")
            
            # 创建 Application
            _application = Application.builder().token(TOKEN).build()
            
            # 导入并执行初始化函数
            from main import setup_application
            from database.db_manager import init_db
            from utils.database import initialize_database
            from utils.blacklist import init_blacklist
            
            # 同步运行异步初始化函数
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # 初始化数据库
            loop.run_until_complete(init_db())
            initialize_database()
            loop.run_until_complete(init_blacklist())
            
            # 设置处理器
            setup_application(_application)
            
            # 初始化应用
            loop.run_until_complete(_application.initialize())
            
            _app_initialized = True
            logger.info("✅ Telegram Bot Application 初始化完成")
            
        except Exception as e:
            logger.error(f"初始化失败: {e}", exc_info=True)
            raise
    
    return _application


async def handle_webhook_async(environ):
    """异步处理 Webhook 请求"""
    from telegram import Update
    from config.settings import WEBHOOK_SECRET_TOKEN
    import json
    
    # 验证 Secret Token（如果设置）
    if WEBHOOK_SECRET_TOKEN:
        token = environ.get('HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN', '')
        if token != WEBHOOK_SECRET_TOKEN:
            logger.warning("收到无效的 Secret Token")
            return ('403 Forbidden', [('Content-Type', 'text/plain')], [b'Forbidden'])
    
    # 读取请求体
    try:
        request_body_size = int(environ.get('CONTENT_LENGTH', 0))
    except ValueError:
        request_body_size = 0
    
    if request_body_size == 0:
        return ('400 Bad Request', [('Content-Type', 'text/plain')], [b'Bad Request'])
    
    request_body = environ['wsgi.input'].read(request_body_size)
    
    # 解析 Update
    try:
        update_dict = json.loads(request_body.decode('utf-8'))
        application = get_application()
        update = Update.de_json(update_dict, application.bot)
        
        # 处理更新
        await application.process_update(update)
        
        return ('200 OK', [('Content-Type', 'text/plain')], [b'OK'])
        
    except Exception as e:
        logger.error(f"处理 Webhook 更新时出错: {e}", exc_info=True)
        return ('500 Internal Server Error', [('Content-Type', 'text/plain')], [b'Internal Server Error'])


def application(environ, start_response):
    """
    WSGI 应用入口
    
    处理以下端点：
    - /webhook: Telegram Webhook
    - /health: 健康检查
    """
    path = environ.get('PATH_INFO', '/')
    
    try:
        # 健康检查端点
        if path == '/health':
            status = '200 OK'
            headers = [('Content-Type', 'text/plain')]
            start_response(status, headers)
            return [b'OK']
        
        # Webhook 端点
        elif path == '/webhook':
            from config.settings import WEBHOOK_PATH
            
            # 确认路径匹配
            if path != WEBHOOK_PATH:
                status = '404 Not Found'
                headers = [('Content-Type', 'text/plain')]
                start_response(status, headers)
                return [b'Not Found']
            
            # 处理 Webhook 请求
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                status, headers, response = loop.run_until_complete(
                    handle_webhook_async(environ)
                )
                start_response(status, headers)
                return response
            finally:
                loop.close()
        
        # 其他路径返回 404
        else:
            status = '404 Not Found'
            headers = [('Content-Type', 'text/plain')]
            start_response(status, headers)
            return [b'Not Found']
            
    except Exception as e:
        logger.error(f"WSGI 应用错误: {e}", exc_info=True)
        status = '500 Internal Server Error'
        headers = [('Content-Type', 'text/plain')]
        start_response(status, headers)
        return [b'Internal Server Error']


# 记录启动信息
logger.info("=" * 50)
logger.info("TelePost WSGI Application")
logger.info(f"Project path: {project_home}")
logger.info("=" * 50)
