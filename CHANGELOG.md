# 更新日志

所有重要的项目更改都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [Unreleased]

## [2.10.27] - 2026-09-02

### 清理（审计驱动）
- 移除 `main.py` 未使用的 `blacklist_filter` 导入，以及 4 个处理器中从未使用的
  `user_id` 局部变量赋值。仅清理死代码，零行为变更；419 测试全过。

## [2.10.26] - 2026-09-02

### 修复

- 审核群「🔄 重抓/换一张」按钮此前调用 `pixivflow scheduler run`——那是
  PixivFlow 调度**守护进程**的别名，进程永不退出，导致 TelePost 的 1500 秒
  子进程超时并报「重抓任务出错」。现改用 PixivFlow 新增的一次性命令
  `pixivflow run-once`（跑完全部启用计划即退出，行为与定时任务一致），
  重抓在下载完成后会正常结束并回执「✅ 重抓完成」。

## [2.10.25] - 2026-09-02

### 修复

- API 的 `title`/`note` 字段统一在 multipart 与 JSON 共用入口解析字面量
  `\\n`、`\\r\\n`，避免配置模板中的换行被原样显示；同时删除两个未使用的 UI/处理器残留，
  并移除 Python 标准库已提供的 `configparser` 生产依赖。
- 移除统计刷新和删帖检测中“把频道帖子临时转发给 OWNER 再删除”的探测方式，避免私聊中
  偶发闪现审核/频道内容；同时停止每个 Bot 的两项无效轮询，降低 Telegram API 请求和日志噪音。
- 热榜刷新仅重读本地统计。Telegram Bot API 无法无副作用获取任意频道帖的浏览、转发统计，
  也不会推送频道删帖事件；TelePost 自身删帖仍会同步软删除，外部删帖需用 `/delete_posts` 同步。
- 修复混合聊天投稿只有 1 个文档时错误调用 `sendMediaGroup`、GIF/音频被放入不受支持的
  相册、11/21/31 张媒体的单张尾组导致发布失败，以及长说明单独发送失败后内容被静默
  丢弃；非相册媒体与尾组改为回复链发送，已知的中途失败会回滚已发送片段。
- 索引重建失败时会删除残缺目录并恢复旧索引，不再因新目录已创建而跳过备份恢复。
- SIGTERM/SIGINT 改由主协程顺序完成清理，不再用 `loop.stop()` 中断 `asyncio.run()`；
  OWNER 通知发生不确定网络错误时不再盲目补发，避免可能的重复私聊。

## [2.10.24] - 2026-09-01

### 新增

- 新增仅 `OWNER_ID` 可用的 Telegram `/botconfig` 面板，可修改当前 Bot 的投稿频道、
  审核群、API/聊天审核开关和频道署名总开关；运行策略原子保存在各 Bot 持久目录，
  应用后只重载当前 Bot 子进程，不重启另一个 Bot、PixivFlow 或 Fly Machine。

### 安全与稳定性

- 频道和审核群会通过 Telegram 校验类型、Bot 管理员身份及频道发帖权限；存在待审核
  投稿时拒绝切换频道、审核群或恢复部署配置，避免旧稿误发到新频道。
- Bot Token、Owner、管理员列表和 Webhook 密钥不进入聊天配置面板；
  `/botconfig reset` 可删除运行时覆盖并恢复部署环境变量。

### 文档与测试

- 更新配置、命令和运维文档，说明双 Bot 单进程重载语义与完整命令用法。
- 新增运行策略白名单、原子写入、权限边界、链接解析和多 Bot 隔离回归测试；
  完整测试 413 项通过、1 项按预期跳过。

## [2.10.23] - 2026-09-01

### 修复

- `/health` 不再因 Node.js 24 把 Linux 线程名显示为 `MainThread` 而漏报
  PixivFlow RSS；采集器会用进程 cmdline 识别 Node，512 MiB 内存余量重新可信。

### 测试

- 新增 Node 进程名变化的 `/proc` RSS 采集回归测试。

## [2.10.22] - 2026-09-01

### 修复
- 🐛 **搜索/热榜翻页按钮失效**：`/search` 与 `/hot` 多页结果的 ⬅️/➡️ 翻页按钮
  此前接到一个未实现的空壳处理函数，点击只弹“跳转到第 N 页”却不刷新内容。
  现已按存储的查询上下文重新渲染对应页（复用既有 `render_search_page` /
  `get_hot_posts`），上下文过期时优雅降级为提示。

### 维护
- 🧹 全仓死代码清理（零行为变化，pyflakes/pytest 双重把关）：删除从未注册的
  错误处理函数与 `if False:` 调试块、4 个零调用的 `*_safe` 包装、重复的
  `calculate_heat_score`、不可达的 TEXT/IMAGE/EXTRA 会话状态及其 no-op
  lambda 占位、一批未使用导入；`health.py` 作为对外保留的旧轮询入口保留。
- 📝 文档：API.md 补充审核群「🔇 遮罩开关」「🔄 重抓/换一张」按钮说明与审核
  记录状态机（含 `expired`/`deleted`）；修正路由限额注释（50 文件 / 500 MiB）；
  移除 COMMANDS.md 中已不存在的 `/done_img` 旧图片流程。

## [2.10.21] - 2026-09-01

### 安全与维护

- 联合运行镜像改用 Node.js 24 LTS，并把内置 PixivFlow 从 2.8.0
  更新到 2.10.21；生产依赖不再建立在已结束安全维护的 Node.js 20 上。
- 发布工作流升级到当前 `actions/checkout` 主版本。

### 文档

- Fly 双 Bot 模板与 Docker 默认构建参数统一固定 PixivFlow 2.10.21，
  避免直接使用仓库 Dockerfile 时静默部署旧版调度器。

## [2.10.20] - 2026-09-01

### 改进

- `POST /api/v1/notifications` 的幂等键由进程内存改为 SQLite 原子占位：
  Bot 重启后仍可识别重复通知，发送失败会释放占位供 PixivFlow outbox
  重试；异常中断的 pending 占位 5 分钟后可恢复。
- 通知幂等记录与已决审核记录共用 `REVIEW_RETENTION_DAYS`
  的定期清理周期，避免 SQLite 表无限增长。
- 修正 `GET /api/v1/me` 查询限额时使用 token ID，而投稿计数使用
  Telegram user ID 的键不一致问题。

### 测试

- 新增 API 通知跨重启幂等、失败释放与过期清理回归测试。

## [2.10.19] - 2026-08-31

### 新增
- 🔇 **审核群可决定频道遮罩**：审核键盘新增"遮罩：开/关"按钮，审核员可在发布前
  翻转该投稿的 `has_spoiler`，初始值沿用投稿者设置；发布以数据库当前值为准。
- 🔄 **审核群"重抓/换一张"**：对 Pixiv 自动投稿（HTTP API + Pixiv 链接）新增
  "重抓/换一张"按钮，审核员点击后在后台触发一次 `pixivflow scheduler run`，
  已下载作品自动跳过并选取下一张，新稿作为新审核记录进群；不影响已发布的旧稿。
  仅管理员可用，PixivFlow 未启用时按钮不触发。

## [2.10.18] - 2026-08-31

### 新增

- 新增鉴权端点 `POST /api/v1/notifications`，供 PixivFlow 等自动化在没有候选时
  向当前 Bot 的 `REVIEW_CHAT_ID` 发送纯文本运维通知，不创建空投稿或频道消息。
- 支持最长 240 字符的 `idempotency_key`；同一进程 24 小时内的网络重试返回
  `duplicate`，避免审核群重复提示。通知复用投稿 Bearer Token，Bot Token 不外泄。

## [2.10.17] - 2026-08-31

### 安全

- Webhook Secret Token 校验改用恒定时间比较 `hmac.compare_digest`，替代普通
  `!=` 逐字节比较，避免通过响应耗时差异逐字节推断 token（时序侧信道）。
  错误 / 缺失 / 前缀相似的 token 仍一律返回 401；新增对应鉴权测试。

## [2.10.16] - 2026-08-31

### 修复

- 修正 JobQueue 每天 03:00 执行同步日志清理回调时产生的
  `TypeError: object NoneType can't be used in 'await' expression`；日志清理与
  04:00 PixivFlow 维护现均为真正的异步回调，阻塞文件/子进程操作移到工作线程。
- 多 Bot 模式只由主 Bot 注册日志清理和 PixivFlow 维护，避免同一目录被重复清理；
  每个 Bot 自己的审核队列清理仍保持独立运行。
- 03:00/04:00 维护时间显式使用 `TZ`（默认 `Asia/Shanghai`）。此前无时区的时间被
  JobQueue 按 UTC 解释，东八区实际会延后到 11:00/12:00；无效 `TZ` 现安全回退 UTC。

## [2.10.15] - 2026-08-31

### 修复

- 频道消息软删除（`published_posts.is_deleted=1`）时，同步将对应审核记录从
  `published` 更新为 `deleted`；数据库初始化会自动回填旧版已经错位的历史数据。
  `published` 现在只表示当前仍未标记删除的已发布审核结果。
- `/health` 的 `storage.review_queue.by_bot` 增加 `deleted` 历史终态计数，
  避免运维时把已删除稿误判为当前在线发布。

## [2.10.14] - 2026-08-31

### 改进

- 新增 `PENDING_REVIEW_RETENTION_DAYS`（默认 `0`，不自动过期）与
  `PENDING_REVIEW_CLEANUP_BATCH_SIZE`（默认 `100`）：部署者可让长期无人处理的
  `pending` 投稿自动变为 `expired`，同步删除审核群中的媒体、文档与控制消息。
- 过期不会直接抹掉数据库审计记录；`expired` 记录与其他终态记录一样，之后再由
  `REVIEW_RETENTION_DAYS`（默认 30 天）清理。原始上传文件在进入审核群后仍立即删除，
  因此等待审核不会占用常驻内存或本地媒体存储。
- Telegram Bot API 只保证删除发送后 48 小时内的消息；希望自动清理审核群时建议将
  待审核保留期设为 `1` 天。更长保留期仍会正确过期数据库记录，但旧群消息可能需手动删除。

## [2.10.13] - 2026-08-30

### 修复

- 多 Bot 模式只由 Bot1 注册每日 PixivFlow 全局维护任务；其余 Bot 明确跳过，避免
  多个子进程在 04:00 同时清缓存和 `VACUUM` 同一个数据库。单 Bot 模式行为不变。

## [2.10.12] - 2026-08-30

### 修复

- **`api_uploads` 目录泄漏**：上传会话清理原来依赖 aiohttp 任务 done-callback，
  并非所有执行路径都会触发（实测多页大上传后残留 90MB 级孤儿目录）。
  现在新增后台清扫器：每小时删除超过 `UPLOAD_SESSION_MAX_AGE_SECONDS`
  （默认 1 小时）的孤儿会话目录，配合原有回调双保险，持久卷不再被慢速累积占满。

### 改进

- 定期清理扩展到 `pending_reviews`：已决（rejected/published/failed）且超过
  `REVIEW_RETENTION_DAYS`（默认 30 天）的审核记录自动删除；pending 记录绝不动。
- 每天 04:00 自动执行 `pixivflow maintain`（子进程）：清理日志/备份、VACUUM
  优化 SQLite，配合 `deleteAfterDelivery=false` 的缓存保留策略控制存储增长。

## [2.10.11] - 2026-08-30

### 改进

- 超大图片自动按文档发送（参考 t.me/Pixiv_bot 的传输策略）：Telegram 图片
  （含相册）单张上限 10 MiB，超过会被拒绝；现在本地暂存阶段对超过
  `PHOTO_MAX_BYTES`（默认 9.5 MiB）的页面自动改按 document 发送（document
  上限 50 MiB），大尺寸原图不再导致整份投稿进不了审核群。

## [2.10.10] - 2026-08-30

### 修复

- 审核群相册上传真正生效：此前相册里的本地文件 `InputFile` 未启用 `attach` 模式，
  python-telegram-bot 会把每个 `InputMedia` 的 `media` 字段丢弃（没有 `attach://` URI），
  Telegram 返回 `Can't parse inputmedia: media not found`，相册每次都会静默降级为
  逐张发送——多页作品虽然能进审核群，但一直是一张一张的消息而不是相册。
  现在 `attach=True` 保留 `attach://` 引用，5 张一组的小相册 + 组间回复链真正生效；
  降级逐张逻辑仍作为相册发送失败时的兜底保留。

## [2.10.9] - 2026-08-30

### 修复

- 幂等键语义修正：`idempotency_key` 只在审核仍处于 `pending`/`failed` 时去重；
  已被**拒绝或已发布**的旧记录不再阻断同一 key 的新投稿——例如 PixivFlow 定时任务
  次日又选中同一作品时，会删除旧记录与旧预览并创建全新的审核记录，
  而不是静默返回旧结果（此前会导致新投稿不进审核群）。

## [2.10.8] - 2026-08-30

### 改进

- 审核群相册预览改为「小相册 + 自动降级」，避免小内存机器 OOM：
  - 新增 `REVIEW_ALBUM_SIZE`（默认 5，合法范围 1–10），控制每条 media group 打包的媒体数；
    512 MiB 机器用小相册即可把 Telegram 上传阶段的 RSS 峰值压在单张量级。
  - 相册发送失败（超时 / 连接中断 / 限流重试耗尽）时自动**降级为逐张发送**，整份投稿不会
    因为相册一次性上传失败而整体失败；逐张之间保留原有 `RetryAfter` 退避与节流。
  - 相册返回消息数不一致时先把已发送消息记入清理列表再报错，避免失败时残留部分预览。

## [2.10.7] - 2026-08-30

### 修复

- 标签净化：Telegram hashtag 只接受字母、数字、下划线和中文/假名/韩文等非 ASCII 字母，
  包含 `-`、`/`、`()` 等字符的标签不会被官方客户端识别为可点击标签
  （如 `#r-18` 只会解析成 `#r`）。`process_tags` 现在按逗号/空白/斜杠拆分后去除所有
  非法字符，并把 `r-18 / r 18 / r_18` 统一归一为 `#r18`、`r-18g` 归一为 `#r18g`；
  数字开头的 Pixiv 标签保持原名。PixivFlow 传入的 `中文/中国語/chinese`、
  `カフカ(スターレイル)` 等作品标签现在都能正确渲染为可点击标签。
- 审核群多页预览改为「相册 + 回复链」：同一投稿的图片/文件按每批 ≤10 组包成 Telegram
  media group（相册）发送，29 页作品显示为 3 个相册而不是 29 条散消息；相册之间、以及
  小说 `.txt` 等单文件消息都会回复上一条，群内仍是一组完整回复链。单文件投稿（如小说）
  仍带完整 caption，保留 `file_id` 复用与限速/退避。`REVIEW_PREVIEW_THREAD=0` 可关闭回复链。
- 相册仅组合 Telegram 允许的媒体类型（图片/视频、音频、文档），GIF/animation 保持单发；
  遇到限流会重新打开本地文件再重试，返回数量异常时整单回滚，避免空上传或静默丢页。

### 新增

- 审核群预览回复链（2.10.6）：多页图集进入审核群时后续预览回复上一条消息。
- 自动投稿 caption 模板 `{{link}}` / `{{spoiler}}` 变量（配合 PixivFlow 2.10.5）。

## [2.10.5] - 2026-08-30

### 修复

- 多页插画进入审核群时，对每个 Telegram 预览上传显式使用 120 秒读写超时，避免 24 页等大型作品在默认短超时下返回 HTTP 502；可通过 `REVIEW_PREVIEW_TIMEOUT_SECONDS` 调整。

### 改进

- 保留逐页限速、`RetryAfter` 退避和 Telegram `file_id` 持久化；批准后仍复用已上传文件，不重复消耗媒体上行带宽。

## [2.10.4] - 2026-08-30

### 修复

- 修复审核预览遭遇 `RetryAfter` 后重复等待同一个已完成协程、导致重试失败的问题；每次重试现在都会重新创建发送请求并重新打开本地文件。
- 多页审核预览按可配置的 0.75 秒间隔稳定发送，并完整遵守 Telegram 返回的限流等待时间。
- Topic 投稿不再因来源主题为空而只保留 `#pixiv`；配合 PixivFlow 2.10.4 可接收计划主题与作品 Pixiv 标签，重复标签会按原顺序去重。

### 改进

- 保留 50 文件上限，同时把单次累计文件体积限制为 500MB，避免一个请求占满低配实例持久卷；父路由和子 API 继续分块流式处理。
- 修正 Bot/API 自报版本，并在 tag 发布流程校验 tag 与代码版本一致。

## [2.10.3] - 2026-08-29

### 修复

- 审核队列预览逐张发送时增加节流与 Telegram flood 退避（`RetryAfter` 等待后重试）：多页图集（如 24 页 Pixiv 作品）入审核队列不再触发 `Flood control exceeded` 502。

## [2.10.2] - 2026-08-29

### 修复

- 投稿入站文件数上限从 10 放宽到 50：多页插画/图集（如 Pixiv 24 页作品）此前会因 `too_many_files` 400 被拒；发布侧本就支持按每组 ≤10 自动拆成多个 Telegram media group，整本内容完整发出。

## [2.10.1] - 2026-08-29

### 修复

- 修复多 Bot 父路由在守护线程中启动失败的问题：`web.AppKey` 的模块名解析在非模块级调用上下文（daemon 线程）下抛 `UnboundLocalError: module`，导致 8080 路由完全不可用（`/health` 与 `/api/botN/v1` 全部失效）。改用普通字符串 key 存储 aiohttp client session，行为不变。

## [2.10.0] - 2026-08-29

### 改进

- 多 Bot 父路由改为 64 KiB 分块流式转发并复用 aiohttp 连接池，不再把最高 500 MiB 的 multipart 投稿完整复制到内存
- TelePost 向 Telegram 上传本地媒体时保持文件句柄流式读取，避免媒体组在 Python 进程内形成第二份大字节缓存
- API 临时上传目录现在覆盖成功、参数校验失败和 Telegram 异常等全部退出路径，避免持久卷长期积累孤立文件
- 父路由与子 API 显式支持 10 × 50 MiB 的请求上限，同时保留逐文件与文件数量校验
- tag 发布工作流新增完整 pytest 门禁，测试未通过时不再构建镜像或创建 Release

## [2.9.0] - 2026-08-29

### 新增

- Polling 模式现在与 Webhook 模式提供相同的 `/api/v1/*` 投稿接口，无公网机器也能接收本机或内网 PixivFlow 投稿
- 多 Bot 父路由在两种运行模式下都固定提供 `/api/botN/v1/*`，切换 Polling、AUTO 或 Webhook 时无需修改投递地址

### 改进

- Polling 的健康检查与投稿 API 和 Bot 运行在同一事件循环，关闭时会完成异步清理
- 多 Bot 子进程健康/API 端口固定错开为 8081、8082……，避免继承父端口后发生监听冲突

## [2.8.0] - 2026-08-29

### 新增

- `/health` 新增持久卷、PixivFlow cache、delivery outbox 与 API 临时上传指标；outbox 同时报告重试次数、错误文件数和最老任务年龄
- 新增 Mac 端 `update_telepost_policy.sh`，用非敏感 JSON 校验并一次更新多 Bot 的频道、审核群与审核来源策略

### 改进

- Docker 改为多阶段构建，生产镜像不再包含编译器、npm 和测试工具；Fly 联合档仅保留 Node 运行时与 PixivFlow
- 生产与测试依赖拆分为 `requirements.txt` 和 `requirements-dev.txt`
- PixivFlow 远程原子更新脚本固定使用联合部署配置，避免仓库默认 Fly 配置缺失时产生误导警告

## [2.7.3] - 2026-08-29

### 修复

- 兼容新版 python-telegram-bot 使用 tuple 返回图片尺寸，修复 multipart 图片进入审核群后 API 返回 502
- 审核预览上传后若无法提取 Telegram file_id，会自动删除已上传消息，避免群内残留孤立预览

## [2.7.2] - 2026-08-29

### 修复

- 同步机器人和 HTTP API 自报版本，避免 2.7 系列部署仍显示 2.6.0

## [2.7.1] - 2026-08-29

### 安全

- Webhook Secret Token 不再以明文或截断形式写入启动日志，避免托管平台日志泄露凭据

## [2.7.0] - 2026-08-29

### 新增

- **PixivFlow 联合运行档**：Docker 可选安装 PixivFlow 2.7，现有多 Bot supervisor 同时监督单个 Node 多计划调度进程，异常退出自动重启
- **Fly 512 MiB 模板**：双 Bot Webhook + PixivFlow 缓存投稿共用一台 Machine，默认关闭搜索、限制 Node heap/SQLite cache，并使用持久卷保存配置与 outbox
- **SSH 原子热更新脚本**：`scripts/update_pixivflow_config.sh` 校验本地 JSON，SFTP 上传临时文件后同卷替换，无需 WebUI或重启

### 改进

- `/health` 在保留 `python_rss_mb` 的同时新增 `process_rss`，同时展示 Python 与 Node 子进程内存
- PixivFlow 子进程不会继承 Telegram Bot Token，只接收独立投稿 token 与自身配置

## [2.6.0] - 2026-08-29

### 新增

- **运行模式自适应**：新增默认 `RUN_MODE=AUTO`；有效公网 HTTPS `WEBHOOK_URL` 自动选择 Webhook，否则选择 Polling，Webhook 注册失败时自动安全回退
- **可选投稿来源审核**：`API_REVIEW_REQUIRED` 与 `CHAT_REVIEW_REQUIRED` 可独立控制 HTTP API 和 Telegram `/submit` 投稿是否进入私有审核群，管理员点击通过/拒绝
- **审核持久化与幂等**：待审核媒体保存 Telegram file_id，SQLite 记录状态；`idempotency_key` 防止网络重试生成重复审核项
- **聊天审核结果通知**：聊天投稿通过或拒绝后，Bot 私聊通知原投稿人

### 修复

- API file_id 直投的单个媒体/文档不再错用 `send_media_group`

### 安全

- `/gen_token` 现在仅允许 `OWNER_ID` 对应的 Bot 所有者使用，防止普通用户生成可用于 HTTP API 投稿的访问令牌

## [2.5.0] - 2026-08

### 新增

- **HTTP API（/api/v1）**：token 鉴权的自动化投稿接口——multipart 上传媒体/文档（≤10 个、单文件 ≤50MB），支持标签/标题/简介/链接/匿名/剧透字段，与聊天投稿共用发布、记录、搜索索引与频控链路

- **token 管理**：/gen_token 生成（明文仅显示一次，服务端只存哈希）、/tokens 查看、/revoke_token 吊销；API 投稿按绑定 Telegram 身份记账与限频

- **多 bot API 寻址**：/api/botN/v1/* 按路径转发到对应子进程，各 bot token 体系独立

- **/health 内存自报**：进程 RSS 与系统可用内存（容量观测）

### 文档

- 新增 docs/API.md（接口参考）；README 增加致谢章节并精简措辞

## [2.4.0] - 2026-08

### 新增

- **多 bot 部署形态**：新增 run.py 启动器与多 bot webhook 路由——一台机承载任意数量的频道 bot（BOT1_TOKEN/BOT2_TOKEN/… 各自独立数据目录与回调路径），配合 auto_stop 空闲停机，把两个 bot 的运行成本压到一台机的钱

- **匿名投稿**：发布预览页一键切换匿名，开启后频道内 caption 不再显示投稿人

- **投稿流程精简**：剧透与匿名全部按钮化（预览页开关），砍掉两轮打字往返；未识别输入不再静默，catch_all 兜底引导

- **会话持久化**：投稿会话状态落盘（PicklePersistence），auto_stop 停机/重启/发版后用户可从原步骤继续，根治对话中途失忆

## [2.3.0] - 2026-08

### 修复（2026-08）
- 修复 20+ 处核心缺陷：发布回调路径失效、删帖功能因 sqlite3.Row.get() 崩溃、帖子统计/原帖查询不存在的列、文档模式切换无法推进会话状态、Webhook 优雅退出中断、caption 未做 HTML 转义导致含 <>& 的投稿发布失败、时间筛选按钮传字符串导致搜索为空、消息存在性检查转发给机器人自身空转、会话超时机制读取从未写入的数据等
- 恢复被误 ignore 的 health.py（Polling 模式 /health 健康检查，Docker HEALTHCHECK 依赖）
- 环境变量统一为 `TOKEN`，新增 `BOT_TOKEN` / `TELEGRAM_BOT_TOKEN` 别名兼容（修复按旧文档配置导致启动失败）
- 新增 `SUBMIT_LIMIT_PER_HOUR` 投稿频率限制（默认 10 次/小时，0 关闭）

重命名（2026-08）
- **项目更名为 TelePost**（原 TeleSubmit-v2）：与上游同名项目区分，名称更贴合"频道投稿"主题。仓库地址改为 `redtidev1918/TelePost`，Docker 服务/容器名、systemd 单元名（telepost.service）、健康检查 service 标识同步更新；CHANGELOG 中更早条目保留历史名称。

新增（2026-08）
- **发布流水线**：新增 tag 驱动的 GitHub Actions——构建 amd64/arm64 镜像推送 GHCR（x.y.z / x.y / latest 标签），自动创建带 CHANGELOG 摘录的 GitHub Release；docker-compose 内置官方镜像名
- **发布前编辑流程**：投稿最后一步改为预览确认页，支持发布前快速修改标签/简介、补充媒体或取消（新增 EDIT_TAG/EDIT_NOTE/EDIT_MEDIA 会话状态）
- **结果列表分页**：/search 与 /hot 支持按钮翻页（⬅️/页码/➡️），查询上下文跨回调保持
- **投稿频率限制** SUBMIT_LIMIT_PER_HOUR（每用户每小时上限，默认 10，0 关闭）

变更（2026-08）
- 投稿发布失败时保留会话数据，避免重传全部媒体
- 剧透确认步骤严格校验"是/否"，避免错字被静默当作"否"直接发布
- 搜索功能禁用时不再误建索引目录

文档（2026-08）
- 文档体系重构：新增 INSTALL / CONFIGURATION / COMMANDS / OPERATIONS / PERFORMANCE / TROUBLESHOOTING / TESTING 与 internals/moderation.md，重写 README，移除重复与失效文档

新增
- **频道消息监听器增强**
  - 增强频道消息监听器的鲁棒性和错误处理
  - 支持处理非本项目 bot 发布的不规范帖子
  - 自动提取和规范化标签（支持 #标签 和 [标签] 格式）
  - 智能文本清理（移除控制字符、规范化换行、限制长度）
  - 并发控制机制，防止重复处理同一消息
  - 完善的错误处理和日志记录
  - 支持多种消息格式和媒体类型
  - 自动同步到搜索索引
- **中文部分匹配搜索支持**
  - 使用 SimpleAnalyzer 时，支持中文关键词的部分匹配
  - 搜索"卫宫"可以匹配"卫宫士郎"等包含该关键词的内容
  - 自动检测中文查询并使用通配符查询优化
  - 在标题、描述、标签、文件名等多个字段中搜索
  - 提升中文搜索体验，无需完整输入即可找到相关内容
- **Webhook 模式支持**
  - 新增 Webhook 运行模式，与 Polling 模式并存
  - 支持通过配置文件或环境变量切换模式
  - 响应速度更快（<1秒），资源消耗更低
  - 适用于生产环境和云服务器部署
  - 内置 Secret Token 验证机制
  - 支持多种部署方式（VPS、Docker、PaaS 平台）
- **安装脚本增强**
  - `install.sh` 新增运行模式选择向导
  - 自动引导用户选择 Polling 或 Webhook 模式
  - Webhook 模式自动验证 URL 格式
  - 配置完成后显示摘要信息

文档
- 新增 `docs/WEBHOOK_MODE.md` - Webhook 模式完整指南
  - 两种模式详细对比
  - 多种部署方式示例（通用/Docker/PaaS）
  - 模式切换步骤说明
  - 故障排查指南
- `README.md` 新增运行模式章节
- `SCRIPTS_GUIDE.md` 计划更新（待添加）
- 更新 `DELETE_POST_GUIDE.md` - 删除功能实现原理说明
  - 详细说明标记删除（Soft Delete）机制
  - 数据库字段设计和查询过滤机制
  - 双向同步删除机制
  - 数据保留策略和恢复方法

技术细节
- 新增 `handlers/channel_listener.py` - 频道消息监听器模块（增强版）
  - 并发控制机制（`_processing_messages` 和 `_processing_lock`）
  - 文本清理和规范化函数（`clean_text`、`extract_tags_from_text`）
  - 字段长度限制常量（防止数据库溢出）
  - 完善的错误处理和日志记录
- 新增 `utils/webhook_server.py` - Webhook 服务器模块
- 更新 `config/settings.py` - 支持 Webhook 配置项
- 更新 `main.py` - 双模式启动逻辑
- 新增依赖：`aiohttp>=3.9.0`（用于 Webhook）
- 新增配置项：`RUN_MODE`、`WEBHOOK_URL`、`WEBHOOK_PORT`、`WEBHOOK_PATH`、`WEBHOOK_SECRET_TOKEN`

改进
- **删除功能优化：标记删除机制**
  - 采用标记删除（Soft Delete）替代物理删除，保留所有历史数据
  - 数据库记录永久保留，仅标记 `is_deleted = 1` 状态
  - 所有用户查询自动过滤已删除的帖子（`is_deleted = 0`）
  - 统计数据保留在数据库中，可用于历史分析
  - 支持双向同步删除（机器人删除 ↔ 频道删除）
  - 添加 `is_deleted` 字段索引，提升查询性能
  - 添加删除状态检查，避免重复删除已标记的帖子
  - 误删的帖子可以通过数据库操作恢复
  - 更新 `DELETE_POST_GUIDE.md` 文档，详细说明实现原理

修复
- **频道消息监听器** - 增强错误处理，防止异常导致监听器崩溃
- **消息重复处理** - 添加并发控制，防止同一消息被重复处理
- **文本长度限制** - 添加字段长度限制，防止数据库溢出
- **标签提取** - 改进标签提取逻辑，支持多种格式
- **搜索索引为空问题** - 修复索引为空时搜索无结果的问题，自动重建索引
- **中文搜索部分匹配** - 修复使用 SimpleAnalyzer 时中文无法部分匹配的问题
- **端口冲突问题** - health.py 仅在 Polling 模式启动，避免与 Webhook 服务器冲突
- **Webhook 服务器** - 使用 aiohttp 同时处理 `/webhook` 和 `/health` 端点
- **优雅关闭** - 正确清理 Webhook 服务器和 Telegram webhook 设置

测试
- 本地测试：Polling 和 Webhook 模式均通过
- 生产部署：256MB 内存环境下稳定运行
- Telegram Webhook：成功设置并接收消息
- 健康检查：两种模式均正常响应
- 验证平台：PaaS 平台、VPS 服务器、Docker 容器
---

## [2.2.0] - 2025-10-25

### 新增

- **一键安装脚本** (`install.sh`):
  - 自动检测系统环境（Linux/macOS）
  - 智能检查并安装依赖（Docker/Python）
  - 交互式选择部署方式（Docker/Systemd/直接运行）
  - 集成配置向导，引导完成初始配置
  - 支持多种 Linux 发行版（Ubuntu/Debian/CentOS/RHEL）

- **更新脚本** (`update.sh`):
  - 自动备份数据和配置
  - 检测当前部署方式并智能更新
  - 显示更新内容预览
  - 支持暂存和恢复本地更改
  - 自动重启服务

- **重启脚本** (`restart.sh`):
  - 智能查找并停止所有运行中的机器人进程
  - 优雅停止（SIGTERM）+ 强制终止（SIGKILL，超时10秒）
  - 配置文件验证（可选，与 `check_config.py` 集成）
  - 支持 `--stop` 参数仅停止不重启
  - 兼容 macOS 和 Linux 系统
  - 自动处理多个进程实例

- **卸载脚本** (`uninstall.sh`):
  - 优雅停止所有服务
  - 可选择保留或删除数据
  - 自动备份数据（如果选择删除）
  - 清理 Systemd/Docker 相关配置

### 文档

- **README.md 全面优化**:
  - 全新的视觉设计，使用表格和图标
  - 添加项目亮点展示区域
  - 更清晰的快速开始指南
  - 优化命令列表展示
  - 增加详细的使用示例
  - 使用 Shields.io 徽章
  - 更清晰的项目结构展示

- **新增 DEPLOYMENT.md**:
  - 详细的部署指南
  - 四种部署方式完整说明
  - 配置说明和示例
  - 更新和维护指南
  - 故障排查手册
  - 安全建议

### 改进

- **部署脚本增强**:
  - 所有脚本添加彩色输出（红/绿/黄/蓝）
  - 统一的错误处理和用户反馈
  - 更详细的进度提示
  - 支持非交互式运行（Docker 环境）
  - 添加执行权限检查

- **Makefile 扩展**:
  - 新增 `check` 命令（检查配置）
  - 新增 `update` 命令（更新到最新版本）
  - 优化 `backup` 命令（包含时间戳）
  - 改进 `status` 命令（显示资源使用）

### 新增文件

- `install.sh` - 一键安装脚本（支持多平台）
- `update.sh` - 智能更新脚本
- `uninstall.sh` - 优雅卸载脚本
- `DEPLOYMENT.md` - 完整部署文档

### 优化

- README 使用更专业的排版和布局
- 添加更多视觉元素（图标、徽章、表格）
- 统一文档风格和格式
- 改进代码示例的可读性
- 优化移动端阅读体验

### 文档清理

- **精简 README.md**（从 918 行减少到 ~500 行）：
  - 移除与 DEPLOYMENT.md 重复的部署细节
  - 移除详细的故障排查内容（指向部署指南）
  - 移除冗长的配置示例（保留最小配置）
  - 移除重复的 Docker、更新、安全建议章节
  - 精简使用示例和依赖列表
  - 保持核心功能展示和命令参考

- **删除重复/过时文档**：
  - `DEPLOY_GUIDE.md`（与 DEPLOYMENT.md 重复）
  - `PROJECT_READY.md`（临时项目状态文件）
  - `PRIVACY_PROTECTION_SUMMARY.md`（临时总结文件）
  - `GITHUB_FILES.md`、`GITHUB_UPLOAD_GUIDE.md`、`PUSH_TO_GITHUB.md`（开发临时文件）
  - `隐私保护完成.md`（中文临时文件）

- **文档结构优化**：
  - README：项目介绍、快速开始、核心功能、命令参考
  - DEPLOYMENT.md：详细部署、配置、更新、故障排查
  - ADMIN_GUIDE.md：管理功能详解
  - CHANGELOG.md：版本历史记录

---

## [2.1.0] - 2025-10-28

### 新增

- **搜索引擎自动适配功能**
  - 启动时自动检测索引兼容性
  - 自动备份和重建不兼容的索引
  - 智能分词器切换（jieba ↔ simple）
  - 优雅降级处理，确保服务稳定性
  - 修改配置后重启即可，无需手动重建索引

- **内存优化增强**
  - 支持移除 jieba 依赖以节省 ~140MB 内存
  - 优化 Docker 健康检查机制
  - 改进错误处理和日志输出

### 优化

- **搜索引擎初始化**
  - 添加 `index.exists_in()` 异常捕获
  - 改进索引重建流程的错误处理
  - 更好的备份和恢复机制
  
- **文档更新**
  - 更新 MEMORY_USAGE.md 添加自动适配说明
  - 优化 README.md 内存优化部分
  - 整合部署相关文档

### 修复

- **索引兼容性问题**
  - 修复分词器不可用时的索引加载错误
  - 修复索引检查失败导致启动失败的问题
  - 改进备份失败时的降级处理

---


### 新增

- **搜索引擎**: 集成 Whoosh 全文搜索引擎，支持中文分词（基于 jieba）
  - 按关键词搜索投稿内容
  - 按标签快速查找
  - 搜索结果按相关度和热度排序
- **数据迁移工具**: 提供 `migrate_to_search.py` 将现有投稿导入搜索索引

### 改进

- **部署脚本增强**:
  - `deploy.sh` 支持 `--rebuild` 和 `--clean` 选项
  - `start.sh` 增加更详细的启动检查和功能状态展示
  - 优化 Makefile，新增 `migrate`、`check`、`dev` 等命令
- **Docker 优化**:
  - 容器名称更新为 `telesubmit-v2`
  - 增加 g++ 编译器支持（用于 Whoosh 编译）
  - 优化健康检查逻辑（检查主进程而非仅配置文件）
  - 内存限制提升至 1GB（搜索功能需要）
  - 新增 `data/search_index` 目录挂载
- **构建优化**:
  - 更新 `.dockerignore`，排除更多不必要文件
  - 更新 `.gitignore`，忽略搜索索引文件

### 依赖更新

- 新增 `whoosh >= 2.7.4` - 全文搜索引擎
- 新增 `jieba >= 0.42.1` - 中文分词库

### 文档

- 新增搜索功能相关文档（SEARCH_INTEGRATION.md 等）
- 更新部署脚本的帮助信息

---

## [2.0.x] - 之前版本

### 修复

- **索引管理器修复**:
  - 修复数据库列名错误：`id` → `message_id`
  - 修复缺失的导入：添加 `os` 和 `shutil`
  - 统一返回值类型：所有操作返回 `dict` 格式
  - 简化索引优化逻辑避免 API 兼容性问题

- **修复数据库列名错误**:
  - `published_posts` 表的主键是 `message_id`，没有 `id` 列
  - 删除相关说明中误用 `id` 的示例，统一为 `message_id`

### 之前新增

- **帖子删除功能（OWNER 专用）**:
  - 搜索结果中添加删除按钮（仅 OWNER 可见）
  - `/myposts` 命令中添加删除按钮（OWNER 专用）
  - 批量删除命令 `/delete_posts` 支持：
    - 单个删除：`/delete_posts 123`
    - 多个删除：`/delete_posts 123 456 789`
    - 范围删除：`/delete_posts 100-110`
    - 混合删除：`/delete_posts 100-110 150 200-205`
    - 最多一次删除 50 个帖子
  - 完善的权限检查机制（仅 OWNER_ID 用户可删除）
  - 删除操作包括：
    - 从数据库标记为已删除（保留历史数据）
    - 从搜索索引删除
    - 删除关联的多媒体消息索引
  - 不删除频道中的实际消息（需手动删除）
  - 删除后标签统计自动更新
  - 删除后搜索索引实时更新
  - 详细的删除统计报告

- **文件名搜索支持**:
  - 搜索引擎新增 `filename` 字段，支持文件名搜索
  - 文档上传时自动提取并保存文件名
  - 多字段搜索：标题、简介、标签、**文件名**
  - 完全兼容旧数据（历史投稿无文件名）

### 改进

- 数据库 schema 更新：
  - `published_posts` 表新增 `filename` 字段
  - 文档存储格式升级：`document:file_id:filename`
  - 完全向后兼容旧格式

- 搜索引擎优化：
  - Whoosh Schema 包含 filename 字段
  - 支持中文分词的文件名搜索
  - 查询解析器扩展至 4 个字段
  - 修复搜索结果中文件名显示问题

### 新增工具

- `migrate_add_filename.py` - 数据库 schema 迁移工具
- `migrate_extract_filenames.py` - 文件名提取迁移说明
- `FILENAME_SEARCH_UPGRADE.md` - 详细升级指南
- `test_delete_feature.py` - 删除功能测试脚本
- `DELETE_POST_GUIDE.md` - 帖子删除功能详细指南

### 安全改进

- 更新 `.gitignore`，增强临时文件保护：
  - 自动忽略所有 `test_*.py`、`fix_*.py`、`fetch_*.py` 等临时脚本
  - 自动忽略 `*_FIX.md`、`*_SUMMARY.md` 等临时文档
  - 保护敏感数据和测试文件不被误提交

### 文档

- 搜索帮助文档更新，说明文件名搜索功能
- 添加文件名搜索使用示例
- 更新 README，明确说明搜索范围包含文件名
 - 移除 README 中对 `docs/INDEX.md` 的失效链接

---

## [2.0.0] - 2025-10-25

### 重大更新

这是一个完全重写的版本，采用了更现代化的架构和更好的代码组织。

### 新增

- **模块化架构**: 将代码拆分为多个模块（handlers, utils, config），更易维护
- **环境变量支持**: 支持通过环境变量配置，方便容器化部署
- **改进的会话管理**: 使用数据库存储用户会话，支持并发处理
- **黑名单系统**: 完整的黑名单管理功能，支持添加/移除/查询
- **搜索功能**: 支持按关键词、标签、投稿人搜索历史投稿
- **统计功能**: 详细的投稿统计信息和用户排行
- **批量操作**: 支持一次投稿多个文件/图片
- **更好的错误处理**: 更友好的错误提示和异常处理
- **日志系统**: 完整的日志记录，方便调试和监控

### 改进

- **性能优化**: 使用异步操作，提升响应速度
- **代码质量**: 更好的代码组织和文档注释
- **配置管理**: 统一的配置管理，支持默认值和验证
- **用户体验**: 更清晰的提示信息和交互流程
- **安全性**: 添加权限检查和输入验证

### 架构变化

```
v1.x (单文件)          →  v2.x (模块化)
main.py (379行)        →  main.py (主入口)
                          ├── config/
                          │   └── settings.py (配置管理)
                          ├── handlers/
                          │   ├── basic_handlers.py (基础命令)
                          │   ├── submission_handlers.py (投稿处理)
                          │   ├── admin_handlers.py (管理功能)
                          │   └── search_handlers.py (搜索功能)
                          └── utils/
                              ├── database.py (数据库操作)
                              ├── helpers.py (辅助函数)
                              └── blacklist.py (黑名单管理)
```

### 依赖更新

- `python-telegram-bot`: 升级到 21.10
- 新增 `python-dotenv`: 支持 .env 文件
- 新增 `aiosqlite`: 异步数据库操作
- 新增 `psutil`: 系统监控

### 迁移指南

从 v1.x 迁移到 v2.0：

1. **配置文件**: 
   - 旧版配置可以继续使用
   - 建议使用新的 `config.ini.example` 作为模板

2. **数据库**:
   - 旧的 `submissions.db` 可以继续使用
   - 会自动创建新的 `user_sessions.db` 用于会话管理

3. **命令变化**:
   - 所有命令保持向后兼容
   - 新增了搜索和统计命令

### 破坏性变化

- 配置文件读取逻辑改变，现在支持环境变量优先
- 某些内部 API 已重构，如果你基于 v1.x 做了自定义修改，需要适配新架构

### 修复

- 修复了长时间运行后的内存泄漏问题
- 修复了并发投稿时的状态冲突
- 修复了特殊字符处理的问题
- 改进了文件上传的稳定性

### 文档

- 新增快速开始指南 (QUICKSTART.md)
- 更新了 README 文档
- 添加了详细的代码注释

---

## [1.0.0] - 初始版本

- 基础投稿功能
- 简单的管理命令
- 单文件实现
