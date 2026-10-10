"""
用户会话数据库管理模块
"""
import json
import time
import sqlite3
import logging
from contextlib import closing, contextmanager
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# 会话数据库路径
SESSION_DB_PATH = "user_sessions.db"

@contextmanager
def _session_db():
    # Match the application database durability mode and always release handles.
    with closing(sqlite3.connect(SESSION_DB_PATH)) as conn:
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn

def initialize_database():
    """初始化用户会话数据库"""
    try:
        with _session_db() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            c = conn.cursor()
            c.execute('''
                CREATE TABLE IF NOT EXISTS user_sessions (
                    user_id INTEGER PRIMARY KEY,
                    state TEXT,
                    data TEXT,
                    last_activity REAL
                )
            ''')
            conn.commit()
        logger.info("用户会话数据库初始化完成")
    except Exception as e:
        logger.error(f"初始化会话数据库失败: {e}")

def get_user_state(user_id: int) -> Optional[Dict]:
    """
    获取用户状态
    
    Args:
        user_id: 用户ID
        
    Returns:
        包含用户状态信息的字典，如果用户不存在则返回None
    """
    try:
        with _session_db() as conn:
            c = conn.cursor()
            c.execute("SELECT state, data, last_activity FROM user_sessions WHERE user_id=?", (user_id,))
            row = c.fetchone()
        
        if row:
            return {
                "state": row[0],
                "data": json.loads(row[1]) if row[1] else {},
                "last_activity": row[2]
            }
        return None
    except Exception as e:
        logger.error(f"获取用户状态失败: {e}")
        return None

def save_user_state(user_id: int, state: str, data: Dict = None):
    """
    保存用户状态
    
    Args:
        user_id: 用户ID
        state: 状态字符串
        data: 状态数据字典
    """
    try:
        with _session_db() as conn:
            c = conn.cursor()
            c.execute('''
                INSERT OR REPLACE INTO user_sessions (user_id, state, data, last_activity)
                VALUES (?, ?, ?, ?)
            ''', (user_id, state, json.dumps(data or {}), time.time()))
            conn.commit()
    except Exception as e:
        logger.error(f"保存用户状态失败: {e}")

def update_user_activity(user_id: int):
    """
    更新用户最后活动时间
    
    Args:
        user_id: 用户ID
    """
    try:
        with _session_db() as conn:
            c = conn.cursor()
            c.execute("UPDATE user_sessions SET last_activity=? WHERE user_id=?", (time.time(), user_id))
            conn.commit()
    except Exception as e:
        logger.error(f"更新用户活动时间失败: {e}")

def delete_user_state(user_id: int):
    """
    删除用户状态
    
    Args:
        user_id: 用户ID
    """
    try:
        with _session_db() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
            conn.commit()
        logger.debug(f"已删除用户 {user_id} 的会话状态")
    except Exception as e:
        logger.error(f"删除用户状态失败: {e}")

def is_blacklisted(user_id: int) -> bool:
    """
    检查用户是否在黑名单中（调用黑名单模块）
    
    Args:
        user_id: 用户ID
        
    Returns:
        用户是否在黑名单中
    """
    try:
        from utils.blacklist import is_blacklisted as check_blacklist
        return check_blacklist(user_id)
    except Exception as e:
        logger.error(f"检查黑名单状态失败: {e}")
        return False

def cleanup_expired_sessions(timeout: int = 900):
    """
    清理过期的会话
    
    Args:
        timeout: 超时时间（秒），默认15分钟
    """
    try:
        with _session_db() as conn:
            c = conn.cursor()
            cutoff_time = time.time() - timeout
            c.execute("DELETE FROM user_sessions WHERE last_activity < ?", (cutoff_time,))
            deleted_count = c.rowcount
            conn.commit()
        
        if deleted_count > 0:
            logger.info(f"清理了 {deleted_count} 个过期会话")
    except Exception as e:
        logger.error(f"清理过期会话失败: {e}")

def get_all_user_states() -> list:
    """
    获取所有用户会话状态
    
    Returns:
        所有用户状态的列表
    """
    try:
        with _session_db() as conn:
            c = conn.cursor()
            c.execute("SELECT user_id, state, data, last_activity FROM user_sessions")
            rows = c.fetchall()
        
        states = []
        for row in rows:
            states.append({
                "user_id": row[0],
                "state": row[1],
                "data": json.loads(row[2]) if row[2] else {},
                "last_activity": row[3]
            })
        return states
    except Exception as e:
        logger.error(f"获取所有用户状态失败: {e}")
        return []

def get_all_active_users(timeout: int = 900) -> list:
    """
    获取所有活跃用户
    
    Args:
        timeout: 超时时间（秒）
        
    Returns:
        活跃用户ID列表
    """
    try:
        with _session_db() as conn:
            c = conn.cursor()
            cutoff_time = time.time() - timeout
            c.execute("SELECT user_id FROM user_sessions WHERE last_activity >= ?", (cutoff_time,))
            users = [row[0] for row in c.fetchall()]
        return users
    except Exception as e:
        logger.error(f"获取活跃用户失败: {e}")
        return []
