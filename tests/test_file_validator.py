"""
文件验证器测试
"""
import pytest
from utils.file_validator import FileTypeValidator


class TestFileTypeValidator:
    """文件类型验证器测试类"""
    
    @pytest.fixture
    def validator_all(self):
        """创建允许所有类型的验证器"""
        return FileTypeValidator(allowed_types="*")
    
    @pytest.fixture
    def validator_images(self):
        """创建只允许图片的验证器"""
        return FileTypeValidator(allowed_types=".jpg,.jpeg,.png,.gif,.webp")
    
    @pytest.fixture
    def validator_documents(self):
        """创建只允许文档的验证器"""
        return FileTypeValidator(allowed_types=".pdf,.doc,.docx,.txt,.zip")
    
    @pytest.mark.unit
    def test_allow_all_types(self, validator_all):
        """测试允许所有类型"""
        valid, msg = validator_all.validate('test.jpg', 'image/jpeg')
        assert valid is True
        
        valid, msg = validator_all.validate('test.exe', 'application/x-msdownload')
        assert valid is True
    
    @pytest.mark.unit
    def test_validate_image_types(self, validator_images):
        """测试图片类型验证"""
        valid_images = [
            ('test.jpg', 'image/jpeg'),
            ('test.jpeg', 'image/jpeg'),
            ('test.png', 'image/png'),
            ('test.gif', 'image/gif'),
            ('test.webp', 'image/webp')
        ]
        
        for filename, mime in valid_images:
            valid, msg = validator_images.validate(filename, mime)
            assert valid is True, f"{filename} should be valid"
    
    @pytest.mark.unit
    def test_validate_document_types(self, validator_documents):
        """测试文档类型验证"""
        valid_docs = [
            ('test.pdf', 'application/pdf'),
            ('test.doc', 'application/msword'),
            ('test.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
            ('test.txt', 'text/plain'),
            ('test.zip', 'application/zip')
        ]
        
        for filename, mime in valid_docs:
            valid, msg = validator_documents.validate(filename, mime)
            assert valid is True, f"{filename} should be valid"
    
    @pytest.mark.unit
    def test_invalid_file_types(self, validator_images):
        """测试无效文件类型"""
        invalid_files = [
            ('test.exe', 'application/x-msdownload'),
            ('test.bat', 'application/x-bat'),
            ('test.sh', 'application/x-sh'),
        ]
        
        for filename, mime in invalid_files:
            valid, msg = validator_images.validate(filename, mime)
            assert valid is False, f"{filename} should not be valid for image validator"
    
    @pytest.mark.unit
    def test_case_insensitive(self, validator_images):
        """测试大小写不敏感"""
        valid, msg = validator_images.validate('TEST.JPG', 'image/jpeg')
        assert valid is True
        
        valid, msg = validator_images.validate('Test.PNG', 'image/png')
        assert valid is True
    
    @pytest.mark.unit
    def test_filename_with_spaces(self, validator_images):
        """测试带空格的文件名"""
        valid, msg = validator_images.validate('my photo.jpg', 'image/jpeg')
        assert valid is True
    
    @pytest.mark.unit
    def test_filename_with_multiple_dots(self, validator_documents):
        """测试多个点的文件名"""
        valid, msg = validator_documents.validate('file.backup.pdf', 'application/pdf')
        assert valid is True
        
        valid, msg = validator_documents.validate('archive.tar.gz', 'application/x-gzip')
        # gz不在允许列表中，应该失败
        assert valid is False
    
    @pytest.mark.unit
    def test_empty_filename(self, validator_images):
        """测试空文件名"""
        valid, msg = validator_images.validate('', 'image/jpeg')
        # 可以通过MIME类型验证
        assert valid is False  # 没有扩展名，只有MIME
    
    @pytest.mark.unit
    def test_filename_without_extension(self, validator_images):
        """测试无扩展名的文件"""
        valid, msg = validator_images.validate('filename', 'image/jpeg')
        # 没有扩展名但有正确的MIME类型，应该失败（因为我们的validator只检查扩展名）
        assert valid is False
    
    @pytest.mark.unit
    def test_mime_type_validation(self, validator_all):
        """测试MIME类型验证"""
        # 允许所有类型的验证器
        valid, msg = validator_all.validate('', 'image/jpeg')
        assert valid is True
    
    @pytest.mark.unit
    def test_error_message_generation(self, validator_images):
        """测试错误消息生成"""
        valid, msg = validator_images.validate('test.exe', 'application/x-msdownload')
        
        assert valid is False
        assert len(msg) > 0
        assert "不支持" in msg or "允许" in msg


class TestFileValidatorEdgeCases:
    """文件验证器边界情况测试"""
    
    @pytest.mark.unit
    def test_validator_with_mime_wildcard(self):
        """测试MIME通配符"""
        validator = FileTypeValidator(allowed_types="image/*")
        
        valid, msg = validator.validate('test.jpg', 'image/jpeg')
        assert valid is True
        
        valid, msg = validator.validate('test.png', 'image/png')
        assert valid is True
        
        valid, msg = validator.validate('test.pdf', 'application/pdf')
        assert valid is False
    
    @pytest.mark.unit
    def test_validator_mixed_types(self):
        """测试混合类型配置"""
        validator = FileTypeValidator(allowed_types=".pdf,.zip,image/*")
        
        # 扩展名匹配
        valid, msg = validator.validate('test.pdf', 'application/pdf')
        assert valid is True
        
        # MIME通配符匹配
        valid, msg = validator.validate('test.jpg', 'image/jpeg')
        assert valid is True
        
        # 不匹配
        valid, msg = validator.validate('test.doc', 'application/msword')
        assert valid is False
    
    @pytest.mark.unit
    def test_validator_empty_config(self):
        """测试空配置"""
        validator = FileTypeValidator(allowed_types="")
        
        # 空配置应该允许所有类型
        valid, msg = validator.validate('test.any', 'any/type')
        assert valid is True
    
    @pytest.mark.unit
    def test_get_allowed_types_description(self):
        """测试获取允许类型描述"""
        validator_all = FileTypeValidator(allowed_types="*")
        desc = validator_all.get_allowed_types_description()
        assert "所有" in desc
        
        validator_specific = FileTypeValidator(allowed_types=".pdf,.zip")
        desc = validator_specific.get_allowed_types_description()
        assert ".pdf" in desc
        assert ".zip" in desc


class TestBlockedFileTypes:
    """投稿文件黑名单测试：危险类型默认拦截，可配置/关闭。"""

    BLOCKED = ".exe,.bat,.cmd,.scr,.com,.pif,.msi,.ps1,.vbs,.js,.jar,.apk,.sh"

    @pytest.fixture
    def validator_default(self):
        """默认黑名单（无 blocked 参数 → 内置危险类型集）"""
        from config.settings import DEFAULT_BLOCKED_FILE_TYPES
        return FileTypeValidator(allowed_types="*", blocked_types=DEFAULT_BLOCKED_FILE_TYPES)

    @pytest.fixture
    def validator_custom(self):
        return FileTypeValidator(allowed_types="*", blocked_types=self.BLOCKED)

    @pytest.fixture
    def validator_off(self):
        return FileTypeValidator(allowed_types="*", blocked_types="none")

    @pytest.mark.unit
    def test_default_blocklist_blocks_executables(self, validator_default):
        for filename, mime in [
            ("virus.exe", "application/x-msdownload"),
            ("run.bat", "application/x-bat"),
            ("payload.ps1", "text/x-powershell"),
            ("dropper.js", "application/javascript"),
            ("app.apk", "application/vnd.android.package-archive"),
            ("archive.zip", "application/zip"),
            ("archive.rar", "application/x-rar-compressed"),
            ("archive.7z", "application/x-7z-compressed"),
        ]:
            assert validator_default.is_blocked(filename, mime), f"{filename} should be blocked"

    @pytest.mark.unit
    def test_media_and_text_pass_default_blocklist(self, validator_default):
        for filename, mime in [
            ("photo.jpg", "image/jpeg"),
            ("clip.mp4", "video/mp4"),
            ("anim.gif", "image/gif"),
            ("song.mp3", "audio/mpeg"),
            ("readme.txt", "text/plain"),
            ("doc.pdf", "application/pdf"),
            ("notes.md", "text/markdown"),
            ("code.py", "text/x-python"),
            ("data.json", "application/json"),
        ]:
            assert not validator_default.is_blocked(filename, mime), f"{filename} should not be blocked"

    @pytest.mark.unit
    def test_check_document_blocked_wins_over_allowlist(self, validator_default):
        ok, reason = validator_default.check_document("evil.exe", "application/x-msdownload")
        assert ok is False and reason == "blocked"
        ok, reason = validator_default.check_document("note.txt", "text/plain")
        assert ok is True and reason == ""

    @pytest.mark.unit
    def test_custom_blocklist_replaces_default(self, validator_custom):
        assert validator_custom.is_blocked("evil.exe", "")
        assert not validator_custom.is_blocked("archive.zip", "application/zip")

    @pytest.mark.unit
    def test_disabled_blocklist(self, validator_off):
        assert not validator_off.is_blocked("evil.exe", "application/x-msdownload")

    @pytest.mark.unit
    def test_get_blocked_description(self, validator_default):
        desc = validator_default.get_blocked_description()
        assert ".exe" in desc
        assert ".zip" in desc


@pytest.mark.parametrize("name,mime", [
    ("virus.EXE ", "image/png"),
    ("bundle.ZIP.", "audio/mpeg"),
    ("note.txt", "Application/X-Msdownload; charset=binary"),
    ("note.txt", "application/zip"),
])
def test_default_policy_cannot_be_bypassed_by_mime_or_windows_suffix(name, mime):
    from config.settings import DEFAULT_BLOCKED_FILE_TYPES
    assert FileTypeValidator("*", DEFAULT_BLOCKED_FILE_TYPES).is_blocked(name, mime)


def test_two_bots_inherit_or_override_file_policy():
    import run
    env = {"BLOCKED_FILE_TYPES": ".exe,.zip", "BOT2_BLOCKED_FILE_TYPES": ".exe,.zip,.pdf"}
    assert run.build_bot_env(1, env)["BLOCKED_FILE_TYPES"] == ".exe,.zip"
    assert run.build_bot_env(2, env)["BLOCKED_FILE_TYPES"] == ".exe,.zip,.pdf"


def test_blocked_filename_is_html_escaped():
    from ui.messages import MessageFormatter
    message = MessageFormatter.upload_blocked_type("<virus>.exe", "")
    assert "<virus>" not in message
    assert "&lt;virus&gt;.exe" in message
