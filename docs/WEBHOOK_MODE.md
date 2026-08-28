# Webhook 模式使用指南

TelePost 支持两种运行模式：**Polling（轮询）** 和 **Webhook**。

## 📊 两种模式对比

| 特性 | Polling 模式 | Webhook 模式 |
|------|-------------|-------------|
| **工作原理** | 机器人主动拉取消息 | Telegram 推送消息到服务器 |
| **网络消耗** | 较高（持续轮询） | 较低（按需推送） |
| **响应延迟** | 1-3 秒 | < 1 秒 |
| **适用场景** | 开发环境、小型部署 | 生产环境、高并发 |
| **部署要求** | 无特殊要求 | 需要公网 HTTPS 域名 |
| **推荐环境** | 本地开发、测试 | 云服务器、PaaS 平台 |

---

## 配置方法

### 1. 编辑配置文件

编辑 `config.ini`：

```ini
[BOT]
# 其他配置...

# 运行模式: POLLING (轮询) | WEBHOOK (Webhook)
RUN_MODE = WEBHOOK

[WEBHOOK]
# 外部访问地址（必填，例如: https://your-domain.com）
# 注意：必须是 HTTPS 且有效的域名
URL = https://your-domain.com

# Webhook 监听端口（默认 8080）
PORT = 8080

# Webhook 路径（默认 /webhook）
PATH = /webhook

# Webhook Secret Token（可选，用于验证请求来源）
# 留空则自动生成随机 token
SECRET_TOKEN = 
```

### 2. 环境变量方式（推荐）

**通用方式**（适用于任何部署环境）：

```bash
export RUN_MODE=WEBHOOK
export WEBHOOK_URL=https://your-domain.com
export WEBHOOK_PORT=8080
export WEBHOOK_PATH=/webhook
export WEBHOOK_SECRET_TOKEN=your_random_token  # 可选
```

**Docker/Docker Compose**：

在 `docker-compose.yml` 中添加：

```yaml
services:
  telepost:
    environment:
      - RUN_MODE=WEBHOOK
      - WEBHOOK_URL=https://your-domain.com
      - WEBHOOK_PORT=8080
      - WEBHOOK_PATH=/webhook
      - WEBHOOK_SECRET_TOKEN=${WEBHOOK_SECRET_TOKEN}
```

**Systemd 服务**：

编辑 `/etc/systemd/system/telepost.service`：

```ini
[Service]
Environment="RUN_MODE=WEBHOOK"
Environment="WEBHOOK_URL=https://your-domain.com"
Environment="WEBHOOK_PORT=8080"
```

**PaaS 平台设置方式**：

通过各平台的环境变量管理界面或 CLI 工具设置：
- 在 Dashboard/控制面板添加环境变量
- 或使用平台的 CLI 命令（参考各平台文档）

---

## 快速开始

### 方式 1：配置文件模式（推荐本地/VPS）

1. **编辑配置文件** `config.ini`：
```ini
[BOT]
RUN_MODE = WEBHOOK

[WEBHOOK]
URL = https://your-domain.com
PORT = 8080
PATH = /webhook
```

2. **配置反向代理**（Nginx 示例）：
```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location /webhook {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

3. **启动应用**：
```bash
./restart.sh
```

### 方式 2：Docker 部署

1. **配置 docker-compose.yml**：
```yaml
version: '3.8'
services:
  telepost:
    build: .
    ports:
      - "8080:8080"
    environment:
      - RUN_MODE=WEBHOOK
      - WEBHOOK_URL=https://your-domain.com
    volumes:
      - ./data:/app/data
      - ./config.ini:/app/config.ini
```

2. **启动容器**：
```bash
docker-compose up -d
```

### 方式 3：PaaS 平台部署

**通用步骤**（适用于大多数 PaaS 平台）:

1. **设置环境变量**：
   ```
   RUN_MODE = WEBHOOK
   WEBHOOK_URL = https://your-app-domain.com
   ```

2. **配置方式**：
   - **Web 界面**: 进入应用设置 → 环境变量 → 添加配置
   - **CLI 工具**: 使用平台提供的命令行工具
   - **配置文件**: 部分平台支持配置文件管理

3. **重启应用**：
   - 大多数平台修改环境变量后自动重启
   - 或手动触发重启/重新部署

4. **验证部署**：
   ```bash
   # 测试健康检查
   curl https://your-app-domain.com/health
   
   # 检查 Webhook 状态
   curl https://api.telegram.org/bot<TOKEN>/getWebhookInfo
   ```

**常见平台环境变量位置**:
- **Heroku**: Settings → Config Vars
- **Railway**: Variables → New Variable
- **Render**: Environment → Add Environment Variable
- **Vercel**: Settings → Environment Variables
- **其他平台**: 查看平台设置或文档中的环境变量配置

**启动成功标志**：

查看日志，应该看到类似输出：
```
📡 启动 Webhook 模式...
Webhook 模式已启动
   监听地址: 0.0.0.0:8080/webhook
   外部地址: https://your-domain.com/webhook
   Secret Token: 已设置
```

---

## 模式切换

### 从 Polling 切换到 Webhook

**方式 1：修改配置文件**
```bash
# 编辑 config.ini
nano config.ini
# 修改: RUN_MODE = WEBHOOK
# 添加: [WEBHOOK] 配置节

# 重启应用
./restart.sh
```

**方式 2：环境变量**
```bash
# 设置环境变量
export RUN_MODE=WEBHOOK
export WEBHOOK_URL=https://your-domain.com

# 重启应用
./restart.sh
```

**方式 3：Docker**
```bash
# 修改 docker-compose.yml 或 .env
# 重新启动容器
docker-compose down && docker-compose up -d
```

**方式 4：PaaS 平台**
```bash
# 在平台控制面板设置环境变量
RUN_MODE=WEBHOOK
WEBHOOK_URL=https://your-domain.com

# 或使用平台 CLI（参考各平台文档）
# 示例：通用命令格式
platform-cli config:set RUN_MODE=WEBHOOK
platform-cli restart your-app
```

### 从 Webhook 切换到 Polling

**修改配置**：
```ini
[BOT]
RUN_MODE = POLLING
```

或环境变量：
```bash
export RUN_MODE=POLLING
```

然后重启应用即可。

**注意**：
- ✅ 切换模式时，旧的 Webhook 会自动删除
- ✅ 数据和配置不受影响
- ✅ 可随时切换，无需额外操作

---

## 多 bot 单机 Webhook（auto_stop 省钱形态）

设置 `BOT1_TOKEN`/`BOT2_TOKEN`/… 后，容器入口 `run.py` 会：

1. 为每个 bot 派生独立子进程，webhook 端口自动错开（8081、8082、…）
2. 在 `WEBHOOK_PORT`（默认 8080）启动**按路径转发的路由**：
   `/webhook/bot1` → bot1 子进程，`/webhook/bot2` → bot2 子进程
3. 各 bot 以自己的 Secret Token 调用 `setWebhook`（Telegram 校验互不可见）

配合 `auto_stop_machines=true` + `min_machines_running=0`：
空闲自动停机（计算费 0），任何频道的来消息都会自动唤醒机器（约 1-2 秒延迟）。
代价：统计定时任务只在机器醒着的时候执行。

环境变量与数据隔离规则见 [CONFIGURATION.md](CONFIGURATION.md) 的「多 bot 模式」。

## 安全性

### Secret Token 验证

Webhook 模式支持 Secret Token 验证，防止恶意请求：

```bash
# 生成随机 token
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# 设置 token（通过配置文件或环境变量）
# 方式 1: config.ini
# [WEBHOOK]
# SECRET_TOKEN = your_generated_token

# 方式 2: 环境变量
export WEBHOOK_SECRET_TOKEN=your_generated_token

# 方式 3: PaaS 平台控制面板添加环境变量
```

如果不设置，系统会自动生成一个随机 token。

### HTTPS 要求

Telegram 要求 Webhook URL 必须是 HTTPS。

**获取 HTTPS 证书**:
- **PaaS 平台**: 通常自动提供 SSL 证书
- **VPS + Caddy**: 自动申请和续期 Let's Encrypt 证书
- **VPS + Nginx**: 使用 Certbot 获取 Let's Encrypt 免费证书
- **Cloudflare**: 免费 SSL/TLS 加密（适合任何服务器）

---

## ✅ 部署验证

**测试结果**: ✅ 在 256MB 内存环境下成功运行

```bash
# 已验证配置（PaaS 平台示例）
Memory: 256MB
Webhook URL: https://your-app.example.com/webhook
Health Check: OK
Status: 稳定运行
```

**关键优化**:
- ✅ 端口复用：Webhook 服务器同时处理 `/webhook` 和 `/health`
- ✅ 内存优化：使用 `SEARCH_ANALYZER=simple` 节省 ~140MB
- ✅ 无需额外端口：health.py 仅在 Polling 模式启动

**验证平台**:
- ✅ PaaS 平台（256MB）
- ✅ VPS 服务器（512MB+）
- ✅ Docker 容器（256MB+）

---

## 故障排查

### 1. Webhook 设置失败

**症状**：日志显示 "❌ Webhook 模式需要设置 WEBHOOK_URL"

**解决方法**：
```bash
# 检查环境变量（通过平台控制面板或 SSH）
env | grep WEBHOOK

# 确保设置了 WEBHOOK_URL
# 方式 1: 修改 config.ini
# 方式 2: 在平台控制面板添加环境变量
# 方式 3: 使用平台 CLI 设置
```

### 2. 收不到消息

**症状**：机器人启动正常，但不响应消息

**排查步骤**：

1. **检查 Webhook 状态**：
```bash
# 使用 Telegram Bot API 检查
curl "https://api.telegram.org/bot<YOUR_TOKEN>/getWebhookInfo"
```

应该返回：
```json
{
  "url": "https://your-domain.com/webhook",
  "has_custom_certificate": false,
  "pending_update_count": 0,
  "max_connections": 40
}
```

2. **检查应用日志**：
   - PaaS 平台：在控制面板查看日志
   - VPS：`tail -f /app/logs/app.log`
   - Docker：`docker logs container-name`

3. **测试健康检查**：
```bash
curl https://your-domain.com/health
# 应该返回: OK
```

### 3. 端口冲突

**症状**：日志显示端口已被占用

**解决方法**：

修改 `config.ini` 或环境变量，使用不同端口：
```ini
[WEBHOOK]
PORT = 8081
```

**注意**：如果使用配置文件部署，也需要更新相应端口：

```yaml
# Docker Compose
ports:
  - "8081:8081"
```

```nginx
# Nginx 反向代理
location /webhook {
    proxy_pass http://localhost:8081;
}
```

---

## 性能优化

### Webhook 模式的优势

1. **更低延迟**：消息到达即时处理，无需等待轮询周期
2. **节省资源**：无需持续发送轮询请求
3. **更好的并发**：适合高并发场景

### 推荐配置

**通用优化**（适用于所有平台）:
```ini
# config.ini
[BOT]
RUN_MODE = WEBHOOK

[WEBHOOK]
URL = https://your-domain.com

# 内存优化
[SEARCH]
ANALYZER = simple  # 节省 ~140MB

[DB]
CACHE_SIZE_KB = 1024  # 适度缓存
```

**PaaS 平台环境变量**:
```bash
RUN_MODE=WEBHOOK
WEBHOOK_URL=https://your-app.example.com
SEARCH_ANALYZER=simple
DB_CACHE_KB=1024
```

---

## 最佳实践

### 1. 生产环境

- 使用 **Webhook 模式**
- 设置 **Secret Token**
- 启用 HTTPS（Let's Encrypt 或 PaaS 自动）
- 配置健康检查
- 使用反向代理（Nginx/Caddy）

### 2. 开发环境

- 使用 **Polling 模式**
- 本地测试更方便
- 无需配置域名

### 3. 混合部署

- **生产服务器**: Webhook 模式（云平台/VPS）
- **开发/测试**: Polling 模式（本地）
- **通过配置轻松切换**: 无需修改代码

---

## 相关文档

- [主文档 - README.md](../README.md)
- [脚本使用指南 - docs/OPERATIONS.md](../docs/OPERATIONS.md)
- [内存优化指南 - docs/PERFORMANCE.md](../docs/PERFORMANCE.md)
- [配置示例 - config.ini.example](../config.ini.example)

---

## 常见问题

### Q: 我应该使用哪种模式？

**A**: 
- **本地开发/测试** → Polling 模式
- **生产环境（有公网 HTTPS 域名）** → Webhook 模式
- **VPS/云服务器/PaaS 平台** → Webhook 模式

### Q: 切换模式会影响数据吗？

**A**: 不会。两种模式只影响消息获取方式，不影响数据库和存储。

### Q: Webhook 模式需要额外的端口吗？

**A**: 不需要。Webhook 使用应用本身的端口（默认 8080），与健康检查共用。

### Q: 如何验证 Webhook 是否正常工作？

**A**: 
1. 查看日志确认启动成功
2. 向机器人发送消息测试
3. 使用 `getWebhookInfo` API 检查状态

---

## 技术细节

### Webhook 请求流程

```
Telegram 服务器
    ↓ HTTPS POST /webhook
你的服务器（云平台/VPS）
    ↓ 验证 Secret Token
aiohttp Webhook Server
    ↓ 解析 Update
Bot Application
    ↓ 调用 Handler
处理消息并响应
```

### 关键组件

- **telegram.ext.Updater**: 处理 Webhook 请求
- **aiohttp**: 提供 HTTP 服务器（内置在 python-telegram-bot）
- **Secret Token**: 验证请求来源（可选）

### 端口说明

- **8080**: 默认 Webhook 监听端口
- **/webhook**: 默认 Webhook 路径
- **/health**: 健康检查端点（共用）

---

**最后更新**: 2025-10-28  
**适用版本**: TelePost.1+


> 2026-08 更正：环境变量统一为 `TOKEN`（兼容 `BOT_TOKEN` / `TELEGRAM_BOT_TOKEN`）；帖子统计更新周期为每 2 小时。
