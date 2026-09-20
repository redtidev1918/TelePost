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

## 空间与导航（§mine-admin-split）

Mini App 只有**一个**工作空间：所有人都拥有用户三件套（首页 / 投稿 / 我的投稿），
reviewer / admin 额外持有审核队列，admin 再额外持有管理面板。路由与底部导航单一来源是
`webapp/src/app/App.tsx` 的 `navigationForSpace(isReviewer, isAdmin)`：

- **普通用户**（submitter）：Tabbar 为「首页 / 投稿 / 我的投稿」，路由只有投稿三件套与
  各自的详情/编辑历史；不注册审核路径。
- **reviewer / admin**：在用户三件套之上额外显示「审核队列」，并注册
  `/review`、`/review/:id`、`/review/:id/edit`；同时保留了投稿与「我的投稿」全部能力，
  与后端 RBAC 一致（`roles` 始终包含 `submitter`）。
- **admin**：再额外显示「管理」面板（`/admin`），四个分区对应 `/api/v1/admin/*`：
  运行状态（`GET /admin/status`，30 秒轮询）、运行策略（`PATCH /admin/policy`，仅
  `chat_review` / `show_submitter` 两个开关——`miniapp_review` 刻意只留 Bot 侧，避免面板
  把自己锁在外面）、角色管理（`/admin/roles` 持久化绑定增删，env 引导的管理员不在此列）、
  黑名单（`/admin/blacklist` 增删）。所有变更均由服务端审计；移除操作可逆（重新授予 /
  解除拉黑），故无二次确认弹窗。
- 服务端 RBAC 仍是唯一权威；Tabbar/路由只是把非授权表面藏起来，不能替代服务端校验。
  新路由必须走 `navigationForSpace`，禁止各页面自拼底部导航。

### 预览与发送边界（§preview-ux）

- **Mini App 用户空间**：附件与文字先在本端构建附件预览（Uppy 本地媒体预览）。caption
  由服务端真实频道 formatter 生成并原样渲染，预览页提供「返回修改」与「提交审核」两个
  出口——用户确认前可任意增删/改动附件与文案，不产生任何侧写（不上传、不发 Telegram 消息）。
- **私聊预览**：`/done_media` 首次进入时把已选素材以真实 Telegram 媒体发送一次，随后用
  **同一个频道 caption formatter** 发送文字控制消息（直发按钮「确认发布」、需审核按钮
  「提交审核」）；再次刷新不重复发送媒体。
- 两个入口是两个独立 Attachment Preview 实现，caption 使用同一真相源；业务/审核走同一条
  `submissions` / `pending_reviews` 管道。禁止把 Mini App 状态搬进聊天，也禁止在前端另造
  第二套 caption。

## 配置

| 变量 | 说明 | 示例 |
| --- | --- | --- |
| `MINIAPP_ENABLED` | 是否启用 Mini App surface（不影响 Bot） | `false`（默认关闭） |
| `MINIAPP_SESSION_SECRET` | Mini App session 签名密钥（≥32 字符，独立随机） | 生产 secret |
| `MINIAPP_SESSION_TTL` | session 生命周期秒数（默认 1800，上限 43200） | `1800` |
| `WEBAPP_BUILD_VERSION` | 可选：健康检查里暴露的 webapp 构建标识 | `1.2.0` |

`MINIAPP_ENABLED=false` 只关闭 Mini App surface；Bot 与 API 完全不受影响，
这是上线安全开关（§152-§153）。
当前投稿界面按审核流程工作；`MINIAPP_REVIEW_REQUIRED` 内置默认即为 `true`，且与 API 审核开关相互独立。

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
4. 私聊主入口：配置可用时左侧 Telegram 菜单按钮直接打开 Mini App；
   `/start` 的 Reply Keyboard 仍保留 Web App 按钮。命令通过输入 `/` 使用。
5. 频道 footer：默认 `?startapp=miniapp` 直接打开 Main Mini App。若 BotFather
   配置了 Direct Mini App short name，并设置 `MINIAPP_SHORT_NAME`，可使用
   更专用的直达链接。
6. 健康检查 `/api/v1/health`（或新增字段）确认 `miniapp_enabled`。

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
- Visual：`cd webapp && npx playwright test e2e/visual.spec.ts --project=mobile` 保留五条主路由的
  截图契约；语义 E2E 仍由 `miniapp.spec.ts` 负责。
- 完整回归：`pytest -q`（全量）+ `cd webapp && npm run typecheck && npm run lint && npm test && npm run build`。

## 已知边界

- 大文件/弱网二期再评估 Uppy+Tus resumable；当前 multipart + 幂等键已覆盖
  iOS/Android WebView 常规场景（§21-§23 评估结论：50MB 单文件 / 500MB 累计上限内
  不需要 Tus）。
- 审核队列第一版用 15s polling，不引入 WebSocket/SSE（§37）。
- Attachment Menu 不在本轮上线前置（Bot API 限制），deep link 已覆盖直达入口（§171）。

## Framework ownership（本轮硬约束）

- Telegram 平台能力（viewport/safe-area/launch data）来自 `@telegram-apps/sdk`；
  TelegramUI 负责通用视觉组件（Tabbar/Section/Cell/Badge…）。
- 附件状态（选择/限制/去重/移除/进度/错误）由 Uppy 通过官方 `@uppy/react`
  integration 负责；TelePost 只保留「一次 multipart + 稳定幂等键」的提交 adapter。
  禁止重回 imperative Dashboard 挂载或自写文件管理器。
- 底部导航不覆盖内容：`.page` 是唯一滚动区，预留运行时测量的 Tabbar 高度
  （`--app-tabbar-reserve`）+ SDK safe area；不硬编码设备偏移。
- `我的投稿` = human-owned logical submission：一个 review chain 一条（refetch
  代际折叠）、`/me/submissions/{id}` 为用户安全详情；服务/自动化稿永不出现。
- 软删除历史（`DELETE /me/submissions/{id}`）：仅删除自己的已终态投稿历史，
  从列表/详情隐藏，不触碰审核队列、已发布频道消息与审计；
  `preparing / pending / publishing` 不允许删除。
