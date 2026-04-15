#!/usr/bin/env python3
"""
论文自动追踪与飞书录入工具
==========================
从 https://chatpaper.com/zh-CN 搜索最新论文，
在论文详情页上检查关键词频率（不下载 PDF），
将符合条件的论文自动录入飞书 Wiki。

使用方法:
    1. 安装依赖: pip install playwright requests pyyaml
    2. 安装浏览器: playwright install chromium
    3. 配置 .env 或 config.yaml
    4. 运行: python paper_tracker.py
    5. 定时: crontab -e → "0 9 * * * cd /path && python3 paper_tracker.py"
"""

import os
import re
import json
import time
import logging
import hashlib
import requests
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path

# ============================================================
# 配置加载：.env → 环境变量 → config.yaml → 默认值
# ============================================================

def _load_dotenv():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

def _load_yaml_config():
    yaml_path = Path(__file__).parent / "config.yaml"
    if yaml_path.exists():
        try:
            import yaml
            with open(yaml_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except ImportError:
            cfg, current_key, current_list = {}, None, []
            for line in yaml_path.read_text(encoding="utf-8").splitlines():
                line = line.rstrip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("  - "):
                    current_list.append(line.strip("- \"\' "))
                else:
                    if current_key and current_list:
                        cfg[current_key] = current_list
                        current_list = []
                    if ":" in line:
                        k, v = line.split(":", 1)
                        k, v = k.strip(), v.strip().strip("\"'")
                        if v:
                            try: v = int(v)
                            except ValueError: pass
                            cfg[k] = v
                        else:
                            current_key = k
            if current_key and current_list:
                cfg[current_key] = current_list
            return cfg
    return {}

_load_dotenv()
_yaml = _load_yaml_config()

CONFIG = {
    # 在 ChatPaper 搜索框中依次搜索的关键词
    "search_keywords": _yaml.get("search_keywords", [
        "GUI Agent", "CUA", "Web Agent", "mobile agent", "computer use",
    ]),
    # 在论文详情页上统计出现次数的关键词（不下载 PDF）
    "page_check_keywords": _yaml.get("page_check_keywords", [
        "web agent", "gui agent", "computer use", "mobile agent", "CUA",
    ]),
    "keyword_threshold": _yaml.get("keyword_threshold", 2),
    "days_lookback": _yaml.get("days_lookback", 1),

    # ChatPaper 站点地址
    "chatpaper_url": "https://chatpaper.com/zh-CN",

    # 飞书配置
    "feishu_app_id":     os.environ.get("FEISHU_APP_ID",     _yaml.get("feishu_app_id",     "YOUR_APP_ID")),
    "feishu_app_secret": os.environ.get("FEISHU_APP_SECRET", _yaml.get("feishu_app_secret", "YOUR_APP_SECRET")),
    "feishu_wiki_token": os.environ.get("FEISHU_WIKI_TOKEN", _yaml.get("feishu_wiki_token", "NwzAwDKTui4kPok4W0ucJocdnch")),

    "log_file":       _yaml.get("log_file",       "paper_tracker.log"),
    "processed_file": _yaml.get("processed_file", "processed_papers.json"),

    # 浏览器
    "headless":     _yaml.get("headless", True),
    "slow_mo":      _yaml.get("slow_mo", 300),
    "page_timeout": _yaml.get("page_timeout", 30000),
}

# ============================================================
# 数据模型
# ============================================================

@dataclass
class Paper:
    title_zh: str = ""
    title_en: str = ""
    authors: List[str] = field(default_factory=list)
    institution: str = ""
    date: str = ""
    arxiv_url: str = ""
    chatpaper_url: str = ""
    project_url: str = ""
    abstract: str = ""
    summary: str = ""
    keyword_count: int = 0
    categories: List[str] = field(default_factory=list)

# ============================================================
# 日志
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(CONFIG["log_file"], encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ============================================================
# ChatPaper 搜索模块（Playwright 浏览器自动化）
# ============================================================

class ChatPaperSearcher:
    """
    完整工作流（全部在 chatpaper.com/zh-CN 上完成）：
      1. 打开 chatpaper.com → 在搜索框输入关键词 → 搜索
      2. 点击 Sort by: Published Date
      3. 遍历搜索结果列表
      4. 点进每篇论文详情页 → 在页面文本中统计关键词次数（不下载 PDF）
      5. 关键词 ≥ 阈值 → 提取作者/摘要等信息 → 录入飞书
    """

    def __init__(self):
        try:
            from playwright.sync_api import sync_playwright
            self._pw_factory = sync_playwright
        except ImportError:
            logger.error(
                "❌ 请安装 Playwright:\n"
                "   pip install playwright\n"
                "   playwright install chromium"
            )
            raise

    # ── 浏览器生命周期 ──

    def start_browser(self):
        self._pw = self._pw_factory().__enter__()
        self.browser = self._pw.chromium.launch(
            headless=CONFIG["headless"],
            slow_mo=CONFIG["slow_mo"],
        )
        self.context = self.browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        self.page = self.context.new_page()
        self.page.set_default_timeout(CONFIG["page_timeout"])
        logger.info("🌐 浏览器已启动")

    def close_browser(self):
        if self.browser:
            self.browser.close()
        if self._pw:
            self._pw.__exit__(None, None, None)
        logger.info("🌐 浏览器已关闭")

    # ── 搜索单个关键词 ──

    def search_keyword(self, keyword: str) -> List[dict]:
        logger.info(f"🔍 ChatPaper 搜索: {keyword}")
        try:
            self.page.goto(CONFIG["chatpaper_url"], wait_until="networkidle")
            time.sleep(2)

            # 找搜索框
            search_input = self._find_element([
                'input[placeholder*="paper"]',
                'input[placeholder*="Paper"]',
                'input[placeholder*="搜索"]',
                'input[placeholder*="search"]',
                'input[type="search"]',
                'input[type="text"]',
                '.search-input input',
                'input.ant-input',
                'input[class*="search"]',
            ])
            if not search_input:
                logger.error("  找不到搜索框")
                return []

            search_input.click()
            search_input.fill("")
            search_input.fill(keyword)
            time.sleep(0.5)

            # 点搜索按钮 / 回车
            btn = self._find_element([
                'button:has-text("search")',
                'button:has-text("搜索")',
                'button:has-text("Search")',
                'button[type="submit"]',
                '[class*="search"] button',
            ])
            if btn:
                btn.click()
            else:
                self.page.keyboard.press("Enter")
            time.sleep(3)

            # 切换排序 → Published Date
            sort_btn = self._find_element([
                'text="Published Date"',
                'button:has-text("Published Date")',
                'span:has-text("Published Date")',
                'a:has-text("Published Date")',
            ])
            if sort_btn:
                sort_btn.click()
                logger.info("  📅 已切换排序: Published Date")
                time.sleep(2)
            else:
                logger.warning("  ⚠️ 未找到排序按钮，使用默认排序")

            # 提取结果
            results = self._extract_results()
            logger.info(f"  找到 {len(results)} 篇论文")
            return results

        except Exception as e:
            logger.error(f"  搜索失败 [{keyword}]: {e}")
            return []

    # ── 在详情页上检查关键词（不下载 PDF）──

    def check_keywords_on_page(self, url: str) -> int:
        """打开论文详情页，在网页文本中统计关键词出现次数。"""
        if not url:
            return 0
        detail = None
        try:
            detail = self.context.new_page()
            detail.set_default_timeout(CONFIG["page_timeout"])
            detail.goto(url, wait_until="networkidle")
            time.sleep(2)

            page_text = detail.inner_text("body").lower()

            total = 0
            for kw in CONFIG["page_check_keywords"]:
                count = page_text.count(kw.lower())
                if count > 0:
                    logger.debug(f"    '{kw}' × {count}")
                total += count

            detail.close()
            return total
        except Exception as e:
            logger.error(f"  详情页访问失败: {e}")
            if detail:
                try: detail.close()
                except: pass
            return 0

    # ── 从详情页提取作者 / 摘要等 ──

    def extract_details(self, url: str) -> dict:
        info = {"authors": [], "institution": "", "abstract": "", "arxiv_url": ""}
        if not url:
            return info
        detail = None
        try:
            detail = self.context.new_page()
            detail.set_default_timeout(CONFIG["page_timeout"])
            detail.goto(url, wait_until="networkidle")
            time.sleep(2)

            # arXiv 链接
            for a in detail.query_selector_all('a[href*="arxiv.org"]'):
                href = a.get_attribute("href") or ""
                if "abs" in href:
                    info["arxiv_url"] = href
                    break

            # 摘要
            for sel in ['div[class*="abstract"]', 'p[class*="abstract"]',
                        'section[class*="abstract"]', 'div[class*="summary"]', 'blockquote']:
                el = detail.query_selector(sel)
                if el:
                    txt = el.inner_text().strip()
                    if len(txt) > 50:
                        info["abstract"] = txt[:1000]
                        break

            if not info["abstract"]:
                body = detail.inner_text("body")
                for marker in ["Abstract", "摘要", "Summary"]:
                    idx = body.find(marker)
                    if idx >= 0:
                        chunk = body[idx + len(marker):idx + len(marker) + 800].strip()
                        if len(chunk) > 50:
                            info["abstract"] = chunk
                            break

            detail.close()
        except Exception as e:
            logger.debug(f"  提取详情失败: {e}")
            if detail:
                try: detail.close()
                except: pass
        return info

    # ── 主入口 ──

    def search_all_keywords(self) -> List[Paper]:
        all_papers = {}

        self.start_browser()
        try:
            # 1) 对每个关键词搜索
            for kw in CONFIG["search_keywords"]:
                for r in self.search_keyword(kw):
                    key = r.get("title_en") or r.get("title_zh") or ""
                    if not key or key in all_papers:
                        continue
                    all_papers[key] = Paper(
                        title_zh=r.get("title_zh", ""),
                        title_en=r.get("title_en", ""),
                        date=r.get("date", ""),
                        categories=r.get("categories", []),
                        chatpaper_url=r.get("chatpaper_url", ""),
                    )
                time.sleep(2)

            logger.info(f"📊 去重后共 {len(all_papers)} 篇")

            # 2) 逐篇：进详情页检查关键词（不下载 PDF）
            papers = list(all_papers.values())
            for p in papers:
                if not p.chatpaper_url:
                    continue
                logger.info(f"🔎 检查: {(p.title_en or p.title_zh)[:50]}...")
                p.keyword_count = self.check_keywords_on_page(p.chatpaper_url)
                logger.info(f"   页面关键词 × {p.keyword_count}")

                # 只为符合条件的论文提取详情（节省时间）
                if p.keyword_count >= CONFIG["keyword_threshold"]:
                    d = self.extract_details(p.chatpaper_url)
                    p.authors = d["authors"]
                    p.institution = d["institution"]
                    p.abstract = d["abstract"]
                    p.arxiv_url = d["arxiv_url"]

                time.sleep(1)

            return papers
        finally:
            self.close_browser()

    # ── 工具方法 ──

    def _find_element(self, selectors: list):
        for sel in selectors:
            try:
                el = self.page.query_selector(sel)
                if el and el.is_visible():
                    return el
            except: pass
        return None

    def _extract_results(self) -> List[dict]:
        cutoff = datetime.now() - timedelta(days=CONFIG["days_lookback"])
        papers = []

        # 尝试多种卡片选择器
        cards = []
        for sel in ['div[class*="paper"]', 'div[class*="result"]',
                     'div[class*="card"]', 'article', 'li[class*="paper"]']:
            cards = self.page.query_selector_all(sel)
            if cards:
                break

        for card in cards[:30]:
            try:
                text = card.inner_text()
                if not text or len(text.strip()) < 15:
                    continue

                lines = [l.strip() for l in text.split("\n") if l.strip()]
                date_str = self._extract_date(text)

                # 日期过滤
                if date_str:
                    try:
                        pd = datetime.strptime(date_str, "%Y-%m-%d")
                        if pd < cutoff:
                            continue
                    except ValueError:
                        pass

                # 标题
                title_zh, title_en = "", ""
                for line in lines:
                    if len(line) < 10 or re.match(r"^(cs\.|PDF|\d{4})", line):
                        continue
                    if re.search(r"[\u4e00-\u9fff]", line) and not title_zh:
                        title_zh = line
                    elif not title_en:
                        title_en = line

                if not title_en and not title_zh:
                    continue

                # 链接
                link = ""
                try:
                    a = card.query_selector("a[href]")
                    if a:
                        link = a.get_attribute("href") or ""
                        if link.startswith("/"):
                            link = f"https://chatpaper.com{link}"
                except: pass

                papers.append({
                    "title_zh": title_zh,
                    "title_en": title_en or title_zh,
                    "date": date_str,
                    "categories": re.findall(r"cs\.\w+", text),
                    "chatpaper_url": link,
                })
            except: continue

        return papers

    @staticmethod
    def _extract_date(text: str) -> str:
        for pattern, fmt in [
            (r"(\d{4}-\d{2}-\d{2})", "%Y-%m-%d"),
            (r"(\d{1,2}\s+\w{3}\s+\d{4})", "%d %b %Y"),
            (r"(\w{3}\s+\d{1,2},?\s+\d{4})", None),
        ]:
            m = re.search(pattern, text)
            if m:
                raw = m.group(1)
                if fmt:
                    try:
                        return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                    except ValueError:
                        pass
                return raw
        return ""


# ============================================================
# 飞书 API
# ============================================================

class FeishuClient:
    BASE_URL = "https://open.feishu.cn/open-apis"

    def __init__(self):
        self.app_id = CONFIG["feishu_app_id"]
        self.app_secret = CONFIG["feishu_app_secret"]
        self.token = None
        self.token_expires = 0

    def _get_tenant_token(self) -> str:
        if self.token and time.time() < self.token_expires:
            return self.token
        resp = requests.post(f"{self.BASE_URL}/auth/v3/tenant_access_token/internal", json={
            "app_id": self.app_id, "app_secret": self.app_secret,
        })
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"获取飞书 Token 失败: {data.get('msg')}")
        self.token = data["tenant_access_token"]
        self.token_expires = time.time() + data.get("expire", 7200) - 60
        return self.token

    def _headers(self):
        return {"Authorization": f"Bearer {self._get_tenant_token()}", "Content-Type": "application/json"}

    def add_paper_to_wiki(self, paper: Paper) -> bool:
        try:
            return self._add_to_bitable(paper)
        except Exception as e:
            logger.error(f"多维表格录入失败: {e}")
            try:
                return self._append_to_doc(paper)
            except Exception as e2:
                logger.error(f"文档追加也失败: {e2}")
                return False

    def _add_to_bitable(self, paper: Paper) -> bool:
        wt = CONFIG["feishu_wiki_token"]
        resp = requests.get(f"{self.BASE_URL}/wiki/v2/spaces/get_node",
                            headers=self._headers(), params={"token": wt})
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"获取 Wiki 节点失败: {data}")
        node = data["data"]["node"]
        if node["obj_type"] == "bitable":
            return self._insert_record(node["obj_token"], paper)
        return self._append_to_doc(paper)

    def _insert_record(self, app_token: str, paper: Paper) -> bool:
        resp = requests.get(f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables",
                            headers=self._headers())
        tables = resp.json().get("data", {}).get("items", [])
        if not tables:
            raise Exception("未找到多维表格")
        tid = tables[0]["table_id"]

        record = {"fields": {
            "论文": paper.title_en,
            "标题": paper.title_zh or paper.title_en,
            "机构": paper.institution,
            "日期": self._ts(paper.date),
            "作者": ", ".join(paper.authors[:5]) if paper.authors else "",
            "arxiv": {"link": paper.arxiv_url, "text": paper.arxiv_url} if paper.arxiv_url else "",
            "Project": {"link": paper.project_url, "text": paper.project_url} if paper.project_url else "",
            "概要": paper.abstract[:500] if paper.abstract else "",
            "简介": paper.summary or "",
        }}
        resp = requests.post(f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables/{tid}/records",
                             headers=self._headers(), json=record)
        result = resp.json()
        if result.get("code") == 0:
            logger.info(f"  ✅ 飞书录入成功: {paper.title_en[:50]}")
            return True
        raise Exception(f"插入失败: {result}")

    def _append_to_doc(self, paper: Paper) -> bool:
        wt = CONFIG["feishu_wiki_token"]
        body = {"element_type": 2, "children": [{"element_type": 1, "text_run": {"content":
            f"\n📄 {paper.title_en}\n中文: {paper.title_zh}\n"
            f"作者: {', '.join(paper.authors[:5])}\n日期: {paper.date}\n"
            f"机构: {paper.institution}\nArXiv: {paper.arxiv_url}\n"
            f"ChatPaper: {paper.chatpaper_url}\n摘要: {paper.abstract[:300]}\n---\n"
        }}]}
        resp = requests.post(
            f"{self.BASE_URL}/docx/v1/documents/{wt}/blocks/{wt}/children",
            headers=self._headers(), json=body)
        result = resp.json()
        if result.get("code") == 0:
            logger.info(f"  ✅ 文档追加成功: {paper.title_en[:50]}")
            return True
        raise Exception(f"追加失败: {result}")

    @staticmethod
    def _ts(date_str: str) -> int:
        for fmt in ["%Y-%m-%d", "%d %b %Y", "%b %d, %Y"]:
            try: return int(datetime.strptime(date_str, fmt).timestamp() * 1000)
            except ValueError: continue
        return int(datetime.now().timestamp() * 1000)


# ============================================================
# 去重管理
# ============================================================

class ProcessedTracker:
    def __init__(self):
        self.filepath = CONFIG["processed_file"]
        self.processed = self._load()

    def _load(self) -> set:
        if os.path.exists(self.filepath):
            with open(self.filepath, "r") as f:
                return set(json.load(f))
        return set()

    def _save(self):
        with open(self.filepath, "w") as f:
            json.dump(list(self.processed), f, indent=2)

    def is_processed(self, p: Paper) -> bool:
        return hashlib.md5((p.title_en or p.title_zh).encode()).hexdigest() in self.processed

    def mark_processed(self, p: Paper):
        self.processed.add(hashlib.md5((p.title_en or p.title_zh).encode()).hexdigest())
        self._save()


# ============================================================
# 主流程
# ============================================================

def main():
    logger.info("=" * 60)
    logger.info("🚀 论文自动追踪开始")
    logger.info(f"   日期:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"   数据源: {CONFIG['chatpaper_url']}")
    logger.info(f"   搜索:   {CONFIG['search_keywords']}")
    logger.info(f"   页面检测关键词: {CONFIG['page_check_keywords']}")
    logger.info(f"   阈值:   ≥ {CONFIG['keyword_threshold']} 次")
    logger.info(f"   范围:   最近 {CONFIG['days_lookback']} 天")
    logger.info("=" * 60)

    searcher = ChatPaperSearcher()
    feishu  = FeishuClient()
    tracker = ProcessedTracker()

    # ── 步骤 1: 在 ChatPaper 搜索 + 页面关键词检测 ──
    logger.info("\n📡 步骤1: ChatPaper 搜索 + 页面关键词检测...")
    papers = searcher.search_all_keywords()

    if not papers:
        logger.info("未找到新论文，结束。")
        return

    # ── 步骤 2: 筛选 ──
    qualified = []
    for p in papers:
        if tracker.is_processed(p):
            logger.info(f"  ⏭️ 已处理: {(p.title_en or p.title_zh)[:50]}")
            continue
        if p.keyword_count >= CONFIG["keyword_threshold"]:
            qualified.append(p)
            logger.info(f"  ✅ 符合: {p.title_en[:50]} (×{p.keyword_count})")
        else:
            logger.info(f"  ❌ 不符: {p.title_en[:50]} (×{p.keyword_count})")

    # ── 步骤 3: 录入飞书 ──
    logger.info(f"\n📝 步骤2: 录入 {len(qualified)} 篇 → 飞书...")
    ok = 0
    for p in qualified:
        try:
            if feishu.add_paper_to_wiki(p):
                tracker.mark_processed(p)
                ok += 1
        except Exception as e:
            logger.error(f"  录入失败: {e}")
        time.sleep(1)

    # ── 汇总 ──
    logger.info("\n" + "=" * 60)
    logger.info(f"🏁 完成！搜索 {len(papers)} → 符合 {len(qualified)} → 录入 {ok}")
    logger.info("=" * 60)

    if qualified:
        logger.info("\n📋 符合条件的论文:")
        for i, p in enumerate(qualified, 1):
            logger.info(f"  {i}. {p.title_en}")
            if p.title_zh: logger.info(f"     中文: {p.title_zh}")
            logger.info(f"     {p.date} | 关键词×{p.keyword_count}")
            if p.chatpaper_url: logger.info(f"     {p.chatpaper_url}")
            logger.info("")


if __name__ == "__main__":
    main()
