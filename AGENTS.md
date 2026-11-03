# TelePost Agent 入口

先确认任务范围、git status、当前分支与远端；修改使用独立 worktree，不覆盖未知改动。

## 隐私与安全
生产主题、偏好 tag、业务别名和可推断主题的 target 昵称仅属于私有运行配置。
公开源码、UI 示例、测试夹具、文档、PR 与发布说明一律使用中性虚构数据。
不得把私有运行配置导出回仓库；历史清理后的旧克隆不得直接推送，以免重新污染历史。

这份文件写给任何进入本仓库的智能体或工程师。先读跨仓库权威文档，它们共同定义职责边界与执行纪律：

* `pixivflow-telepost-deploy/AGENTS.md`（PixivFlow Ecosystem Agent Operating Contract）
* `pixivflow-telepost-deploy/docs/architecture/ecosystem-platform.md`（长期架构）
* `pixivflow-telepost-deploy/docs/operations/current-state.md`（当前生产状态）
* `pixivflow-telepost-deploy/CONTRACT.md`（PixivFlow ↔ TelePost 跨仓库契约）

本文件只保留本仓边界与特殊约束；与上面权威冲突时以上面为准，并顺手修正。

## 默认读取规则

- 普通开发：本文件、涉及目录的 AGENTS.md、相关源码与测试；按下表读取细则中对应标题段落（先 `rg -n '^#' docs/agent-guide.md` 定位，再按行读取）。不得因按需读取而省略适用约束。
- 接续旧任务：先读 `.agent/STATUS.md`（若存在），再检索该任务的 TASKS/HANDOFF/DECISIONS/LESSONS；不默认加载全部历史。
- 跨仓接口或职责变更：读取 Deploy AGENTS.md、CONTRACT.md 与相关架构章节；只扫描涉及仓库。
- 生产变更或事故：另读 Deploy `docs/operations/current-state.md`、相关运维/交付计划；历史按日期、PR、故障检索，不默认读全量记录。
- 领域、安全、发布和 CI 门禁不因文档拆分而放宽。保留标签、数据与审计历史；不打印凭据；不盲重试不确定的外部写入。
- 复用现有领域服务和 ReleaseGraph，不新建平行状态库或发布引擎。非平凡新增能力先调查已有实现。
- 文档与证据冲突时核对运行事实并修正文档。报告分别说明本地验证、CI、提交/PR、发行、部署、运行验收；不把测试通过当作生产通过。

## 按任务加载细则

完整规则保存在 [Agent 细则](docs/agent-guide.md)，以下标题均为检索入口。修改对应领域前必须读取该段及其引用的相关约束。

- 公开仓库的业务隐私
- 一句话
- 目录职责
- 绝对不要做
- 改动前必须保持的行为
- 审核回调反馈与卡片恢复不变量（§review-card-recovery）
- 改完请自证
- 审核群「重抓」不变量
- Refetch Generation Replacement 不变量（§refetch-replacement）
- 已知待办（本仓库范围）
- 投稿 UX 与预览不变量（§submission-disposition, §preview-ux）
- Telegram Mini App 不变量（presentation adapter, §88/§149）
- Mini App 基础设施不变量（framework-first，硬约束）
- 身份与归属不变量（硬约束，§identity）
- Schedule 通知与 mention 不变量
- Refetch 生命周期状态机（§refetch-lifecycle）
- Refetch 终态可见性 与 watchdog 不变量（§refetch-terminal-notify）
- Editorial Revision 与投稿者通知不变量（§editorial, §notify-submitter）
- Manager 新投稿通知与多图 packing 不变量
- Media Capacity SSOT 不变量（§media-packing）
- Discussion 失败隔离不变量（§discussion-failure-isolation）
- Schedule 失败可观测性与 Manual Recovery 不变量（§failure-observability, §manual-recovery）
- Publication Presentation 不变量（§publication-presentation）
- Submission CTA 不变量（§submission-entrypoint）
- Review 卡不携带公共投稿 CTA（§review-cta）
- Novel Cover 与 fallback card 不变量（§novel-cover）
- Novel TXT Telegraph Preview（§telepress-preview）
