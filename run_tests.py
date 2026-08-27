#!/usr/bin/env python3
"""
全面测试脚本 - 在推送前验证所有功能
"""
import sys
import os
import ast
import logging
from pathlib import Path
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

class TestRunner:
    """测试运行器"""
    
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
        
    def run_all_tests(self):
        """运行所有测试"""
        print('='*80)
        print('🧪 TelePost 全面测试')
        print('='*80)
        print()
        
        # 1. 语法检查
        self.test_syntax()
        
        # 2. 导入测试
        self.test_imports()
        
        # 3. 配置文件测试
        self.test_config()
        
        # 4. 数据库测试
        self.test_database()
        
        # 5. 搜索引擎测试
        self.test_search_engine()
        
        # 6. 工具函数测试
        self.test_utilities()
        
        # 显示测试结果
        self.show_results()
        
        return self.failed == 0
    
    def test_syntax(self):
        """测试 Python 语法"""
        print('📝 测试 1: Python 语法检查')
        print('-'*80)
        
        python_files = [
            'main.py',
            'config/settings.py',
            'handlers/command_handlers.py',
            'handlers/search_handlers.py',
            'handlers/stats_handlers.py',
            'handlers/publish.py',
            'handlers/callback_handlers.py',
            'utils/database.py',
            'utils/search_engine.py',
            'utils/helper_functions.py',
        ]
        
        for filepath in python_files:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    ast.parse(f.read(), filename=filepath)
                print(f'  ✅ {filepath}')
                self.passed += 1
            except SyntaxError as e:
                print(f'  ❌ {filepath}: {e}')
                self.failed += 1
                self.errors.append(f'语法错误 in {filepath}: {e}')
            except FileNotFoundError:
                print(f'  ⚠️  {filepath}: 文件不存在')
        
        print()
    
    def test_imports(self):
        """测试关键模块导入"""
        print('📦 测试 2: 模块导入检查')
        print('-'*80)
        
        modules = [
            ('config.settings', '配置模块'),
            ('utils.database', '数据库工具'),
            ('utils.search_engine', '搜索引擎'),
            ('utils.helper_functions', '辅助函数'),
        ]
        
        for module_name, description in modules:
            try:
                __import__(module_name)
                print(f'  ✅ {description} ({module_name})')
                self.passed += 1
            except ImportError as e:
                print(f'  ❌ {description} ({module_name}): {e}')
                self.failed += 1
                self.errors.append(f'导入错误 {module_name}: {e}')
            except Exception as e:
                print(f'  ⚠️  {description} ({module_name}): {e}')
        
        print()
    
    def test_config(self):
        """测试配置文件"""
        print('⚙️  测试 3: 配置文件检查')
        print('-'*80)
        
        # 检查示例配置
        if os.path.exists('config.ini.example'):
            print('  ✅ config.ini.example 存在')
            self.passed += 1
        else:
            print('  ❌ config.ini.example 不存在')
            self.failed += 1
            self.errors.append('缺少 config.ini.example')
        
        # 检查配置模块
        try:
            from config import settings
            
            # 检查必要的配置项
            required_attrs = [
                'TOKEN', 'CHANNEL_ID', 'DB_PATH',
                'OWNER_ID', 'ALLOWED_FILE_TYPES'
            ]
            
            for attr in required_attrs:
                if hasattr(settings, attr):
                    print(f'  ✅ 配置项 {attr} 已定义')
                    self.passed += 1
                else:
                    print(f'  ❌ 配置项 {attr} 未定义')
                    self.failed += 1
                    self.errors.append(f'缺少配置项 {attr}')
                    
        except Exception as e:
            print(f'  ❌ 加载配置失败: {e}')
            self.failed += 1
            self.errors.append(f'配置加载错误: {e}')
        
        print()
    
    def test_database(self):
        """测试数据库功能"""
        print('🗄️  测试 4: 数据库功能检查')
        print('-'*80)
        
        try:
            from utils.database import initialize_database
            from database.db_manager import get_db
            
            # 测试初始化
            initialize_database()
            print('  ✅ 会话数据库初始化成功')
            self.passed += 1
            
            # 测试数据库管理器
            db = get_db()
            print('  ✅ 数据库管理器加载成功')
            self.passed += 1
            
        except Exception as e:
            print(f'  ❌ 数据库测试失败: {e}')
            self.failed += 1
            self.errors.append(f'数据库错误: {e}')
        
        print()
    
    def test_search_engine(self):
        """测试搜索引擎"""
        print('🔍 测试 5: 搜索引擎检查')
        print('-'*80)
        
        try:
            from utils.search_engine import get_search_engine, PostDocument
            
            # 获取搜索引擎实例
            engine = get_search_engine()
            print('  ✅ 搜索引擎初始化成功')
            self.passed += 1
            
            # 测试文档结构
            doc = PostDocument(
                message_id=1,
                title="测试标题",
                description="测试描述"
            )
            print('  ✅ PostDocument 创建成功')
            self.passed += 1
            
        except Exception as e:
            print(f'  ❌ 搜索引擎测试失败: {e}')
            self.failed += 1
            self.errors.append(f'搜索引擎错误: {e}')
        
        print()
    
    def test_utilities(self):
        """测试工具函数"""
        print('🛠️  测试 6: 工具函数检查')
        print('-'*80)
        
        tests = [
            ('utils.helper_functions', 'build_caption', '构建消息标题'),
            ('utils.heat_calculator', 'calculate_multi_message_heat', '热度计算'),
            ('utils.file_validator', 'FileTypeValidator', '文件验证器'),
        ]
        
        for module_name, func_name, description in tests:
            try:
                module = __import__(module_name, fromlist=[func_name])
                func = getattr(module, func_name)
                print(f'  ✅ {description} ({func_name})')
                self.passed += 1
            except (ImportError, AttributeError) as e:
                print(f'  ❌ {description} ({func_name}): {e}')
                self.failed += 1
                self.errors.append(f'{description} 错误: {e}')
        
        print()
    
    def show_results(self):
        """显示测试结果"""
        print('='*80)
        print('📊 测试结果总结')
        print('='*80)
        print()
        
        total = self.passed + self.failed
        success_rate = (self.passed / total * 100) if total > 0 else 0
        
        print(f'  总测试数: {total}')
        print(f'  ✅ 通过: {self.passed}')
        print(f'  ❌ 失败: {self.failed}')
        print(f'  成功率: {success_rate:.1f}%')
        print()
        
        if self.errors:
            print('❌ 发现的错误:')
            for i, error in enumerate(self.errors, 1):
                print(f'  {i}. {error}')
            print()
        
        if self.failed == 0:
            print('🎉 所有测试通过！可以安全推送代码。')
        else:
            print('⚠️  存在失败的测试，请修复后再推送。')
        
        print('='*80)

def main():
    """主函数"""
    runner = TestRunner()
    success = runner.run_all_tests()
    
    return 0 if success else 1

if __name__ == '__main__':
    sys.exit(main())

