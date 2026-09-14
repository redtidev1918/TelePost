# Telegram Mini App（TelePost 小程序）部署与架构

Mini App 是 TelePost 的**可选增强层**：它是纯 Presentation/UI Adapter，不拥有任何
业务状态。所有 mutation 都进入与 Telegram Bot 完全相同的
`domain → application → storage` 链路（同一个状态机、同一套幂等、同一个审计系统）。

```text
Telegram
  ├── Bot      （quick actions / notifications / fallback）
  └── Mini App （rich submission + moderation UI）
          │
          └── TelePost HTTP API (/api/v1)   ← 唯一业务入口
                │
        ReviewQueueService / ReviewService / RefetchService
```

## 架构不变量（写入 AGENTS，勿违反）

1. Mini App 是 presentation adapter；业务状态只在 TelePost domain/application/storage。
2. Bot 与 Mini App 共用同一组 command/service；禁止复制业务逻辑到前端或后端第二套。
3. 浏览器永不接触 Bot token、长效 TelePost admin token、PixivFlow service secret。
4. 前端从 `@telegram-apps/sdk` 读取原始 launch `initData`（WebApp bridge 仅兼容后备）；
   服务器用 `init-data-py` 验证，绝不信任 `initDataUnsafe` 身份。
5. 授权只认服务器（RBAC：submitter / reviewer / admin，reviewer 身份复用
   `ADMIN_IDS`/`OWNER_ID`，单一来源）。
6. Mini App deep link（`startapp=review_123`）只表达导航意图，不构成授权。
7. TelePost 仍是后端 SSOT；Mini App 不是第二个 backend。

## 配置

| 变量 | 说明 | 示例 |
| --- | --- | --- |
| `MINIAPP_ENABLED` | 是否启用 Mini App surface（不影响 Bot） | `false`（默认关闭） |
| `MINIAPP_SESSION_SECRET` | Mini App session 签名密钥（≥32 字符，独立随机） | 生产 secret |
| `MINIAPP_SESSION_TTL` | session 生命周期秒数（默认 1800，上限 43200） | `1800` |
| `WEBAPP_BUILD_VERSION` | 可选：健康检查里暴露的 webapp 构建标识 | `1.2.0` |

`MINIAPP_ENABLED=false` 只关闭 Mini App surface；Bot 与 API 完全不受影响，
这是上线安全开关（§152-§153）。

## 构建

```bash
cd webapp
npm install
npm run typecheck && npm run lint && npm test
npm run build        # → webapp/dist/
```

`npm run generate:api` 从 `api/openapi.yaml` 重新生成 TypeScript 类型
（`webapp/src/api/generated/schema.d.ts`，作为 prebuild 钩子运行）。

## 部署（同域托管，§68-§69）

镜像构建 `webapp/dist/`，TelePost 在同域 `/app/` 提供静态资源并通过
`/api/botN/v1/` 路由各 Bot API。

1. 构建 `webapp/dist`。
2. 确认镜像包含 dist，`/app/` 与 `/api/botN/v1/health` 可访问。
3. 配置 `MINIAPP_ENABLED=true` + `MINIAPP_SESSION_SECRET` + `MINIAPP_SESSION_TTL`。
4. 分别配置两个 Bot 的私聊 Menu Button：bot1 用 `/app/?bot=bot1`，
   bot2 用 `/app/?bot=bot2`；可复用部署仓库的 `scripts/setup-miniapp-menu.sh`。
5. 健康检查 `/api/v1/health`（或新增字段）确认 `miniapp_enabled`。

静态资源 HTTP 200 只是传输检查；上线验收需从真实 Telegram 菜单打开，
确认 session、`/me`、首页、审核队列和一次审核操作。

### CSP（§71）

不要 `script-src *`。允许：

```text
default-src 'self';
script-src 'self';
style-src 'self' 'unsafe-inline';
img-src 'self' data:;
connect-src 'self';
```

### 安全要点

- session token 只存在于内存，浏览器刷新后重新 `POST /miniapp/session` 引导（§12, §108）。
- 审核 mutation 全部 `_review_auth`（server-side RBAC），普通用户 curl 获得 403（§14）。
- 投稿内容一律按不可信文本渲染（无 `dangerouslySetInnerHTML`，§52）。
- 外链仅允许 `http/https`（表单与展示两侧都过滤，§53）。
- 上传继续复用 TelePost 的 size/type/filename 校验与服务端 multipart 流（§54）。
- 所有破坏性操作审计 `surface=mini_app`，与 Bot 的 `surface=telegram_bot` 可区分（§56, §146）。

## 测试

- Python：`pytest tests/test_miniapp_*.py tests/test_refetch.py`（auth tamper、RBAC 矩阵、
  endpoint 契约、refetch 状态机）。
- WebApp：`cd webapp && npm test`（Vitest + Testing Library：AuthProvider、ReviewDetail mutation）。
- 完整回归：`pytest -q`（全量）+ `cd webapp && npm run typecheck && npm run lint && npm test && npm run build`。

## 已知边界

- 大文件/弱网二期再评估 Uppy+Tus resumable；当前 multipart + 幂等键已覆盖
  iOS/Android WebView 常规场景（§21-§23 评估结论：50MB 单文件 / 500MB 累计上限内
  不需要 Tus）。
- 审核队列第一版用 15s polling，不引入 WebSocket/SSE（§37）。
- Attachment Menu 不在本轮上线前置（Bot API 限制），deep link 已覆盖直达入口（§171）。
