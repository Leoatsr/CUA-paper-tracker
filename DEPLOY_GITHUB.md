# GitHub Actions 云端部署指南

本项目已配置 GitHub Actions，可以在 GitHub 服务器上每天自动定时采集论文并发布报告到 GitHub Pages。

## 🎯 最终形态

- **每天 UTC 14:00（北京 22:00 左右）自动跑**
- 报告发布到 https://leoatsr.github.io/CUA-paper-tracker/
- 不再需要你本地开机或执行任何命令
- 可以在 Actions 页面手动触发（传参指定日期/关键词）

## 🚀 一次性部署步骤（约 15 分钟）

### Step 1. 清理 GitHub 仓库

你的仓库是 https://github.com/Leoatsr/CUA-paper-tracker，现在里面有之前 Claude 帮你做的另一版代码。

**两个选择**：

**A. 保留历史，在新分支开发（稳妥）**
```bash
# 本地建个 dev 分支，把新代码覆盖上去
git clone https://github.com/Leoatsr/CUA-paper-tracker.git
cd CUA-paper-tracker
git checkout -b paper-tracker-v2
# 覆盖所有文件 → 提交 → PR 合并
```

**B. 直接全部覆盖（干净）**
```bash
git clone https://github.com/Leoatsr/CUA-paper-tracker.git
cd CUA-paper-tracker
# 把仓库里所有东西删干净（留 .git 目录）
git rm -rf .
# 然后把本项目的所有文件拷贝进来
```

我推荐 B（干净）。

### Step 2. 把本项目推到仓库

把本项目所有文件（包括 `.github/`）推上去：

```bash
cd CUA-paper-tracker
# 确保所有文件在这个目录下 ls -la 能看到 .github src config data requirements.txt 等
git add -A
git commit -m "feat: cloud-ready paper tracker v2.4"
git push origin main
```

⚠️ **推送前确认 `.env` 没有被提交**：`git status` 看看有没有 `.env` 在 staged 区域，有的话 `git restore --staged .env` 撤销。`.gitignore` 已经设了 `.env`，正常情况不会被 track。

### Step 3. 配置 GitHub Secrets

在 GitHub 仓库页面：

1. 点 **Settings** → 左侧 **Secrets and variables** → **Actions**
2. 点 **New repository secret**，逐个添加以下 4 个（复制你本地 `.env` 里的值）：

| Name | Value |
|---|---|
| `FEISHU_APP_ID` | `cli_a954f2e38cb8dcd3` |
| `FEISHU_APP_SECRET` | 你重置后的 Secret |
| `FEISHU_APP_TOKEN` | `KEzQwGjtgih34GkejmbcfPIwnIe` |
| `FEISHU_TABLE_ID` | `tblnciqGn3Xgdq8V` |

⚠️ 每个 Secret 填完点 **Add secret**，Secret 一旦存入**不能再查看**（只能更新覆盖）。

### Step 4. 开启 GitHub Pages

1. 仓库页 **Settings** → 左侧 **Pages**
2. **Source** 选择 **Deploy from a branch**
3. **Branch** 选 `gh-pages` → `/ (root)` → **Save**
4. 如果列表里还没有 `gh-pages` 分支：不用手动建，等第一次 Actions 跑完会自动创建

### Step 5. 首次手动触发跑一次

1. 仓库页 **Actions** 标签
2. 左侧选 **Daily Paper Tracker**
3. 右侧 **Run workflow** 按钮：
   - Branch: `main`
   - `target_date`: 留空（采北京昨日）或填 `2026-04-17`
   - `keywords`: 留空（跑全部 6 个）或填 `GUI Agent,Web Agent`（只跑两个）
   - `dry_run`: 第一次建议勾 ✅，不写飞书先试水
4. 点 **Run workflow** 开始

### Step 6. 观察运行情况

点 Actions 页面里那个刚启动的 workflow，能看到每一步的日志。核心检查点：

- `Install Playwright Chromium` 是否装成功（约 1-2 分钟）
- `Run paper tracker` 是否真的采集到论文（看日志最后有没有 `任务结束 | 录入 N`）
- `Publish report to gh-pages` 是否成功

### Step 7. 访问报告

首次部署 GitHub Pages 需要 1-3 分钟才生效。然后：

- 索引页：https://leoatsr.github.io/CUA-paper-tracker/
- 某天的报告：https://leoatsr.github.io/CUA-paper-tracker/report_2026-04-17.html

## 📅 自动调度

workflow 配置为每天 **UTC 14:00** 自动跑（≈ 北京 22:00）。

⚠️ GitHub Actions cron 可能**延迟 5-30 分钟**（负载高时更长），不保证准时。

## 🛠️ 手动操作

### 随时跑一次

Actions → Daily Paper Tracker → Run workflow（可以传参数）

### 修改定时

编辑 `.github/workflows/daily.yml` 顶部的 `cron: '0 14 * * *'`，推到 main 即可。

cron 格式参考（UTC 时间）：
- `0 14 * * *` — 每天 UTC 14:00（北京 22:00）
- `0 2 * * *` — 每天 UTC 02:00（北京 10:00）
- `0 */12 * * *` — 每 12 小时

## 🔍 常见问题排查

### Q: 第一次跑失败报 chatpaper.com 超时？

GitHub Actions runner 在美国，可能访问 chatpaper 很慢或不通。看 `Run paper tracker` 步骤的日志。

**如果确实连不上**：
- 检查 chatpaper.com 是否有美国 IP 限制
- 考虑用 GitHub 自建 runner（在自己电脑/云服务器上搭 runner，这样网络环境就是你的）
- 或者用 Cloudflare Worker 做中转

### Q: 报告能看到但飞书没写入？

检查 Secrets 是否 4 个都设对了，特别是 Secret 有没有粘贴时带了前后空格。

### Q: history.json 没更新？

看 workflow 的 `Commit history.json` 步骤有没有执行成功。如果显示"history.json 无变化，跳过 commit"说明这次没采到新论文。

### Q: 某天跑失败要重跑？

Actions → 那次失败的 run → 右上 **Re-run failed jobs**

## 📂 目录结构（提交到仓库的）

```
CUA-paper-tracker/
├── .github/workflows/daily.yml   ← Actions 配置
├── .gitignore
├── README.md
├── requirements.txt
├── src/                          ← 代码
│   ├── __init__.py
│   ├── main.py
│   ├── chatpaper.py
│   ├── feishu.py
│   ├── pdf_analyzer.py
│   ├── matchers.py
│   ├── models.py
│   ├── report.py
│   ├── dedup.py
│   └── scheduler.py              (云端不用，但保留不影响)
├── config/                       ← 运行时工作流会生成 config.yaml，这个目录可以只有 .gitkeep
│   └── .gitkeep
├── data/
│   ├── .gitkeep
│   └── history.json              ← 去重历史（工作流会 commit 回来）
├── tests/
│   └── test_matchers.py
└── tools/
    └── dump_html.py              (本地调试用)
```

✅ **不要提交的**：
- `.env` (gitignore 已忽略)
- `venv/`（本地虚拟环境）
- `data/logs/`、`data/dump/`、`data/reports/`（gitignore 已忽略）
- `config/config.yaml`（gitignore 已忽略，云端会临时生成）
