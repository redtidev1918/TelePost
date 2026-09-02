# PythonAnywhere Webhook 模式部署指南

本指南详细说明如何在 PythonAnywhere 上以 Webhook 模式部署 TelePost 项目。

---

## 前提条件

### 账号要求

> **💡 强烈推荐使用付费账号**
> 
> - ✅ **推荐**：PythonAnywhere 付费账号（Hacker 计划 $5/月起）
>   - 支持访问外部 API（可以发送 Telegram 消息）
>   - 提供 HTTPS 域名
>   - 更稳定的运行环境
> 
> - ⚠️ **不推荐**：免费账号
>   - **无法发送** Telegram 消息（无法访问 `api.telegram.org`）
>   - 只能接收消息，无法回复
>   - **机器人基本无法正常工作**
> 
> **结论**：免费账号无法正常运行此机器人，请使用付费账号。

### 必需信息

- Telegram Bot Token（从 [@BotFather](https://t.me/BotFather) 获取）
- 频道 ID 或用户名
- 管理员 User ID（从 [@userinfobot](https://t.me/userinfobot) 获取）
- **一台可以访问互联网的设备**（本地电脑或手机）用于设置 Webhook

### PythonAnywhere 网络限制说明

PythonAnywhere 对外部 API 访问有限制，这意味着：

- ✅ **机器人可以正常运行**：Telegram 会主动推送消息到您的服务器（Webhook 模式）
- ❌ **无法在 PythonAnywhere 上设置 Webhook**：需要在本地电脑执行设置命令
- ✅ **部署后机器人工作正常**：一旦 Webhook 设置完成，机器人接收和发送消息都没有问题

**简而言之**：部署在 PythonAnywhere，但设置 Webhook 需要在本地完成。

---

## 部署步骤

### 第一步：上传代码

#### 方式 1：通过 Git（推荐）

1. 打开 PythonAnywhere Dashboard
2. 进入 **Consoles** → 打开 **Bash** 控制台
3. 克隆项目：

```bash
cd ~
git clone https://github.com/redtidev1918/TelePost.git
cd TelePost
```

#### 方式 2：手动上传

1. 将项目打包为 zip 文件
2. 在 PythonAnywhere 的 **Files** 页面上传
3. 在 Bash 控制台解压：

```bash
cd ~
unzip TelePost.zip
cd TelePost
```

---

### 第二步：安装依赖

在 Bash 控制台执行：

```bash
# 确保使用 Python 3.9 或更高版本
python3.9 --version

# 安装依赖
pip3.9 install --user -r requirements.txt
```

**注意事项**：
- PythonAnywhere 默认使用系统 Python，使用 `--user` 安装到用户目录
- 如果需要特定 Python 版本，在创建 Web App 时选择

---

### 第三步：配置项目

#### 1. 创建配置文件

```bash
cp config.ini.example config.ini
nano config.ini  # 或使用 vi 编辑
```

#### 2. 基本配置

编辑 `config.ini`，填入以下内容：

```ini
[BOT]
# 从 @BotFather 获取
TOKEN = your_bot_token_here

# 频道 ID 或用户名
CHANNEL_ID = @your_channel

# 管理员 User ID
OWNER_ID = 123456789

# 重要：设置为 WEBHOOK 模式
RUN_MODE = WEBHOOK

[WEBHOOK]
# 替换为你的 PythonAnywhere 用户名
# 格式: https://你的用户名.pythonanywhere.com
URL = https://yourusername.pythonanywhere.com

# 端口：PythonAnywhere 不需要指定，但保留此配置
PORT = 8080

# Webhook 路径
PATH = /webhook

# Secret Token（留空自动生成）
SECRET_TOKEN = 

[SEARCH]
# 推荐使用 simple 模式以节省内存
ANALYZER = simple

[DB]
# 内存优化配置
CACHE_SIZE_KB = 1024
```

**关键配置说明**：
- `RUN_MODE = WEBHOOK`：必须设置为 Webhook 模式
- `WEBHOOK_URL`：必须是 `https://你的用户名.pythonanywhere.com`
- `ANALYZER = simple`：在内存受限环境下使用轻量级分词器

---

### 第四步：准备 WSGI 应用文件

项目已包含 PythonAnywhere 专用的 WSGI 文件，只需简单配置：

```bash
cd ~/TelePost
nano pythonanywhere_wsgi.py
```

修改文件开头的用户名：

```python
# 重要：替换为你的 PythonAnywhere 用户名
USERNAME = 'yourusername'  # 修改这里！例如: USERNAME = 'john123'
```

保存后退出（Ctrl+X → Y → Enter）。

**说明**：
- 这个文件已经配置好所有必要的初始化逻辑
- 支持 `/webhook` 端点接收 Telegram 消息
- 支持 `/health` 端点进行健康检查
- 自动处理数据库初始化和处理器注册

---

### 第五步：配置 Web App

1. **进入 Web 页面**
   - 在 PythonAnywhere Dashboard 点击 **Web**

2. **创建新的 Web App**
   - 点击 **Add a new web app**
   - 选择 **Manual configuration**
   - 选择 **Python 3.9**（或更高版本）

3. **配置 WSGI 文件**
   - 在 Web App 配置页面，找到 **Code** 部分
   - 点击 **WSGI configuration file** 链接（通常是 `/var/www/yourusername_pythonanywhere_com_wsgi.py`）
   - 删除所有内容，替换为以下代码：

```python
# 重要：将下面的 'yourusername' 替换为你的实际用户名 ⭐
import sys
import os

# 项目路径
project_home = '/home/yourusername/TelePost'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

os.chdir(project_home)

# 导入 PythonAnywhere WSGI 应用
from pythonanywhere_wsgi import application
```

4. **配置虚拟环境（可选）**
   - 如果使用虚拟环境，在 **Virtualenv** 部分设置路径
   - 建议使用系统 Python + `--user` 安装更简单

5. **保存并重新加载**
   - 点击页面顶部的 **Reload** 按钮

---

### 第六步：设置 Webhook

> **⚠️ 重要提示：网络限制**
> 
> PythonAnywhere 限制了对外部 API 的访问，**无法在 PythonAnywhere 服务器上**直接执行 `curl` 命令访问 Telegram API。
> 
> **解决方法**：
> - ✅ **推荐**：在**您的本地电脑**执行以下命令（不是在 PythonAnywhere 上）
> - ✅ 或使用任何可以访问互联网的设备执行
> - ⚠️ 如果在 PythonAnywhere 上执行会看到 `HTTP code 503 from proxy` 错误

**在您的本地电脑**执行以下命令设置 Webhook：

```bash
# 注意：将 YOUR_BOT_TOKEN 和 yourusername 替换为实际值，不要保留 <> 符号
curl -X POST "https://api.telegram.org/botYOUR_BOT_TOKEN/setWebhook" \
  -d "url=https://yourusername.pythonanywhere.com/webhook" \
  -d "max_connections=40"
```

**示例**（假设 Token 是 `123456:ABC-DEF`，用户名是 `john`）：
```bash
curl -X POST "https://api.telegram.org/bot123456:ABC-DEF/setWebhook" \
  -d "url=https://john.pythonanywhere.com/webhook" \
  -d "max_connections=40"
```

**验证 Webhook 设置**（也在本地执行）：

```bash
curl "https://api.telegram.org/botYOUR_BOT_TOKEN/getWebhookInfo"
```

应该看到类似输出：

```json
{
  "ok": true,
  "result": {
    "url": "https://yourusername.pythonanywhere.com/webhook",
    "has_custom_certificate": false,
    "pending_update_count": 0,
    "max_connections": 40
  }
}
```

---

## 验证部署

### 1. 检查 Web App 状态

在 PythonAnywhere Web 页面：
- 确保 Web App 显示为 **Enabled**
- 状态显示为绿色勾号 ✅

### 2. 检查日志

查看错误日志：
- 在 Web App 配置页面，点击 **Log files** 部分的 **error log** 链接
- 检查是否有错误信息

### 3. 测试健康检查

```bash
curl https://yourusername.pythonanywhere.com/health
# 应返回: OK
```

### 4. 测试机器人

向你的 Telegram 机器人发送消息：
- 发送 `/start` 命令
- 应该立即收到回复（< 1 秒）

---

## 常见问题解决

### 问题 1：设置 Webhook 时出现 503 错误

**症状**：在 PythonAnywhere 上执行 `curl` 命令时看到：
```
curl: (56) Received HTTP code 503 from proxy after CONNECT
```

**原因**：PythonAnywhere 限制了对外部 API 的访问。

**解决方法**：

✅ **在本地电脑执行命令**（不是在 PythonAnywhere 上）：
```bash
curl -X POST "https://api.telegram.org/botYOUR_BOT_TOKEN/setWebhook" \
  -d "url=https://yourusername.pythonanywhere.com/webhook" \
  -d "max_connections=40"
```

参见上文"[第六步：设置 Webhook](#第六步设置-webhook)"的详细说明。

### 问题 2：机器人无响应

**可能原因**：
1. Webhook 未正确设置
2. WSGI 配置错误
3. 依赖未完全安装

**解决方法**：

```bash
# 1. 检查 Webhook 状态（将 YOUR_BOT_TOKEN 替换为实际 Token）
curl "https://api.telegram.org/botYOUR_BOT_TOKEN/getWebhookInfo"

# 2. 查看错误日志
# 在 Web 页面查看 error.log

# 3. 重新安装依赖
pip3.9 install --user --force-reinstall -r requirements.txt

# 4. 重新加载 Web App
# 在 Web 页面点击 Reload 按钮
```

### 问题 3：导入模块失败

**症状**：日志显示 `ModuleNotFoundError`

**解决方法**：

```bash
# 确认项目路径正确
ls -la ~/TelePost/

# 检查 WSGI 文件中的路径
nano /var/www/yourusername_pythonanywhere_com_wsgi.py

# 确保路径正确：
# project_home = '/home/yourusername/TelePost'
```

### 问题 4：数据库权限错误

**症状**：日志显示数据库无法创建或写入

**解决方法**：

```bash
# 确保 data 目录存在且有写权限
cd ~/TelePost
mkdir -p data
chmod 755 data

# 检查数据库文件权限
ls -la data/
```

### 问题 5：内存不足

**症状**：应用频繁重启或崩溃

**解决方法**：

编辑 `config.ini`，启用内存优化：

```ini
[SEARCH]
# 使用轻量级分词器
ANALYZER = simple

[DB]
# 降低缓存大小
CACHE_SIZE_KB = 512
```

然后重新加载 Web App。

### 问题 6：Webhook URL 不匹配

**症状**：`getWebhookInfo` 显示的 URL 与配置不一致

**解决方法**（⚠️ 在本地电脑执行）：

```bash
# 删除旧的 Webhook（将 YOUR_BOT_TOKEN 替换为实际 Token）
curl -X POST "https://api.telegram.org/botYOUR_BOT_TOKEN/deleteWebhook"

# 重新设置正确的 Webhook
curl -X POST "https://api.telegram.org/botYOUR_BOT_TOKEN/setWebhook" \
  -d "url=https://yourusername.pythonanywhere.com/webhook"
```

然后在 PythonAnywhere Web 页面重新加载 Web App。

---

## 性能优化建议

### 1. 内存优化

PythonAnywhere 免费账号内存限制较严格，付费账号一般为 512MB-3GB：

```ini
# config.ini 优化配置
[SEARCH]
ANALYZER = simple          # 节省 ~140MB
HIGHLIGHT = false          # 关闭高亮节省内存

[DB]
CACHE_SIZE_KB = 1024       # 适度缓存
```

### 2. 日志管理

定期清理日志文件：

```bash
# 创建日志清理脚本
cat > ~/TelePost/cleanup_logs.sh << 'EOF'
#!/bin/bash
cd ~/TelePost
find logs/ -name "*.log" -mtime +7 -delete
EOF

chmod +x cleanup_logs.sh

# 添加到 crontab（每周执行）
# 在 PythonAnywhere 的 Tasks 页面添加定时任务
```

### 3. 数据库维护

定期优化数据库：

```bash
# 在 Bash 控制台运行
cd ~/TelePost
python3.9 optimize_database.py
```

---

## 更新代码

当项目有新版本时：

```bash
# 1. 进入项目目录
cd ~/TelePost

# 2. 备份配置
cp config.ini config.ini.backup

# 3. 拉取最新代码
git pull origin main

# 4. 更新依赖
pip3.9 install --user -r requirements.txt

# 5. 重新加载 Web App
# 在 Web 页面点击 Reload 按钮
```

---

## 监控和维护

### 1. 定期检查

建议每周检查：
- Web App 状态（是否运行正常）
- 错误日志（是否有异常）
- Webhook 状态（是否正常接收消息）

### 2. 备份数据

定期备份数据库：

```bash
# 创建备份
cp ~/TelePost/data/submissions.db ~/backups/submissions_$(date +%Y%m%d).db

# 下载备份到本地
# 在 Files 页面下载备份文件
```

### 3. 性能监控

查看 Web App 的访问统计：
- 在 Web 页面的 **Access log** 查看请求记录
- 监控响应时间和错误率

---

## 相关文档

- [主文档 - README.md](../README.md)
- [Webhook 模式完整指南](WEBHOOK_MODE.md)
- [部署指南 - DEPLOYMENT.md](../DEPLOYMENT.md)
- [内存优化指南 - docs/PERFORMANCE.md](../docs/PERFORMANCE.md)

---

## 获取帮助

如遇到问题：

1. **检查文档**：先查看本指南和相关文档
2. **查看日志**：PythonAnywhere 的 error.log 通常包含详细错误信息
3. **搜索问题**：在 PythonAnywhere 论坛搜索类似问题
4. **提交 Issue**：在 [GitHub Issues](https://github.com/redtidev1918/TelePost/issues) 提问

---

**最后更新**：2025-12-02  
**适用版本**：TelePost.1+  
**测试账号**：PythonAnywhere Hacker Plan (512MB 内存)

**部署成功标志**：
- ✅ Web App 状态为 Enabled
- ✅ 健康检查返回 OK
- ✅ Webhook 信息正确
- ✅ 机器人响应正常（< 1 秒）

> 2026-09 更正：环境变量统一为 `TOKEN`（兼容 `BOT_TOKEN` / `TELEGRAM_BOT_TOKEN`）；不再用临时转发探测频道帖子统计。
