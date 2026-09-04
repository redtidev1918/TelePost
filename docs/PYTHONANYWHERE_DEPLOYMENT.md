# PythonAnywhere 部署状态

当前版本**不把 PythonAnywhere WSGI 作为受支持的生产部署方式**。

仓库中的 `pythonanywhere_wsgi.py` 是旧版实验适配，只处理基本 `/webhook` 与 `/health`，
没有覆盖当前 `run.py` 的完整生命周期、会话 persistence、多 Bot 父路由、HTTP API、
后台任务和优雅停机。旧文档把它描述成“已验证可用”是不准确的。

请改用：

- 有公网服务器：源码/systemd 或 Docker，见 [INSTALL.md](INSTALL.md)。
- 需要托管 Webhook 和自动休眠：Fly.io，见 [FLYIO_DEPLOYMENT.md](FLYIO_DEPLOYMENT.md)。
- 无公网 HTTPS：Polling 模式，见 [WEBHOOK_MODE.md](WEBHOOK_MODE.md)。

只有在有人补齐 WSGI/ASGI 生命周期适配、API 路由和集成测试后，才应恢复完整
PythonAnywhere 指南。不要照旧教程手工 `setWebhook` 后用于生产。
