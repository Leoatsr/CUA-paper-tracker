# 📄 CUA Paper Tracker — 论文自动追踪与飞书录入

每天自动从 [ChatPaper](https://chatpaper.com/zh-CN) 搜索最新论文，在详情页检查关键词频率，将符合条件的论文自动录入飞书 Wiki。

## ✨ 功能

| 步骤 | 说明 |
|------|------|
| 🔍 ChatPaper 搜索 | 搜索 GUI Agent / CUA / Web Agent 等关键词，按 Published Date 排序，自动翻页 |
| 🔎 关键词检测 | 打开论文详情页，统计 web agent / gui agent 等关键词出现次数 |
| ✅ 飞书录入 | 自动填写 Wiki 多维表格：论文、标题、机构、作者、日期、arxiv、Project、概要、简介 |
| ⏰ 每天自动执行 | GitHub Actions 每天北京时间 6:00 自动运行，无需开电脑 |
| 📊 HTML 报告 | 每次运行生成可视化报告，可在 Actions Artifacts 下载 |

## 🚀 工作原理
## 📦 安装（本地运行）

```bash
git clone https://github.com/Leoatsr/CUA-paper-tracker.git
cd CUA-paper-tracker
pip3 install selenium webdriver-manager requests pyyaml
cp .env.example .env  # 编辑填入飞书凭证
python3 paper_tracker.py
```

## ☁️ 云端自动执行（GitHub Actions）

已配置 `.github/workflows/daily.yml`，每天北京时间 6:00 自动运行。

需要在仓库 Settings → Secrets → Actions 添加：
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_WIKI_TOKEN`

运行结果可在 Actions → Artifacts 下载 report.html 查看。

## ⚙️ 配置

编辑 `config.yaml`：

```yaml
search_keywords:          # ChatPaper 搜索关键词
  - "GUI Agent"
  - "CUA"
  - "Web Agent"
  - "mobile agent"
  - "computer use"

page_check_keywords:      # 详情页检测关键词
  - "web agent"
  - "gui agent"

keyword_threshold: 2      # 关键词出现次数阈值
days_lookback: 1          # 只看今天的论文
headless: true            # 无头模式（不弹浏览器）
```

## 📁 项目结构
## 📝 License

MIT
