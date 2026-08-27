#!/bin/bash
# Fly.io 快速部署脚本
# TeleSubmit v2 - Telegram 投稿机器人

set -e

echo "========================================="
echo "  TeleSubmit v2 - Fly.io 部署脚本"
echo "========================================="
echo ""

# 检查 flyctl 是否安装
if ! command -v flyctl &> /dev/null; then
    echo "❌ 错误：flyctl 未安装"
    echo ""
    echo "请先安装 Fly.io CLI:"
    echo "  macOS/Linux: curl -L https://fly.io/install.sh | sh"
    echo "  Windows:     iwr https://fly.io/install.ps1 -useb | iex"
    echo ""
    echo "详见: https://fly.io/docs/hands-on/install-flyctl/"
    exit 1
fi

echo "✅ flyctl 已安装: $(flyctl version)"
echo ""

# 检查是否已登录
if ! flyctl auth whoami &> /dev/null; then
    echo "请先登录 Fly.io:"
    flyctl auth login
    echo ""
fi

echo "✅ 已登录: $(flyctl auth whoami)"
echo ""

# 检查配置文件
if [ ! -f "config.ini" ]; then
    echo "⚠️  未找到 config.ini，从示例创建..."
    cp config.ini.example config.ini
    echo ""
    echo "❗ 请编辑 config.ini 填入以下信息："
    echo "  - BOT_TOKEN (从 @BotFather 获取)"
    echo "  - CHANNEL_ID (频道 ID 或用户名)"
    echo "  - OWNER_ID (管理员 User ID)"
    echo ""
    read -p "按回车继续编辑配置文件..."
    ${EDITOR:-nano} config.ini
fi

echo "✅ 配置文件存在"
echo ""

# 询问是否首次部署
read -p "这是首次部署吗? (y/n): " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo "开始初始化应用..."
    echo ""
    
    # 执行 fly launch（不立即部署）
    flyctl launch --no-deploy
    
    echo ""
    echo "========================================="
    echo "  应用已创建，现在设置密钥"
    echo "========================================="
    echo ""
    
    # 读取配置
    BOT_TOKEN=$(grep "^TOKEN" config.ini | cut -d'=' -f2 | tr -d ' ')
    CHANNEL_ID=$(grep "^CHANNEL_ID" config.ini | cut -d'=' -f2 | tr -d ' ')
    OWNER_ID=$(grep "^OWNER_ID" config.ini | cut -d'=' -f2 | tr -d ' ')
    
    # 获取应用名称
    APP_NAME=$(grep "^app" fly.toml | cut -d'"' -f2)
    WEBHOOK_URL="https://${APP_NAME}.fly.dev"
    
    echo "应用名称: $APP_NAME"
    echo "Webhook URL: $WEBHOOK_URL"
    echo ""
    
    # 设置密钥
    # 注意：代码读取 TOKEN（兼容 BOT_TOKEN / TELEGRAM_BOT_TOKEN 别名），
    # 这里同时设置两个名字，确保任何版本都能读到
    echo "设置 TOKEN..."
    flyctl secrets set TOKEN="$BOT_TOKEN" BOT_TOKEN="$BOT_TOKEN"
    
    echo "设置 CHANNEL_ID..."
    flyctl secrets set CHANNEL_ID="$CHANNEL_ID"
    
    echo "设置 OWNER_ID..."
    flyctl secrets set OWNER_ID="$OWNER_ID"
    
    echo "设置 WEBHOOK_URL..."
    flyctl secrets set WEBHOOK_URL="$WEBHOOK_URL"
    
    echo ""
    echo "✅ 密钥设置完成"
    echo ""
fi

# 部署
echo "========================================="
echo "  开始部署应用"
echo "========================================="
echo ""

flyctl deploy

echo ""
echo "========================================="
echo "  部署完成！"
echo "========================================="
echo ""

# 获取应用信息
APP_NAME=$(grep "^app" fly.toml | cut -d'"' -f2)
WEBHOOK_URL="https://${APP_NAME}.fly.dev"

echo "应用 URL: $WEBHOOK_URL"
echo ""

# 询问是否设置 Webhook
read -p "是否立即设置 Telegram Webhook? (y/n): " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    BOT_TOKEN=$(grep "^TOKEN" config.ini | cut -d'=' -f2 | tr -d ' ')
    
    echo ""
    echo "正在设置 Webhook..."
    
    RESPONSE=$(curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
        -d "url=${WEBHOOK_URL}/webhook" \
        -d "max_connections=40")
    
    echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
    echo ""
    
    echo "验证 Webhook 设置..."
    WEBHOOK_INFO=$(curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo")
    echo "$WEBHOOK_INFO" | python3 -m json.tool 2>/dev/null || echo "$WEBHOOK_INFO"
fi

echo ""
echo "========================================="
echo "  常用命令"
echo "========================================="
echo ""
echo "查看状态:  flyctl status"
echo "查看日志:  flyctl logs"
echo "重启应用:  flyctl apps restart $APP_NAME"
echo "SSH 连接:  flyctl ssh console"
echo ""
echo "✅ 部署完成！向机器人发送 /start 测试吧！"
