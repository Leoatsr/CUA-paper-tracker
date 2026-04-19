# 论文定时追踪与飞书录入系统

根据 SOP 规格实现：每日北京时间 04:00、22:00 自动从 chatpaper.com 采集 6 个关键词下过去 24 小时发布的新论文，统计 PDF 中 Web Agent / GUI Agent 出现次数，录入飞书多维表格。

---

## 1. 项目结构

```
paper_tracker/
├── requirements.txt
├── README.md
├── config/
│   ├── config.example.yaml   # 配置模板
│   └── .env.example          # 飞书凭证模板
├── data/
│   ├── history.json          # 已录入论文的 arXiv ID（自动生成）
│   └── logs/                 # 每日运行日志（自动生成）
├── src/
│   ├── __init__.py
│   ├── main.py               # 主入口
│   ├── scheduler.py          # APScheduler 定时调度
│   ├── chatpaper.py          # Playwright 采集 chatpaper.com
│   ├── pdf_analyzer.py       # PyMuPDF 下载 + 分析 arXiv PDF
│   ├── feishu.py             # 飞书多维表格录入
│   ├── dedup.py              # history_set 本地持久化
│   ├── matchers.py           # 模糊匹配规则
│   └── models.py             # Pydantic 数据模型
└── tests/
    └── test_matchers.py      # 匹配规则单元测试
```

---

## 2. 安装

```bash
# 推荐使用 Python 3.10+
pip install -r requirements.txt

# Playwright 首次使用需要下载 Chromium 二进制
python -m playwright install chromium
```

---

## 3. 配置

### 3.1 飞书应用

1. 在 [飞书开放平台](https://open.feishu.cn/) 创建 **自建应用**
2. 开通权限：
   - `bitable:app`（多维表格读写）
   - `drive:drive`（云空间上传图片）
3. 将应用添加到目标多维表格所在的空间/文档，授予编辑权限
4. 从应用详情页复制 `App ID` 和 `App Secret`

### 3.2 多维表格

打开目标多维表格，从 URL 提取两个参数：
```
https://xxx.feishu.cn/base/{FEISHU_APP_TOKEN}?table={FEISHU_TABLE_ID}&view=...
```

确保表格中已建好 10 个字段：
- 论文（文本）
- 标题（文本）
- 机构（多选）
- 日期（日期）
- 作者（多选）
- arxiv（超链接）
- project（超链接）
- 概要（多行文本）
- 简介（多行文本）
- 图片（附件）

### 3.3 写配置文件

```bash
cp config/.env.example .env
cp config/config.example.yaml config/config.yaml
```

编辑 `.env`，填入飞书凭证和表格 ID。
编辑 `config/config.yaml`，如果你的飞书字段名不是默认值就修改 `field_mapping`。

---

## 4. ⚠️ 部署前必做：调试 chatpaper 选择器

`src/chatpaper.py` 顶部 `SELECTORS` 字典里的所有 CSS 选择器都是 **占位符**。
因为 chatpaper.com 的真实 DOM 无法静态确定，需要你实际打开网站抓取一次。

### 步骤

```bash
# 以非无头模式运行，观察浏览器行为
# 将 config.yaml 中 headless: false
python -m src.main --once
```

然后：

1. 浏览器打开 https://chatpaper.com/zh-CN
2. 按 `F12` 打开 DevTools
3. 用"Select an element"工具逐一定位以下元素，复制选择器：
   - 搜索框 → `search_input`
   - Published Date 排序按钮 → `sort_published_date`
   - 论文列表卡片 → `paper_card`
   - 卡片内的中文/英文标题、日期、arXiv 链接
   - 详情页内的作者、机构、Core Points、Abstract 中文、第一张图
4. 把抓到的选择器替换到 `SELECTORS` 字典里

调试时把 `headless: false`，能直接看到浏览器的点击/输入行为。

---

## 5. 运行

```bash
# 只采集不录入（调试选择器用，不改飞书、不改 history）
python -m src.main --once --dry-run

# 立即执行一次完整流程
python -m src.main --once

# 常驻模式（生产，每日 04:00 / 22:00 北京时间触发）
python -m src.main
```

### 后台常驻（Linux，systemd）

`/etc/systemd/system/paper-tracker.service`：
```ini
[Unit]
Description=Paper Tracker
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/paper_tracker
ExecStart=/usr/bin/python3 -m src.main
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now paper-tracker
sudo journalctl -u paper-tracker -f
```

### 后台常驻（macOS / 通用）

```bash
nohup python -m src.main > data/logs/nohup.out 2>&1 &
```

---

## 6. 与 SOP 的对应关系

| SOP 章节 | 代码位置 |
|---|---|
| § 0.2 模糊匹配规则 | `src/matchers.py` |
| § 1 调度（北京时间 04:00 / 22:00） | `src/scheduler.py` |
| § 2 chatpaper 采集 + 跨关键词/跨任务去重 | `src/chatpaper.py`, `src/dedup.py`, `src/main.py` |
| § 3 PDF 计数（3 分钟超时） | `src/pdf_analyzer.py` |
| § 4 飞书录入 + arxiv 链接去重 | `src/feishu.py` |
| § 5 运行日志 | loguru 每日切片于 `data/logs/` |

---

## 7. 与 SOP 的两处工程化差异（已在代码中实现）

| SOP 原描述 | 实际实现 | 原因 |
|---|---|---|
| 在云端打开 PDF 按 Ctrl+F 检索 | 直接从 arXiv 下载 PDF，PyMuPDF 全文正则计数 | 更快、更准、更稳定，避免浏览器 PDF 查看器的懒加载问题 |
| 把第一张图复制粘贴到飞书 | 从 chatpaper 下载图片 URL → 上传为飞书附件 | 飞书 API 不支持"粘贴"操作，附件展示效果一致 |

---

## 8. 测试

```bash
python tests/test_matchers.py
```

验证模糊匹配规则（7 个测试用例，含 CUA 不误伤 evacuation 等场景）。

---

## 9. 运维建议

- **首次部署**：建议先用 `--once` 跑一次，观察日志和飞书录入效果
- **选择器失效**：chatpaper 网站改版后选择器会失效，表现为采集数量为 0，检查 `data/logs/` 里的警告
- **timeout_queue**：如果某些论文长期超时，可手动从 arXiv 下载后用 `python -c "from src.pdf_analyzer import analyze_pdf; ..."` 本地处理后入库
- **历史库迁移**：`data/history.json` 是纯 JSON 列表，可随意备份/合并

---

## 10. 依赖清单

```
playwright       — 浏览器自动化
pymupdf (fitz)   — PDF 文本提取
lark-oapi        — 飞书官方 SDK
apscheduler      — 定时任务
httpx            — 异步 HTTP（下载 PDF 和图片）
pydantic         — 数据模型
loguru           — 日志
pyyaml, python-dotenv, pytz — 配置与时区
```
