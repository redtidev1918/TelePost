"""
辅助函数测试
"""
import pytest
from utils.helper_functions import (
    process_tags,
    escape_markdown,
    build_caption
)


class TestHelperFunctions:
    """辅助函数测试类"""
    
    @pytest.mark.unit
    def test_process_tags_basic(self):
        """测试基本标签处理"""
        success, result = process_tags("Python,编程,学习")
        
        assert success is True
        assert "#python" in result
        assert "#编程" in result
        assert "#学习" in result
    
    @pytest.mark.unit
    def test_process_tags_with_hash(self):
        """测试已有#号的标签"""
        success, result = process_tags("#Python #编程")
        
        assert success is True
        assert "#python" in result
        assert "#编程" in result
    
    @pytest.mark.unit
    def test_process_tags_mixed_separators(self):
        """测试混合分隔符"""
        success, result = process_tags("Python, 编程，学习 开发")
        
        assert success is True
        tags = result.split()
        assert len(tags) == 4
    
    @pytest.mark.unit
    def test_process_tags_limit(self):
        """测试标签数量限制"""
        from config.settings import ALLOWED_TAGS
        long_tags = ",".join([f"tag{i}" for i in range(ALLOWED_TAGS + 5)])
        success, result = process_tags(long_tags)
        
        assert success is True
        tags = result.split()
        assert len(tags) <= ALLOWED_TAGS
    
    @pytest.mark.unit
    def test_process_tags_empty(self):
        """测试空标签"""
        success, result = process_tags("")
        
        assert success is True
        assert result == ""
    
    @pytest.mark.unit
    def test_process_tags_whitespace(self):
        """测试只有空格的标签"""
        success, result = process_tags("   ,  ,  ")
        
        assert success is True
        assert result == ""
    
    @pytest.mark.unit
    def test_process_tags_long_tag(self):
        """测试超长标签"""
        long_tag = "a" * 50  # 超过30字符的标签
        success, result = process_tags(long_tag)
        
        assert success is True
        # 标签应该被截断到30字符（加上#号）
        assert len(result) <= 31
    
    @pytest.mark.unit
    def test_escape_markdown_basic(self):
        """测试基本 Markdown 转义"""
        text = "Hello *World*"
        result = escape_markdown(text)
        
        assert "\\*" in result
    
    @pytest.mark.unit
    def test_escape_markdown_multiple_chars(self):
        """测试多个特殊字符转义"""
        text = "_test_ [link](url) #tag"
        result = escape_markdown(text)
        
        assert "\\_" in result
        assert "\\[" in result
        assert "\\]" in result
        assert "\\(" in result
        assert "\\)" in result
        assert "\\#" in result
    
    @pytest.mark.unit
    def test_escape_markdown_no_special_chars(self):
        """测试无特殊字符的文本"""
        text = "Hello World"
        result = escape_markdown(text)
        
        assert result == text
    
    @pytest.mark.unit
    def test_build_caption_basic(self):
        """测试基本标题构建"""
        # Using dict instead of class
        data = {"link": "https://example.com", "title": "测试标题", "note": "测试内容", "tags": "#测试 #Python", "spoiler": "false", "user_id": 123456789}
        result = build_caption(data)
        
        assert isinstance(result, str)
        assert len(result) > 0
    
    @pytest.mark.unit
    def test_build_caption_empty_fields(self):
        """测试空字段的标题构建"""
        # Using dict instead of class
        data = {"link": "", "title": "", "note": "", "tags": "", "spoiler": "false", "user_id": 123456789}
        result = build_caption(data)
        
        assert isinstance(result, str)
    
    @pytest.mark.unit
    def test_build_caption_with_spoiler(self):
        """测试带剧透标记的标题"""
        # Using dict instead of class
        data = {"link": "", "title": "测试", "note": "内容", "tags": "", "spoiler": "true", "user_id": 123456789}
        result = build_caption(data)
        
        assert "⚠️" in result or "点击查看" in result
    
    @pytest.mark.unit
    def test_build_caption_length_limit(self):
        """测试标题长度限制"""
        # Using dict instead of class
        data = {"link": "https://example.com", "title": "标题" * 100, "note": "内容" * 500, "tags": "#标签 " * 50, "spoiler": "false", "user_id": 123456789}
        result = build_caption(data)
        
        # Telegram caption 最大长度为 1024
        assert len(result) <= 1024


class TestTagProcessingEdgeCases:
    """标签处理边界情况测试"""
    
    @pytest.mark.unit
    def test_tags_with_special_chars(self):
        """测试带特殊字符的标签"""
        success, result = process_tags("Python3.9, C++, .NET")
        
        assert success is True
        assert len(result) > 0
    
    @pytest.mark.unit
    def test_tags_with_unicode(self):
        """测试 Unicode 标签"""
        success, result = process_tags("编程,プログラミング,программирование")
        
        assert success is True
        assert "编程" in result
    
    @pytest.mark.unit
    def test_tags_case_insensitive(self):
        """测试标签大小写"""
        success, result = process_tags("Python,PYTHON,python")
        
        assert success is True
        # 所有标签应该转换为小写并去重
        assert result == "#python"
