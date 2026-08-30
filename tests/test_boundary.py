"""
边界条件测试模块
测试空值、极端值、特殊字符等边界情况
"""
import pytest
import json
from unittest.mock import MagicMock, AsyncMock, patch


class TestEmptyInputs:
    """空值输入测试"""
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_process_tags_empty_string(self):
        """测试空字符串标签处理"""
        from utils.helper_functions import process_tags
        
        success, result = process_tags("")
        assert success is True
        assert result == ""
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_process_tags_none_like(self):
        """测试None类似值的标签处理"""
        from utils.helper_functions import process_tags
        
        # 空白字符串
        success, result = process_tags("   ")
        assert success is True
        assert result == ""
        
        # 只有分隔符
        success, result = process_tags(",,,")
        assert success is True
        assert result == ""
        
        success, result = process_tags("，，，")
        assert success is True
        assert result == ""
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_build_caption_empty_data(self):
        """测试空数据构建caption"""
        from utils.helper_functions import build_caption
        
        empty_data = {}
        caption = build_caption(empty_data)
        assert isinstance(caption, str)
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_build_caption_none_values(self):
        """测试None值构建caption"""
        from utils.helper_functions import build_caption
        
        none_data = {
            "link": None,
            "title": None,
            "note": None,
            "tags": None,
            "spoiler": None,
            "user_id": None,
        }
        caption = build_caption(none_data)
        assert isinstance(caption, str)
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_parse_json_list_empty(self):
        """测试空JSON列表解析"""
        from utils.helper_functions import parse_json_list
        
        assert parse_json_list("") == []
        assert parse_json_list(None) == []
        assert parse_json_list("[]") == []
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_file_validator_empty_inputs(self):
        """测试文件验证器空输入"""
        from utils.file_validator import FileTypeValidator
        
        validator = FileTypeValidator(allowed_types=".pdf,.zip")
        
        # 空文件名
        valid, msg = validator.validate("", "application/pdf")
        assert valid is False
        
        # 空MIME类型
        valid, msg = validator.validate("test.pdf", "")
        assert valid is True  # 应该通过扩展名验证
        
        # 都为空
        valid, msg = validator.validate("", "")
        assert valid is False
        
        # None值
        valid, msg = validator.validate(None, None)
        assert valid is False


class TestExtremeValues:
    """极端值测试"""
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_very_long_tags(self):
        """测试超长标签"""
        from utils.helper_functions import process_tags
        
        # 单个超长标签
        long_tag = "a" * 1000
        success, result = process_tags(long_tag)
        assert success is True
        # 标签应该被截断
        assert len(result) <= 100
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_many_tags(self):
        """测试大量标签"""
        from utils.helper_functions import process_tags
        
        # 100个标签
        many_tags = ",".join([f"tag{i}" for i in range(100)])
        success, result = process_tags(many_tags)
        assert success is True
        # 标签数量应该被限制
        tags = result.split()
        assert len(tags) <= 30  # ALLOWED_TAGS 默认值
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_very_long_caption(self):
        """测试超长caption"""
        from utils.helper_functions import build_caption
        
        long_data = {
            "link": "https://example.com/" + "a" * 500,
            "title": "T" * 500,
            "note": "N" * 2000,
            "tags": "#tag " * 100,
            "spoiler": "false",
            "user_id": 12345,
        }
        
        caption = build_caption(long_data)
        # Telegram caption 限制为 1024 字符
        assert len(caption) <= 1024
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_large_user_id(self):
        """测试大用户ID"""
        from utils.blacklist import is_blacklisted, is_owner
        
        large_ids = [
            2**31 - 1,      # 32位最大值
            2**31,          # 超过32位
            2**63 - 1,      # 64位最大值
            9999999999999,  # 13位数字
        ]
        
        for user_id in large_ids:
            # 不应该崩溃
            result = is_blacklisted(user_id)
            assert isinstance(result, bool)
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_negative_user_id(self):
        """测试负数用户ID"""
        from utils.blacklist import is_blacklisted
        
        negative_ids = [-1, -100, -2**31]
        
        for user_id in negative_ids:
            result = is_blacklisted(user_id)
            assert isinstance(result, bool)
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_zero_user_id(self):
        """测试零用户ID"""
        from utils.blacklist import is_blacklisted, is_owner
        
        result = is_blacklisted(0)
        assert isinstance(result, bool)
        
        result = is_owner(0)
        assert isinstance(result, bool)


class TestSpecialCharacters:
    """特殊字符测试"""
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_unicode_tags(self):
        """测试Unicode标签"""
        from utils.helper_functions import process_tags
        
        unicode_tags = [
            "中文标签",
            "日本語タグ",
            "한국어태그",
            "العربية",
            "🎉🎊🎈",
            "тег",
        ]
        
        for tag in unicode_tags:
            success, result = process_tags(tag)
            assert success is True
            if tag == "🎉🎊🎈":
                assert result == ""
            else:
                assert len(result) > 0
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_mixed_unicode_ascii(self):
        """测试混合Unicode和ASCII"""
        from utils.helper_functions import process_tags
        
        mixed_tags = "Python,中文,日本語,emoji🎉"
        success, result = process_tags(mixed_tags)
        assert success is True
        assert "#python" in result.lower()
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_special_punctuation(self):
        """测试特殊标点符号"""
        from utils.helper_functions import process_tags
        
        special_chars = "tag!@#$%^&*()_+-=[]{}|;':\",./<>?"
        success, result = process_tags(special_chars)
        assert success is True
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_newlines_and_tabs(self):
        """测试换行和制表符"""
        from utils.helper_functions import process_tags
        
        with_newlines = "tag1\ntag2\ttag3\r\ntag4"
        success, result = process_tags(with_newlines)
        assert success is True
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_escape_markdown_special_chars(self):
        """测试Markdown特殊字符转义"""
        from utils.helper_functions import escape_markdown
        
        special_chars = r"_*[]()~`>#+-=|{}.!"
        escaped = escape_markdown(special_chars)
        
        # 每个特殊字符都应该被转义
        for char in special_chars:
            if char in r'\_*[]()~>#+-=|{}.!':
                assert f"\\{char}" in escaped or char not in escaped
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_file_validator_special_filenames(self):
        """测试特殊文件名"""
        from utils.file_validator import FileTypeValidator
        
        validator = FileTypeValidator(allowed_types=".pdf,.zip")
        
        special_filenames = [
            ("文件.pdf", "application/pdf"),
            ("файл.pdf", "application/pdf"),
            ("ファイル.pdf", "application/pdf"),
            ("file with spaces.pdf", "application/pdf"),
            ("file-with-dashes.pdf", "application/pdf"),
            ("file_with_underscores.pdf", "application/pdf"),
            ("file.multiple.dots.pdf", "application/pdf"),
        ]
        
        for filename, mime in special_filenames:
            valid, msg = validator.validate(filename, mime)
            assert valid is True, f"{filename} should be valid"


class TestEdgeCases:
    """边缘情况测试"""
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_json_parse_invalid(self):
        """测试无效JSON解析"""
        from utils.helper_functions import parse_json_list
        
        invalid_jsons = [
            "{invalid}",
            "[1, 2, 3",
            "not json at all",
            "{'single': 'quotes'}",
            "[1, 2, 3,]",  # 尾随逗号
        ]
        
        for invalid in invalid_jsons:
            result = parse_json_list(invalid)
            assert result == []
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_json_parse_non_list(self):
        """测试非列表JSON解析"""
        from utils.helper_functions import parse_json_list
        
        non_lists = [
            '{"key": "value"}',
            '"string"',
            '123',
            'true',
            'null',
        ]
        
        for non_list in non_lists:
            result = parse_json_list(non_list)
            assert result == []
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_submission_mode_edge_cases(self):
        """测试投稿模式边缘情况"""
        from utils.helper_functions import get_submission_mode
        
        # None行
        assert get_submission_mode(None) == "mixed"
        
        # 空字典
        assert get_submission_mode({}) == "mixed"
        
        # 无mode键
        assert get_submission_mode({"other": "value"}) == "mixed"
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_file_validator_edge_extensions(self):
        """测试文件验证器边缘扩展名"""
        from utils.file_validator import FileTypeValidator
        
        validator = FileTypeValidator(allowed_types=".pdf")
        
        # 有效的边缘情况
        valid_cases = [
            ("file.PDF", "application/pdf"),  # 大写扩展名
            ("a.pdf", "application/pdf"),     # 最短有效文件名
            ("my.file.pdf", "application/pdf"),  # 多个点
        ]
        
        for filename, mime in valid_cases:
            valid, msg = validator.validate(filename, mime)
            assert valid is True, f"{filename} should be valid"
        
        # 边缘情况 - 验证不会崩溃
        edge_cases = [
            (".pdf", "application/pdf"),      # 只有扩展名（无文件名）
            ("pdf", "application/pdf"),       # 没有点
            ("file.", "application/pdf"),     # 空扩展名
            ("..pdf", "application/pdf"),     # 双点开头
        ]
        
        for filename, mime in edge_cases:
            valid, msg = validator.validate(filename, mime)
            # 这些边缘情况可能有效也可能无效，取决于实现
            # 我们只验证不会崩溃
            assert isinstance(valid, bool)


class TestDataTypeHandling:
    """数据类型处理测试"""
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_build_caption_with_row_object(self):
        """测试使用Row对象构建caption"""
        from utils.helper_functions import build_caption
        
        # 模拟sqlite3.Row对象
        class MockRow:
            def __init__(self, data):
                self._data = data
            
            def __getitem__(self, key):
                return self._data.get(key)
            
            def __contains__(self, key):
                return key in self._data
            
            def keys(self):
                return self._data.keys()
        
        row = MockRow({
            "link": "https://example.com",
            "title": "Test Title",
            "note": "Test Note",
            "tags": "#test",
            "spoiler": "false",
            "user_id": 12345,
            "username": "testuser"
        })
        
        caption = build_caption(row)
        assert isinstance(caption, str)
        assert "Test Title" in caption
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_process_tags_with_numbers(self):
        """测试数字标签（保留 Pixiv 原始标签，不擅自改名）"""
        from utils.helper_functions import process_tags

        numeric_tags = "123,456,789"
        success, result = process_tags(numeric_tags)
        assert success is True
        assert "#123" in result
        assert "#456" in result

    @pytest.mark.boundary
    @pytest.mark.unit
    def test_process_tags_sanitizes_telegram_illegal_chars(self):
        """非法字符的标签要净化成可点击 hashtag（#r-18 不会被 Telegram 解析）"""
        from utils.helper_functions import process_tags

        ok, result = process_tags("r-18,R-18G,R 18,中文/中国語/chinese,カフカ(スターレイル),絵🎨")
        assert ok is True
        # r-18 / r-18g 归一
        assert "#r18" in result
        assert "#r18g" in result
        # 不含 Telegram hashtag 无法解析的字符
        assert "-" not in result
        assert "/" not in result
        assert "(" not in result and ")" not in result
        assert "🎨" not in result
        # 斜杠分隔的作品标签各自成词
        assert "#中文" in result and "#中国語" in result and "#chinese" in result
        # 括号被移除但标签保留
        assert "#カフカスターレイル" in result
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_process_tags_with_hash_prefix(self):
        """测试已有#前缀的标签"""
        from utils.helper_functions import process_tags
        
        prefixed_tags = "#tag1,#tag2,#tag3"
        success, result = process_tags(prefixed_tags)
        assert success is True
        # 不应该有双#
        assert "##" not in result


class TestConcurrencyBoundary:
    """并发边界测试"""
    
    @pytest.mark.boundary
    @pytest.mark.unit
    def test_blacklist_set_thread_safety(self):
        """测试黑名单集合的线程安全性"""
        from utils.blacklist import _blacklist, is_blacklisted
        import threading
        
        errors = []
        
        def add_and_check():
            try:
                for i in range(100):
                    test_id = 1000000 + i
                    _blacklist.add(test_id)
                    is_blacklisted(test_id)
                    _blacklist.discard(test_id)
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=add_and_check) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # 不应该有错误
        assert len(errors) == 0
