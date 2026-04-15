# 📄 Paper Tracker — 论文自动追踪与飞书录入

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Scheduled](https://img.shields.io/badge/cron-daily%209AM-orange.svg)](.github/workflows/paper_track.yml)

> 每天自动从 [ChatPaper](https://chatpaper.com/zh-CN) 搜索最新论文，在论文详情页上检查关键词频率（**不下载 PDF**），将符合条件的论文自动录入飞书 Wiki。

---

## ✨ 功能

| 步骤 | 说明 |
|------|------|
| 🔍 **ChatPaper 搜索** | 在 chatpaper.com/zh-CN 搜索多个关键词，按 Published Date 排序 |
| 🔎 **页面关键词检测** | 进入论文详情页，在网页文本中统计关键词次数（不下载 PDF） |
| ✅ **飞书录入** | 自动填写 Wiki 页面：论文、标题、机构、日期、作者、arxiv 链接等 |
| 🔁 **去重机制** | 已录入论文自动跳过，不会重复处理 |
| ⏰ **定时执行** | 支持 crontab / GitHub Actions 每天自动运行 |

## 🚀 快速开始

### 1. 克隆 & 安装

```bash
git clone https://github.com/YOUR_USERNAME/paper-tracker.git
cd paper-tracker
pip install -r requirements.txt
playwright install chromium
```

### 2. 配置飞书凭证

```bash
cp .env.example .env
# 编辑 .env 填入你的飞书 App ID / Secret
```

### 3. 运行

```bash
python paper_tracker.py
```

首次运行建议关闭无头模式以便观察浏览器行为：在 `config.yaml` 中设置 `headless: false`。

## ⏰ 定时任务

### crontab（每天 9:00）

```bash
0 9 * * * cd /path/to/paper-tracker && python3 paper_tracker.py >> cron.log 2>&1
```

### GitHub Actions

仓库已包含 workflow，只需在 Settings → Secrets 添加 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_WIKI_TOKEN`。

## 🔧 工作原理

```
chatpaper.com/zh-CN
    │
    ├─ 搜索 "GUI Agent" → 按 Published Date 排序 → 论文列表
    ├─ 搜索 "CUA"        → ...
    ├─ 搜索 "Web Agent"   → ...
    │
    ▼ 合并去重
    │
    ├─ 论文1 → 打开详情页 → 页面文本统计关键词 → ×8 ≥ 2 → ✅ 录入飞书
    ├─ 论文2 → 打开详情页 → 页面文本统计关键词 → ×0 < 2 → ❌ 跳过
    └─ ...
```

关键词检测在 **论文详情网页上** 完成，不需要下载 PDF 文件。

## 🔧 飞书应用配置

1. [飞书开放平台](https://open.feishu.cn/app) → 创建企业自建应用
2. 权限：`wiki:wiki:readonly` + `bitable:app` + `docx:document`
3. 创建版本 → 申请上线
4. App ID / Secret 填入 `.env`

## 📁 项目结构

```
paper-tracker/
├── paper_tracker.py          # 主脚本（Playwright 浏览器自动化）
├── config.yaml               # 搜索关键词、阈值等配置
├── requirements.txt           # Python 依赖
├── .env.example               # 飞书凭证模板
├── .gitignore
├── LICENSE
├── README.md
└── .github/workflows/
    └── paper_track.yml        # GitHub Actions 定时任务
```

## 📝 License

[MIT](LICENSE)
