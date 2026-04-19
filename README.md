# CUA Paper Tracker · 论文定时追踪与飞书录入

每日自动从 [ChatPaper](https://chatpaper.com) 采集指定关键词下发布的最新论文，对 PDF 做关键词计数与过滤，将命中的论文录入飞书多维表格，并生成可视化 HTML 报告发布到 GitHub Pages。

**🟢 在线报告**：<https://leoatsr.github.io/CUA-paper-tracker/>

---

## ✨ 功能

- 🔍 **按关键词搜索 + 翻页**：6 个一级关键词（GUI Agent / Web Agent / CUA / computer use / mobile agent / GUI grounding），自动翻页直到遇到目标日期前一天的论文
- 📅 **按目标日期采集**：默认采集「北京时间昨天」发布的论文，可通过参数指定
- 📑 **PDF 关键词计数**：从 arXiv 下载 PDF，统计 Web Agent / GUI Agent 两类词出现次数，未命中的论文自动过滤不写飞书
- 📊 **多维表录入**：10 个字段完整录入（中/英文标题、机构、日期、作者、arxiv、project、概要、简介、图片）
- 🔁 **跨任务去重**：用 `data/history.json` 持久化已处理论文的 arXiv ID
- 📈 **可视化报告**：每次跑完生成 HTML 报告（Chart.js 柱状图 + 录入/过滤明细表）
- ☁️ **云端自动运行**：GitHub Actions 每天定时跑，无需本地开机

---

## 🎯 运行模式

### 方式一：GitHub Actions 云端跑（推荐）

仓库里的 `.github/workflows/daily.yml` 已配置好。

- **自动触发**：每天 UTC 14:00（北京时间 22:00 左右）
- **手动触发**：GitHub Actions 页面 → Run workflow，可传入 `target_date`、`keywords`、`dry_run` 参数
- **报告发布**：每次跑完 HTML 报告自动推送到 `gh-pages` 分支，访问 https://leoatsr.github.io/CUA-paper-tracker/ 查看

部署步骤详见 [DEPLOY_GITHUB.md](./DEPLOY_GITHUB.md)。

### 方式二：本地跑

```bash
# 立即跑一次（采北京昨天的论文）
python -m src.main --once

# 指定目标日期
python -m src.main --once --date 2026-04-17

# 只跑指定关键词（逗号分隔）
python -m src.main --once --keywords "GUI Agent,Web Agent"

# DRY-RUN 模式：不写飞书、不更新 history
python -m src.main --once --dry-run

# 常驻调度模式（每日北京时间 22:00 自动跑）
python -m src.main
```

---

## 🏗️ 项目结构

```
CUA-paper-tracker/
├── .github/workflows/
│   └── daily.yml              # GitHub Actions 定时工作流
├── src/
│   ├── main.py                # 主入口 + CLI 参数解析
│   ├── chatpaper.py           # Playwright 采集 chatpaper.com（URL 搜索 + 翻页）
│   ├── pdf_analyzer.py        # PyMuPDF 下载分析 arXiv PDF
│   ├── feishu.py              # 飞书多维表录入 + 图片上传（支持 Wiki 嵌套）
│   ├── matchers.py            # 关键词模糊匹配规则
│   ├── models.py              # Pydantic 数据模型
│   ├── dedup.py               # history.json 持久化
│   ├── report.py              # HTML 可视化报告生成
│   └── scheduler.py           # APScheduler 调度（本地常驻模式用）
├── config/
│   ├── config.example.yaml    # 配置模板（字段映射等）
│   └── .env.example           # 飞书凭证模板
├── data/
│   ├── history.json           # 已处理 arXiv ID（云端工作流自动 commit 回仓库）
│   ├── logs/                  # 运行日志（.gitignore）
│   └── reports/               # 生成的 HTML 报告（.gitignore，云端会推到 gh-pages）
├── tests/
│   └── test_matchers.py       # 匹配规则单元测试
├── tools/
│   └── dump_html.py           # 本地调试工具：dump chatpaper 真实 DOM
├── DEPLOY_GITHUB.md           # 云端部署完整指南
├── requirements.txt
└── README.md
```

---

## 📋 核心规则

### 一级关键词（6 个）

在 chatpaper 搜索页输入这些关键词：

| 关键词 | 说明 |
|---|---|
| `GUI Agent` | 图形界面智能体 |
| `Web Agent` | 网页智能体 |
| `CUA` | Computer-Using Agent |
| `computer use` | 电脑操作能力 |
| `mobile agent` | 移动端智能体 |
| `GUI grounding` | GUI 定位 |

### 二级关键词（PDF 计数）

对命中的论文 PDF 全文计数以下两类，合计 ≥ 1 才写飞书：

- **Web Agent 类**：web agent / webagent / web-agent / web agents（大小写/连字符/复数不敏感）
- **GUI Agent 类**：gui agent / guiagent / gui-agents（同上）
- **CUA**：单词边界精确匹配（区分大小写，避免误伤 evacuation 之类）

### 采集流程

```
对每个关键词：
  访问 https://chatpaper.com/zh-CN/search?keywords=xxx&type=all&sort=date&page=1
  逐页翻：
    每张卡片读 Published Date：
      = 目标日期 → 进详情页 → 下 PDF → 计数 → 过滤 → 写飞书
      < 目标日期 → 停止本关键词
    遍历完本页 → 翻下一页
    最多翻 30 页保护
```

### 飞书字段映射

| 飞书字段 | 来源 |
|---|---|
| 论文 | chatpaper 中文标题 |
| 标题 | chatpaper 英文标题 |
| 机构 | 论文机构列表（多值） |
| 日期 | 卡片/详情页 Published Date |
| 作者 | 作者列表（多值） |
| arxiv | `https://arxiv.org/abs/xxxx.xxxxx` |
| project | Abstract 区域内第一个非 arxiv 外链（如 GitHub / aka.ms 等） |
| 概要 | chatpaper Core Points 面板的 tldr 总结 |
| 简介 | chatpaper 中文 Abstract |
| 图片 | AI Summary 中第一张论文配图（上传为飞书附件） |

---

## 🔧 本地开发安装

### 1. 依赖

```bash
# 需要 Python 3.10+
python -m venv venv
venv\Scripts\activate.bat      # Windows
# source venv/bin/activate     # Linux/macOS

pip install -r requirements.txt
python -m playwright install chromium
```

### 2. 配置

```bash
# 复制模板
cp config/.env.example .env
cp config/config.example.yaml config/config.yaml
```

编辑 `.env`，填入飞书应用凭证：

```
FEISHU_APP_ID=cli_xxxx
FEISHU_APP_SECRET=xxx
FEISHU_APP_TOKEN=xxx           # 如果是 Wiki 嵌套多维表，填 wiki node token
FEISHU_TABLE_ID=tblxxx
CONFIG_PATH=config/config.yaml
```

编辑 `config/config.yaml`，如果是 Wiki 嵌套多维表设 `is_wiki: true`：

```yaml
feishu:
  is_wiki: true               # Wiki 嵌套多维表 / false = 普通 base URL
  field_mapping:
    论文: "论文"
    ...
```

### 3. 飞书应用权限

在 [飞书开放平台](https://open.feishu.cn/app) 创建自建应用，开通以下权限并发布版本：

- `bitable:app` - 多维表格读写
- `drive:drive` - 云空间文件管理（图片上传）
- `wiki:wiki:readonly`（或 `wiki:wiki`）- 仅当多维表在 Wiki 里时需要

然后把应用添加到目标多维表格，授予**可编辑**权限。

---

## 🧪 测试

```bash
python tests/test_matchers.py
```

---

## ⚙️ 命令行参数

| 参数 | 说明 |
|---|---|
| `--once` | 立即执行一次（非常驻模式） |
| `--date YYYY-MM-DD` | 指定目标日期（默认北京昨日） |
| `--keywords "A,B"` | 仅跑指定关键词（逗号分隔） |
| `--dry-run` | 调试模式，不写飞书、不更新 history |

示例：

```bash
# 用 DRY-RUN 跑 2026-04-17 的 GUI Agent 试水
python -m src.main --once --date 2026-04-17 --keywords "GUI Agent" --dry-run
```

---

## 📊 报告

每次跑完自动生成：

- `data/reports/report_<target_date>.html` - 可视化报告（本地打开即看）
- `data/reports/report_<target_date>.json` - 原始数据

云端模式下：
- 每次 run 结果作为 Actions artifact 保留 7 天（可在 Actions 页面下载）
- HTML 报告同时推送到 `gh-pages` 分支
- 访问 <https://leoatsr.github.io/CUA-paper-tracker/> 查看

---

## 🛠️ 依赖

| 包 | 用途 |
|---|---|
| playwright | 浏览器自动化（采集 chatpaper） |
| pymupdf | PDF 下载与文本提取 |
| lark-oapi | 飞书官方 SDK |
| pydantic | 数据模型与校验 |
| httpx | 异步 HTTP（PDF / 图片下载） |
| loguru | 结构化日志 |
| apscheduler | 定时任务（仅本地常驻模式用） |
| pyyaml / python-dotenv | 配置加载 |

---

## 📄 License

MIT
