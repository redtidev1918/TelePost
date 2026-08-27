"""
搜索引擎模块 - 基于 Whoosh 的全文搜索
改编自 tg_searcher 项目，用于 TelePost
"""
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List
import shutil

from whoosh import index
from whoosh.fields import Schema, TEXT, ID, DATETIME, NUMERIC
from whoosh.qparser import QueryParser, MultifieldParser
from whoosh.writing import IndexWriter
from whoosh.query import Term, Or, DateRange, NumericRange, And, Wildcard, FuzzyTerm
import whoosh.highlight as highlight
from config.settings import SEARCH_ANALYZER, SEARCH_HIGHLIGHT
import re

logger = logging.getLogger(__name__)


class PostDocument:
    """搜索文档数据结构"""

    @staticmethod
    def get_schema() -> Schema:
        """按配置懒加载构建 Schema，避免在导入时加载大词典。"""
        analyzer = None
        if SEARCH_ANALYZER == 'jieba':
            try:
                from jieba.analyse.analyzer import ChineseAnalyzer  # 延迟导入
                analyzer = ChineseAnalyzer()
            except Exception:
                # 回退到简单分词器
                from whoosh.analysis import SimpleAnalyzer
                analyzer = SimpleAnalyzer()
        else:
            from whoosh.analysis import SimpleAnalyzer
            analyzer = SimpleAnalyzer()

        return Schema(
            message_id=ID(stored=True, unique=True),
            post_id=NUMERIC(stored=True),  # 数据库ID，用于删除操作
            title=TEXT(stored=True, analyzer=analyzer),
            description=TEXT(stored=False, analyzer=analyzer),  # 仅用于检索，不存储
            tags=TEXT(stored=True, analyzer=analyzer),
            filename=TEXT(stored=True, analyzer=analyzer),
            link=TEXT(stored=False),      # 不展示，不存储
            user_id=ID(stored=True),
            username=TEXT(stored=False),  # 不展示，不存储
            publish_time=DATETIME(stored=True, sortable=True),
            views=NUMERIC(stored=True),
            heat_score=NUMERIC(stored=True, sortable=True),
        )
    
    def __init__(self, message_id: int, title: str = "", description: str = "", 
                 tags: str = "", filename: str = "", link: str = "", user_id: int = 0, 
                 username: str = "", publish_time: datetime = None, 
                 views: int = 0, heat_score: float = 0, post_id: int = None):
        self.message_id = str(message_id)
        self.post_id = post_id  # 数据库ID
        self.title = title or ""
        self.description = description or ""
        self.tags = tags or ""
        self.filename = filename or ""
        self.link = link or ""
        self.user_id = user_id
        self.username = username or ""
        self.publish_time = publish_time or datetime.now()
        self.views = views
        self.heat_score = heat_score
    
    def as_dict(self):
        """转换为字典用于索引"""
        return {
            'message_id': self.message_id,
            'post_id': self.post_id if self.post_id is not None else 0,
            'title': self.title,
            'description': self.description,
            'tags': self.tags,
            'filename': self.filename,
            'link': self.link,
            'user_id': str(self.user_id),  # 转换为字符串以支持大整数
            'username': self.username,
            'publish_time': self.publish_time,
            'views': self.views,
            'heat_score': self.heat_score,
        }


class SearchHit:
    """搜索结果项"""
    
    def __init__(self, message_id: int, title: str, description: str, 
                 tags: str, filename: str, link: str, user_id: int, username: str,
                 publish_time: datetime, views: int, heat_score: float,
                 highlighted_title: str = "", highlighted_desc: str = "",
                 matched_fields: List[str] = None, post_id: int = None):
        self.message_id = message_id
        self.post_id = post_id  # 数据库ID，用于删除操作
        self.title = title
        self.description = description
        self.tags = tags
        self.filename = filename
        self.link = link
        self.user_id = user_id
        self.username = username
        self.publish_time = publish_time
        self.views = views
        self.heat_score = heat_score
        self.highlighted_title = highlighted_title or title
        self.highlighted_desc = highlighted_desc or description
        self.matched_fields = matched_fields or []  # 记录匹配的字段


class SearchResult:
    """搜索结果集"""
    
    def __init__(self, hits: List[SearchHit], total_results: int, 
                 is_last_page: bool, page_num: int):
        self.hits = hits
        self.total_results = total_results
        self.is_last_page = is_last_page
        self.page_num = page_num


class PostSearchEngine:
    """帖子搜索引擎 - 基于 Whoosh"""
    
    def __init__(self, index_dir: str = "search_index", from_scratch: bool = False):
        """
        初始化搜索引擎
        
        Args:
            index_dir: 索引目录路径
            from_scratch: 是否从头创建索引（清除现有数据）
        """
        self.index_dir = Path(index_dir)
        self.index_name = 'posts'
        self.enable_highlight = SEARCH_HIGHLIGHT
        
        # 创建索引目录
        if not self.index_dir.exists():
            self.index_dir.mkdir(parents=True)
        
        # 从头创建索引
        if from_scratch and self.index_dir.exists():
            logger.warning(f"清除现有索引: {self.index_dir}")
            shutil.rmtree(self.index_dir)
            self.index_dir.mkdir(parents=True)
        
        # 打开或创建索引（带兼容性检查）
        try:
            index_exists = index.exists_in(str(self.index_dir), self.index_name)
        except Exception as check_err:
            # 索引检查失败（可能是因为分词器不兼容）
            logger.warning(f"索引检查失败: {check_err}")
            logger.info("将创建新索引")
            index_exists = False
        
        if index_exists:
            try:
                self.ix = index.open_dir(str(self.index_dir), self.index_name)
                logger.info(f"打开现有索引: {self.index_dir}")
                
                # 检查索引兼容性
                if not self._check_index_compatibility():
                    logger.warning(f"索引不兼容当前配置，将自动重建")
                    self._rebuild_incompatible_index()
            except Exception as e:
                logger.error(f"打开索引失败: {e}")
                logger.info(f"尝试重建索引...")
                self._rebuild_incompatible_index()
        else:
            self.ix = index.create_in(str(self.index_dir), PostDocument.get_schema(), self.index_name)
            logger.info(f"创建新索引: {self.index_dir}")
        
        # 创建查询解析器（支持多字段搜索）
        # 使用索引中的 schema 创建解析器
        self.query_parser = MultifieldParser(
            ['title', 'description', 'tags', 'filename'],
            schema=self.ix.schema
        )
        
        # 创建高亮器（可配置关闭）
        if self.enable_highlight:
            self.highlighter = highlight.Highlighter(
                fragmenter=highlight.ContextFragmenter(maxchars=200, surround=50),
                formatter=highlight.HtmlFormatter()
            )
        else:
            self.highlighter = None
    
    def _check_index_compatibility(self) -> bool:
        """
        检查索引是否与当前配置兼容
        
        Returns:
            bool: True 表示兼容，False 表示不兼容
        """
        try:
            # 尝试读取一个文档，检查是否能正常工作
            with self.ix.searcher() as searcher:
                # 检查 schema 是否匹配
                current_schema = PostDocument.get_schema()
                index_schema = self.ix.schema
                
                # 比较字段名称
                current_fields = set(current_schema.names())
                index_fields = set(index_schema.names())
                
                if current_fields != index_fields:
                    logger.warning(f"Schema 字段不匹配: 当前={current_fields}, 索引={index_fields}")
                    return False
                
                # 检查分词器类型（通过尝试搜索来验证）
                try:
                    searcher.search(Term("title", "test"), limit=1)
                except Exception as e:
                    logger.warning(f"索引搜索测试失败: {e}")
                    return False
                
            return True
        except Exception as e:
            logger.error(f"索引兼容性检查失败: {e}")
            return False
    
    def _rebuild_incompatible_index(self):
        """重建不兼容的索引"""
        backup_dir = None
        try:
            logger.info("开始重建索引...")
            
            # 关闭当前索引
            if hasattr(self, 'ix') and self.ix is not None:
                try:
                    self.ix.close()
                except:
                    pass
            
            # 备份旧索引
            backup_dir = self.index_dir.parent / f"{self.index_dir.name}.backup"
            if self.index_dir.exists():
                try:
                    if backup_dir.exists():
                        shutil.rmtree(backup_dir)
                    shutil.move(str(self.index_dir), str(backup_dir))
                    logger.info(f"旧索引已备份到: {backup_dir}")
                except Exception as backup_err:
                    logger.warning(f"备份索引失败: {backup_err}，将直接删除")
                    shutil.rmtree(self.index_dir)
                    backup_dir = None
            
            # 创建新索引目录
            self.index_dir.mkdir(parents=True, exist_ok=True)
            
            # 创建新索引
            self.ix = index.create_in(str(self.index_dir), PostDocument.get_schema(), self.index_name)
            logger.info(f"新索引创建成功: {self.index_dir}")
            
            # 标记需要重新索引
            self._needs_reindex = True
            
        except Exception as e:
            logger.error(f"重建索引失败: {e}", exc_info=True)
            # 如果重建失败，尝试恢复备份
            if backup_dir and backup_dir.exists() and not self.index_dir.exists():
                try:
                    shutil.move(str(backup_dir), str(self.index_dir))
                    logger.info("已恢复旧索引")
                except:
                    logger.error("恢复旧索引失败")
            # 不抛出异常，允许程序继续运行（搜索功能降级）
            logger.warning("索引重建失败，搜索功能可能不可用")
    
    def add_post(self, post: PostDocument, writer: Optional[IndexWriter] = None):
        """
        添加帖子到索引
        
        Args:
            post: 帖子文档
            writer: 可选的写入器（用于批量操作）
        """
        if writer is not None:
            writer.add_document(**post.as_dict())
        else:
            with self.ix.writer() as writer:
                writer.add_document(**post.as_dict())
        logger.debug(f"添加帖子到索引: {post.message_id}")
    
    def update_post(self, post: PostDocument):
        """
        更新帖子索引
        
        Args:
            post: 帖子文档
        """
        with self.ix.writer() as writer:
            writer.update_document(**post.as_dict())
        logger.debug(f"更新帖子索引: {post.message_id}")
    
    def delete_post(self, message_id: int):
        """
        从索引中删除帖子
        
        Args:
            message_id: 消息 ID
        """
        with self.ix.writer() as writer:
            writer.delete_by_term('message_id', str(message_id))
        logger.debug(f"从索引删除帖子: {message_id}")
    
    def search(self, query_str: str, page_num: int = 1, page_len: int = 10,
               time_filter: Optional[DateRange] = None,
               user_filter: Optional[int] = None,
               tag_filter: Optional[str] = None,
               sort_by: str = "publish_time") -> SearchResult:
        """
        搜索帖子
        
        Args:
            query_str: 搜索关键词
            page_num: 页码（从1开始）
            page_len: 每页结果数量
            time_filter: 时间过滤器
            user_filter: 用户ID过滤
            tag_filter: 标签过滤
            sort_by: 排序字段（publish_time 或 heat_score）
        
        Returns:
            SearchResult: 搜索结果
        """
        try:
            # 解析查询
            if query_str.strip():
                # 检测是否包含中文字符，且使用 SimpleAnalyzer
                # SimpleAnalyzer 将中文作为整体索引，需要特殊处理以支持部分匹配
                has_chinese = bool(re.search(r'[\u4e00-\u9fff]', query_str))
                use_simple_analyzer = SEARCH_ANALYZER == 'simple'
                
                if has_chinese and use_simple_analyzer:
                    # 对于中文查询，使用通配符查询以支持部分匹配
                    # 在多个字段中搜索包含查询字符串的内容
                    query_terms = []
                    search_fields = ['title', 'description', 'tags', 'filename']
                    for field in search_fields:
                        # 使用通配符匹配，支持部分匹配
                        query_terms.append(Wildcard(field, f'*{query_str}*'))
                    q = Or(query_terms) if query_terms else self.query_parser.parse(query_str)
                else:
                    # 使用标准查询解析器
                    q = self.query_parser.parse(query_str)
            else:
                # 空查询时返回所有结果
                from whoosh.query import Every
                q = Every()
            
            # 构建过滤条件
            filters = []
            
            if time_filter:
                filters.append(time_filter)
            
            if user_filter is not None:
                filters.append(Term('user_id', str(user_filter)))  # 转换为字符串
            
            if tag_filter:
                # 标签精确匹配
                filters.append(Term('tags', tag_filter))
            
            # 合并过滤条件
            q_filter = None
            if filters:
                if len(filters) == 1:
                    q_filter = filters[0]
                else:
                    q_filter = And(filters)
            
            # 执行搜索
            with self.ix.searcher() as searcher:
                result_page = searcher.search_page(
                    q, 
                    page_num, 
                    page_len,
                    filter=q_filter,
                    sortedby=sort_by,
                    reverse=True
                )
                
                # 构建结果
                hits = []
                for hit in result_page:
                    # 可选高亮
                    if self.highlighter is not None:
                        highlighted_title = self.highlighter.highlight_hit(hit, 'title') or hit.get('title', '')
                        highlighted_desc = self.highlighter.highlight_hit(hit, 'description') or hit.get('description', '')
                    else:
                        highlighted_title = hit.get('title', '')
                        highlighted_desc = hit.get('description', '')
                    
                    # 检测匹配字段
                    matched_fields = []
                    if query_str.strip():
                        query_lower = query_str.lower()
                        if hit.get('title', '').lower().find(query_lower) != -1:
                            matched_fields.append('标题')
                        if hit.get('description', '').lower().find(query_lower) != -1:
                            matched_fields.append('简介')
                        if hit.get('tags', '').lower().find(query_lower) != -1:
                            matched_fields.append('标签')
                        if hit.get('filename', '').lower().find(query_lower) != -1:
                            matched_fields.append('文件名')
                    
                    search_hit = SearchHit(
                        message_id=int(hit.get('message_id', 0)),
                        post_id=int(hit.get('post_id', 0)) if hit.get('post_id') else None,
                        title=hit.get('title', ''),
                        description=hit.get('description', ''),
                        tags=hit.get('tags', ''),
                        filename=hit.get('filename', ''),
                        link=hit.get('link', ''),
                        user_id=hit.get('user_id', 0),
                        username=hit.get('username', ''),
                        publish_time=hit.get('publish_time', datetime.now()),
                        views=hit.get('views', 0),
                        heat_score=hit.get('heat_score', 0),
                        highlighted_title=highlighted_title,
                        highlighted_desc=highlighted_desc,
                        matched_fields=matched_fields
                    )
                    hits.append(search_hit)
                
                return SearchResult(
                    hits=hits,
                    total_results=result_page.total,
                    is_last_page=result_page.is_last_page(),
                    page_num=page_num
                )
        
        except Exception as e:
            logger.error(f"搜索失败: {e}", exc_info=True)
            # 返回空结果
            return SearchResult(hits=[], total_results=0, is_last_page=True, page_num=page_num)
    
    def get_stats(self) -> dict:
        """
        获取索引统计信息
        
        Returns:
            dict: 统计信息
        """
        with self.ix.searcher() as searcher:
            return {
                'total_docs': searcher.doc_count_all(),
                'indexed_fields': list(self.ix.schema.names()),
            }
    
    def clear(self):
        """清空索引"""
        if self.index_dir.exists():
            shutil.rmtree(self.index_dir)
            self.index_dir.mkdir(parents=True)
            self.ix = index.create_in(str(self.index_dir), PostDocument.get_schema(), self.index_name)
            logger.info("索引已清空")
    
    def is_empty(self) -> bool:
        """检查索引是否为空"""
        return self.ix.is_empty()

    def optimize_index(self):
        """
        优化索引（合并段）。
        与历史脚本向后兼容，供 upgrade/检查脚本调用。
        """
        try:
            writer = self.ix.writer()
            writer.commit(optimize=True)
            logger.info("索引优化完成")
        except Exception as e:
            logger.error(f"索引优化失败: {e}", exc_info=True)


# 全局搜索引擎实例（单例模式）
_search_engine: Optional[PostSearchEngine] = None


def get_search_engine(index_dir: str = "search_index") -> PostSearchEngine:
    """
    获取全局搜索引擎实例
    
    Args:
        index_dir: 索引目录
    
    Returns:
        PostSearchEngine: 搜索引擎实例
    """
    global _search_engine
    if _search_engine is None:
        _search_engine = PostSearchEngine(index_dir)
    return _search_engine


def init_search_engine(index_dir: str = "search_index", from_scratch: bool = False):
    """
    初始化搜索引擎
    
    Args:
        index_dir: 索引目录
        from_scratch: 是否从头创建
        
    Returns:
        PostSearchEngine: 搜索引擎实例
    """
    global _search_engine
    _search_engine = PostSearchEngine(index_dir, from_scratch)
    logger.info("搜索引擎初始化完成")
    return _search_engine


# 向后兼容别名：历史代码从 utils.search_engine 导入 SearchEngine
SearchEngine = PostSearchEngine

