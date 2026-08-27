.PHONY: help build up down start stop restart logs status clean backup migrate install dev

# 默认目标
help:
	@echo "====================================="
	@echo "  TelePost 管理命令"
	@echo "====================================="
	@echo ""
	@echo "使用方法: make [命令]"
	@echo ""
	@echo "📦 Docker 部署:"
	@echo "  deploy      - 一键部署（首次使用）"
	@echo "  build       - 构建 Docker 镜像"
	@echo "  rebuild     - 强制重新构建镜像"
	@echo "  up          - 启动容器（后台运行）"
	@echo "  down        - 停止并删除容器"
	@echo "  restart     - 重启容器"
	@echo ""
	@echo "📋 日志和监控:"
	@echo "  logs        - 查看实时日志"
	@echo "  logs-tail   - 查看最近100行日志"
	@echo "  status      - 查看容器状态"
	@echo "  shell       - 进入容器 shell"
	@echo ""
	@echo "🔧 数据管理:"
	@echo "  migrate     - 运行数据迁移（导入搜索索引）"
	@echo "  backup      - 备份数据"
	@echo "  clean       - 清理容器和镜像"
	@echo ""
	@echo "💻 本地开发:"
	@echo "  install     - 安装依赖"
	@echo "  dev         - 启动开发环境"
	@echo "  check       - 检查配置"
	@echo ""
	@echo "🔄 更新:"
	@echo "  update      - 更新到最新版本"
	@echo ""

# 一键部署
deploy:
	@echo "🚀 开始部署..."
	@./deploy.sh

# 构建镜像
build:
	@echo "🔨 构建 Docker 镜像..."
	docker-compose build

# 强制重新构建
rebuild:
	@echo "🔨 强制重新构建 Docker 镜像..."
	docker-compose build --no-cache

# 启动容器
up:
	@echo "⬆️  启动容器..."
	docker-compose up -d
	@echo "✅ 容器已启动"

# 停止并删除容器
down:
	@echo "⬇️  停止容器..."
	docker-compose down
	@echo "✅ 容器已停止"

# 重启容器
restart:
	@echo "🔄 重启容器..."
	docker-compose restart
	@echo "✅ 容器已重启"

# 查看实时日志
logs:
	@echo "📋 查看实时日志（按 Ctrl+C 退出）..."
	docker-compose logs -f

# 查看最近100行日志
logs-tail:
	@echo "📋 查看最近100行日志..."
	docker-compose logs --tail=100

# 查看容器状态
status:
	@echo "📊 容器状态:"
	@docker-compose ps
	@echo ""
	@echo "💻 资源使用:"
	@docker stats --no-stream telepost 2>/dev/null || echo "容器未运行"

# 进入容器 shell
shell:
	@echo "🐚 进入容器 shell（输入 exit 退出）..."
	docker exec -it telepost /bin/bash

# 运行数据迁移
migrate:
	@echo "🔄 运行数据迁移..."
	@if docker ps | grep -q telepost; then \
		docker exec telepost python migrate_to_search.py; \
		echo "✅ 迁移完成"; \
	else \
		echo "❌ 容器未运行，请先启动容器: make up"; \
	fi

# 备份数据
backup:
	@echo "💾 备份数据..."
	@mkdir -p backups
	@tar -czf backups/backup-$$(date +%Y%m%d-%H%M%S).tar.gz config.ini data/ logs/ 2>/dev/null || true
	@echo "✅ 备份完成: backups/backup-$$(date +%Y%m%d-%H%M%S).tar.gz"
	@ls -lh backups/ | tail -5

# 清理容器和镜像
clean:
	@echo "🧹 清理容器和镜像..."
	docker-compose down -v
	docker system prune -f
	@echo "✅ 清理完成"

# 本地安装依赖
install:
	@echo "📦 安装依赖..."
	pip3 install -r requirements.txt
	@echo "✅ 依赖安装完成"

# 本地开发环境
dev:
	@echo "💻 启动开发环境..."
	@chmod +x start.sh
	@./start.sh

# 检查配置
check:
	@echo "🔍 检查配置..."
	@if [ -f "config.ini" ]; then \
		python3 check_config.py; \
	else \
		echo "❌ 配置文件不存在"; \
		echo "请复制: cp config.ini.example config.ini"; \
	fi

# 更新到最新版本
update:
	@echo "🔄 更新到最新版本..."
	@make backup
	@echo "⬇️  拉取最新代码..."
	git pull
	@echo "🔨 重新构建镜像..."
	docker-compose build --no-cache
	@echo "🚀 重启容器..."
	docker-compose up -d
	@echo "✅ 更新完成"
	@echo ""
	@echo "📋 查看日志确认运行正常:"
	@echo "   make logs"

