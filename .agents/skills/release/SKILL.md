---
name: telepost-release
description: TelePost 的可重复发布流程（fix PR → CI → merge → release-please → 产物 → 自动部署 → 验收 → 文档）。当日已按此流程完成 2.84.5/2.84.6 两班次。
---

# TelePost 发布流程

1. **工作树**：`git status` 干净；从最新 `main` 切分支，命名 `codex/<topic>`（branch-contract 要求）。
2. **变更范围自查**：不混无关重构；`git diff --stat` 逐文件核对。
3. **测试**：先目标文件 `pytest -q tests/<area>`，再全量 `./.venv/Scripts/python -m pytest -q`
   （~5 分钟；本机 `check_config` 失败为预期）。若改 `api/openapi.yaml`：`cd webapp && npx openapi-typescript ../api/openapi.yaml -o src/api/generated/schema.d.ts`。
   若改中文文案：同步 `ui/locales/en/LC_MESSAGES/telepost.po` 并用 babel 重编译 `.mo`
   （`test_bot_language` 是门禁）。
4. **PR**：`gh pr create`（中文正文：根因/修复语义/安全论证/测试计划）。
5. **CI**：`gh pr checks <n>` 直至无非 pass 项（Linux/Windows 全量 + webapp + branch-contract + gitleaks）。
6. **合并**：`gh pr merge <n> --squash --delete-branch`。
7. **release-please**：合并后约 1 分钟自动开 `chore(main): release X.Y.Z` PR；等它 CI 全绿后合并。
8. **产物核对**：Release run 成功 → `gh release view vX.Y.Z`（8 产物）+ PyPI JSON 200。
9. **部署**：TelePost 仓库的 deploy-fly 在 Release 成功后**自动**以固定镜像部署生产
   （无需手工 fly 命令）；deploy 仓 `update-telepost` 自动开基线 PR（versions.json）→ CI 绿后合并。
10. **验收（只读）**：`/health`（version/commit/bots）、`/ready`、`/api/botN/v1/health`
    （含 `inbox` 深度）、`smoke-telepost.sh` 4/4；`fly logs` 抽查启动与错误。
    Fly SSH 可用时补机内 `doctor --all-bots`；不可用则记 BLOCKED，不宣称已完成。
11. **回滚点**：记录上一稳定版本镜像 tag（`ghcr.io/redtidev1918/telepost:<prev>`）与 commit。
12. **文档收尾**：deploy 仓 `docs/operations/current-state.md` 增条目（状态词 VERIFIED 等）；
    本仓 `.agent/STATUS.md`/`HANDOFF.md` 同步。跨仓行为变化更新 deploy 仓架构文档。

## 约束

- 生产操作（部署/重启/迁移）仅走上述自动化通道；未经用户明确授权不执行其它不可逆操作。
- PixivFlow 侧发布流程见其仓库自身文档，此处不复述；生产 pin 与部署权威在 deploy 仓。
- 多仓发布顺序：TelePost 与 PixivFlow 可并行；协议不兼容变更需先发消费者侧或同批部署。
