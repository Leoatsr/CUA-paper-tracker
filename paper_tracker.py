#!/usr/bin/env python3
"""
论文自动追踪与飞书录入工具
==========================
从 https://chatpaper.com/zh-CN 搜索最新论文，
在论文详情页上检查关键词频率（不下载 PDF），
将符合条件的论文自动录入飞书 Wiki。

使用方法:
    1. pip3 install selenium webdriver-manager requests pyyaml
    2. 配置 .env 或 config.yaml
    3. python3 paper_tracker.py
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
from typing import List
from pathlib import Path

# ============================================================
# 配置加载
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
    "search_keywords": _yaml.get("search_keywords", [
        "GUI Agent", "CUA", "Web Agent", "mobile agent", "computer use",
    ]),
    "page_check_keywords": _yaml.get("page_check_keywords", [
        "web agent", "gui agent", "computer use", "mobile agent", "CUA",
    ]),
    "keyword_threshold": _yaml.get("keyword_threshold", 2),
    "days_lookback": _yaml.get("days_lookback", 1),
    "chatpaper_url": "https://chatpaper.com/zh-CN",

    "feishu_app_id":     os.environ.get("FEISHU_APP_ID",     _yaml.get("feishu_app_id",     "YOUR_APP_ID")),
    "feishu_app_secret": os.environ.get("FEISHU_APP_SECRET", _yaml.get("feishu_app_secret", "YOUR_APP_SECRET")),
    "feishu_wiki_token": os.environ.get("FEISHU_WIKI_TOKEN", _yaml.get("feishu_wiki_token", "NwzAwDKTui4kPok4W0ucJocdnch")),

    "log_file":       _yaml.get("log_file",       "paper_tracker.log"),
    "processed_file": _yaml.get("processed_file", "processed_papers.json"),
    "headless":       _yaml.get("headless", False),
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
    pdf_url: str = ""
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
# ChatPaper 搜索模块（Selenium）
# ============================================================

class ChatPaperSearcher:
    def __init__(self):
        self.driver = None

    def start_browser(self):
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from webdriver_manager.chrome import ChromeDriverManager
        from selenium import webdriver

        options = Options()
        if CONFIG["headless"]:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1440,900")

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)
        self.driver.implicitly_wait(10)
        logger.info("🌐 浏览器已启动")

    def close_browser(self):
        if self.driver:
            self.driver.quit()
        logger.info("🌐 浏览器已关闭")

    def search_keyword(self, keyword: str) -> List[dict]:
        """在 ChatPaper 搜索一个关键词，智能翻页"""
        from selenium.webdriver.common.by import By

        logger.info(f"🔍 ChatPaper 搜索: {keyword}")
        all_matched = []
        # days_lookback=1 表示只看今天，=2 表示今天和昨天
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = today - timedelta(days=CONFIG["days_lookback"] - 1)
        logger.info(f"  📅 只看 {cutoff.strftime('%Y-%m-%d')} 及之后的论文")

        try:
            # 直接通过 URL 搜索+排序，不依赖 UI 交互
            encoded_kw = keyword.replace(' ', '+')
            search_url = f"https://chatpaper.com/zh-CN/search?keywords={encoded_kw}&type=all&sort=date"
            self.driver.get(search_url)
            logger.info(f"  📅 直接访问排序后的搜索 URL")
            time.sleep(6)

            base_url = self.driver.current_url

            for page in range(1, 20):  # 最多20页
                if page > 1:
                    sep = "&" if "?" in base_url else "?"
                    page_url = re.sub(r'page=\d+', f'page={page}', base_url) if "page=" in base_url else f"{base_url}{sep}page={page}"
                    self.driver.get(page_url)
                    time.sleep(5)

                body_text = self.driver.find_element(By.TAG_NAME, "body").text
                page_papers = self._parse_papers_no_date_filter(body_text)

                if not page_papers:
                    break

                # 这一页有多少篇符合日期
                matched_this_page = 0
                dated_this_page = 0
                cutoff_date = cutoff.date()
                for p in page_papers:
                    if not p.get("date"):
                        continue
                    dated_this_page += 1
                    try:
                        paper_date = datetime.strptime(p["date"], "%Y-%m-%d").date()
                        if paper_date >= cutoff_date:
                            self._extract_links_for_papers([p])
                            all_matched.append(p)
                            matched_this_page += 1
                    except ValueError:
                        continue

                logger.info(f"  📄 第{page}页: {dated_this_page} 篇有日期, 符合 {matched_this_page} 篇, 累计 {len(all_matched)} 篇")

                if dated_this_page == 0:
                    break

                # 本页全部符合 → 继续翻，本页出现旧论文 → 停止
                if matched_this_page < dated_this_page:
                    if matched_this_page > 0:
                        logger.info(f"  📅 本页已出现旧论文，停止翻页")
                    else:
                        logger.info(f"  📅 本页无符合日期的论文，停止翻页")
                    break

                time.sleep(2)

            # 最后统一提取链接
            self._extract_links_for_papers(all_matched)

            logger.info(f"  ✅ 共找到 {len(all_matched)} 篇")
            return all_matched

        except Exception as e:
            logger.error(f"  搜索失败 [{keyword}]: {e}")
            return all_matched

    def _extract_links_for_papers(self, papers):
        """从当前页面提取论文链接"""
        from selenium.webdriver.common.by import By
        try:
            links = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/paper/']")
            for a in links:
                try:
                    href = a.get_attribute("href") or ""
                    text = a.text.strip()
                    if href and text:
                        for p in papers:
                            if not p.get("chatpaper_url") and (text[:20] in p.get("title_en", "") or text[:20] in p.get("title_zh", "")):
                                p["chatpaper_url"] = href
                                break
                except:
                    continue
        except:
            pass

    def _parse_papers_no_date_filter(self, text: str) -> List[dict]:
        """解析页面文本，不做日期过滤"""
        papers = []
        chunks = re.split(r'\n\d+\.', text)

        for chunk in chunks[1:]:
            lines = [l.strip() for l in chunk.split("\n") if l.strip()]
            if len(lines) < 2:
                continue

            title_zh, title_en, date_str, institution = "", "", "", ""
            categories = []

            # 先在整个 chunk 里找日期（不要求独占一行）
            date_match = re.search(r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})', chunk)
            if date_match:
                try:
                    date_str = datetime.strptime(date_match.group(1), "%d %b %Y").strftime("%Y-%m-%d")
                except ValueError:
                    date_str = ""

            # 在整个 chunk 里找分类
            categories = re.findall(r'cs\.\w+', chunk)

            for line in lines:
                if line in ("AI Chat", "search", "track", "Sort by:", "Relevance", "Published Date", "PDF"):
                    continue

                # 跳过日期行（已经提取过了）
                if re.search(r'\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}', line):
                    continue

                # 跳过纯分类行
                clean = re.sub(r'cs\.\w+', '', line).strip()
                if not clean:
                    continue

                # 会议标签
                if re.match(r'^[A-Z]{2,10}\s+\d{4}$', line.strip()):
                    continue

                # 机构
                if ";" in line and len(line) > 20:
                    institution = line
                    continue

                # 标题
                if re.search(r'[\u4e00-\u9fff]', line) and not title_zh:
                    title_zh = line
                elif re.search(r'[a-zA-Z]', line) and len(line) > 10 and not title_en:
                    title_en = line

            if not title_en and not title_zh:
                continue

            # 跳过误识别的非论文内容
            skip_words = ["Sort by", "Relevance", "Published Date", "All Papers", "search", "track", "Update :"]
            if any(sw in (title_en or title_zh) for sw in skip_words):
                continue

            papers.append({
                "title_zh": title_zh,
                "title_en": title_en or title_zh,
                "date": date_str,
                "categories": categories,
                "institution": institution,
                "chatpaper_url": "",
                "pdf_url": "",
            })

        return papers

    # _parse_papers 已被 _parse_papers_no_date_filter 替代

    def check_keywords_on_page(self, paper_dict_or_url) -> int:
        """
        打开论文的 PDF 页面（或详情页），在页面文本中统计关键词。
        类似 Ctrl+F 搜索整篇论文。
        """
        from selenium.webdriver.common.by import By

        # 支持传入 Paper 对象或 URL 字符串
        if isinstance(paper_dict_or_url, str):
            url = paper_dict_or_url
            pdf_url = ""
        else:
            url = paper_dict_or_url
            pdf_url = ""

        if not url:
            return 0

        try:
            self.driver.execute_script("window.open('');")
            self.driver.switch_to.window(self.driver.window_handles[-1])
            self.driver.get(url)

            # 等待页面完全加载（解决网络延迟问题）
            for _ in range(15):
                time.sleep(2)
                state = self.driver.execute_script("return document.readyState")
                if state == "complete":
                    break
            time.sleep(3)  # 额外等待动态内容

            page_text = self.driver.find_element(By.TAG_NAME, "body").text.lower()

            # 如果详情页文本太少，尝试找 PDF 链接并打开
            if len(page_text) < 500:
                logger.debug("  详情页内容较少，尝试查找 PDF...")

            # 尝试在详情页找到 arXiv PDF 链接
            try:
                pdf_links = self.driver.find_elements(By.CSS_SELECTOR, 'a[href*="arxiv.org/pdf"]')
                if not pdf_links:
                    pdf_links = self.driver.find_elements(By.CSS_SELECTOR, 'a[href*="arxiv.org/abs"]')
                if pdf_links:
                    pdf_href = pdf_links[0].get_attribute("href")
                    # 转换为 PDF 链接
                    if "/abs/" in pdf_href:
                        pdf_href = pdf_href.replace("/abs/", "/pdf/")
                    if pdf_href and pdf_href != url:
                        logger.debug(f"  打开 PDF: {pdf_href}")
                        self.driver.get(pdf_href)
                        # PDF 加载需要更多时间
                        for _ in range(20):
                            time.sleep(2)
                            state = self.driver.execute_script("return document.readyState")
                            if state == "complete":
                                break
                        time.sleep(5)
                        # 获取 PDF 页面文本
                        pdf_text = self.driver.find_element(By.TAG_NAME, "body").text.lower()
                        if len(pdf_text) > len(page_text):
                            page_text = pdf_text
            except Exception as e:
                logger.debug(f"  PDF 打开失败，使用详情页文本: {e}")

            # 统计关键词
            total = 0
            for kw in CONFIG["page_check_keywords"]:
                count = page_text.count(kw.lower())
                if count > 0:
                    logger.debug(f"    '{kw}' × {count}")
                total += count

            self.driver.close()
            self.driver.switch_to.window(self.driver.window_handles[0])
            return total

        except Exception as e:
            logger.error(f"  页面访问失败: {e}")
            try:
                if len(self.driver.window_handles) > 1:
                    self.driver.close()
                    self.driver.switch_to.window(self.driver.window_handles[0])
            except:
                pass
            return 0

    def extract_details(self, url: str) -> dict:
        """提取摘要、arXiv 链接等"""
        from selenium.webdriver.common.by import By

        info = {"abstract": "", "arxiv_url": ""}
        if not url:
            return info
        try:
            self.driver.execute_script("window.open('');")
            self.driver.switch_to.window(self.driver.window_handles[-1])
            self.driver.get(url)
            time.sleep(4)

            # arXiv 链接
            for a in self.driver.find_elements(By.CSS_SELECTOR, 'a[href*="arxiv.org"]'):
                href = a.get_attribute("href") or ""
                if "abs" in href:
                    info["arxiv_url"] = href
                    break

            # 摘要
            body = self.driver.find_element(By.TAG_NAME, "body").text
            for marker in ["Abstract", "摘要"]:
                idx = body.find(marker)
                if idx >= 0:
                    chunk = body[idx + len(marker):idx + len(marker) + 800].strip()
                    if len(chunk) > 50:
                        info["abstract"] = chunk
                        break

            self.driver.close()
            self.driver.switch_to.window(self.driver.window_handles[0])
        except Exception as e:
            logger.debug(f"  提取详情失败: {e}")
            try:
                if len(self.driver.window_handles) > 1:
                    self.driver.close()
                    self.driver.switch_to.window(self.driver.window_handles[0])
            except:
                pass
        return info

    def search_all_keywords(self) -> List[Paper]:
        """搜索所有关键词 → 合并去重 → 逐篇在 PDF 中检查关键词"""
        all_papers = {}

        self.start_browser()
        try:
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
                        institution=r.get("institution", ""),
                        chatpaper_url=r.get("chatpaper_url", ""),
                        pdf_url=r.get("pdf_url", ""),
                    )
                time.sleep(2)

            logger.info(f"📊 去重后共 {len(all_papers)} 篇")

            papers = list(all_papers.values())
            for i, p in enumerate(papers):
                # 优先用 ChatPaper 详情页检查，会自动尝试找 PDF 链接
                check_url = p.chatpaper_url or p.pdf_url
                if not check_url:
                    continue
                logger.info(f"🔎 [{i+1}/{len(papers)}] 检查: {(p.title_en or p.title_zh)[:50]}...")
                p.keyword_count = self.check_keywords_on_page(check_url)
                logger.info(f"   关键词 × {p.keyword_count}")

                if p.keyword_count >= CONFIG["keyword_threshold"]:
                    d = self.extract_details(p.chatpaper_url)
                    p.abstract = d["abstract"]
                    p.arxiv_url = d["arxiv_url"]

                time.sleep(1)

            return papers
        finally:
            self.close_browser()


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

    def _get_tenant_token(self):
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

    def _add_to_bitable(self, paper):
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

    def _insert_record(self, app_token, paper):
        # 优先使用 URL 中指定的 table_id
        tid = "tbljpDAPiBSJ2HMT"

        # 机构字段是多选类型，需要拆分成列表
        institution_list = []
        if paper.institution:
            institution_list = [s.strip() for s in paper.institution.replace("；", ";").split(";") if s.strip()]

        # 作者字段也可能是多选
        author_list = []
        if paper.authors:
            author_list = [a.strip() for a in paper.authors[:5] if a.strip()]

        # 构建字段（跳过空值字段，避免格式错误）
        fields = {}
        fields["论文"] = paper.title_en
        if paper.title_zh:
            fields["标题"] = paper.title_zh
        if institution_list:
            fields["机构"] = institution_list
        if paper.date:
            fields["日期"] = self._ts(paper.date)
        if author_list:
            fields["作者"] = author_list
        if paper.arxiv_url:
            fields["arxiv"] = {"link": paper.arxiv_url, "text": paper.arxiv_url}
        if paper.chatpaper_url:
            fields["Project"] = {"link": paper.chatpaper_url, "text": paper.chatpaper_url}
        if paper.abstract:
            fields["概要"] = paper.abstract[:500]

        record = {"fields": fields}
        resp = requests.post(f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables/{tid}/records",
                             headers=self._headers(), json=record)
        result = resp.json()
        if result.get("code") == 0:
            logger.info(f"  ✅ 飞书录入成功: {paper.title_en[:50]}")
            return True
        raise Exception(f"插入失败: {result}")

    def _append_to_doc(self, paper):
        wt = CONFIG["feishu_wiki_token"]
        body = {"element_type": 2, "children": [{"element_type": 1, "text_run": {"content":
            f"\n📄 {paper.title_en}\n中文: {paper.title_zh}\n"
            f"日期: {paper.date}\n机构: {paper.institution}\n"
            f"ArXiv: {paper.arxiv_url}\nChatPaper: {paper.chatpaper_url}\n"
            f"摘要: {paper.abstract[:300]}\n---\n"
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
    def _ts(date_str):
        for fmt in ["%Y-%m-%d", "%d %b %Y"]:
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

    def _load(self):
        if os.path.exists(self.filepath):
            with open(self.filepath, "r") as f:
                return set(json.load(f))
        return set()

    def _save(self):
        with open(self.filepath, "w") as f:
            json.dump(list(self.processed), f, indent=2)

    def is_processed(self, p):
        return hashlib.md5((p.title_en or p.title_zh).encode()).hexdigest() in self.processed

    def mark_processed(self, p):
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
    logger.info(f"   阈值:   ≥ {CONFIG['keyword_threshold']} 次")
    logger.info(f"   范围:   最近 {CONFIG['days_lookback']} 天")
    logger.info("=" * 60)

    searcher = ChatPaperSearcher()
    feishu = FeishuClient()
    tracker = ProcessedTracker()

    logger.info("\n📡 步骤1: ChatPaper 搜索 + 页面关键词检测...")
    papers = searcher.search_all_keywords()

    if not papers:
        logger.info("今天没有找到符合日期范围的新论文。")
        return

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

    logger.info("\n" + "=" * 60)
    logger.info(f"🏁 完成！搜索 {len(papers)} → 符合 {len(qualified)} → 录入 {ok}")
    logger.info("=" * 60)

    if qualified:
        logger.info("\n📋 符合条件的论文:")
        for i, p in enumerate(qualified, 1):
            logger.info(f"  {i}. {p.title_en}")
            if p.title_zh: logger.info(f"     中文: {p.title_zh}")
            logger.info(f"     {p.date} | 关键词×{p.keyword_count}")
            logger.info("")

    # 生成 HTML 报告
    generate_report(papers, qualified, ok)


def generate_report(all_papers, qualified, feishu_ok):
    """生成可视化 HTML 报告，只显示符合条件的论文"""
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    report_path = Path(__file__).parent / "report.html"

    qualified_rows = ""
    for i, p in enumerate(qualified, 1):
        cats = " ".join(f'<span style="background:#1f6feb22;color:#58a6ff;padding:2px 8px;border-radius:10px;font-size:11px">{c}</span>' for c in p.categories)
        link = f'<a href="{p.chatpaper_url}" target="_blank" style="color:#58a6ff;text-decoration:none">ChatPaper</a>' if p.chatpaper_url else ""
        arxiv = f' · <a href="{p.arxiv_url}" target="_blank" style="color:#58a6ff;text-decoration:none">arXiv</a>' if p.arxiv_url else ""
        feishu_badge = '<span style="background:#23863622;color:#3fb950;padding:2px 8px;border-radius:10px;font-size:11px">已录入飞书</span>' if feishu_ok > 0 else ""
        qualified_rows += f"""
        <div style="background:#161b22;border:1px solid #238636;border-radius:10px;padding:16px 20px;margin-bottom:10px">
          <div style="font-size:15px;font-weight:600;color:#e6edf3;margin-bottom:4px">{i}. {p.title_en}</div>
          <div style="font-size:13px;color:#8b949e;margin-bottom:8px">{p.title_zh}</div>
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:12px;color:#8b949e">
            {cats}
            <span>📅 {p.date}</span>
            <span style="color:#3fb950;font-weight:600">关键词 ×{p.keyword_count}</span>
            {link}{arxiv}
            {feishu_badge}
          </div>
          <div style="font-size:12px;color:#8b949e;margin-top:6px">{p.institution}</div>
          {'<div style="font-size:12px;color:#6e7681;margin-top:8px;line-height:1.5">' + p.abstract[:200] + '...</div>' if p.abstract else ''}
        </div>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Paper Tracker 报告 - {today}</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{background:#0a0a0f;color:#e6edf3;font-family:-apple-system,'Noto Sans SC',sans-serif;padding:24px}}
  .container{{max-width:860px;margin:0 auto}}
  h1{{font-size:24px;font-weight:700;margin-bottom:6px}}
  .sub{{font-size:14px;color:#8b949e;margin-bottom:24px}}
  .stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:28px}}
  .stat{{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:16px;text-align:center}}
  .stat-num{{font-size:28px;font-weight:700}}
  .stat-label{{font-size:12px;color:#8b949e;margin-top:4px}}
  .section-title{{font-size:18px;font-weight:600;margin:28px 0 14px}}
</style></head><body>
<div class="container">
  <h1>📄 Paper Tracker 报告</h1>
  <div class="sub">{today} · chatpaper.com · 关键词 ≥{CONFIG['keyword_threshold']} 次</div>

  <div class="stats">
    <div class="stat"><div class="stat-num" style="color:#58a6ff">{len(all_papers)}</div><div class="stat-label">扫描论文总数</div></div>
    <div class="stat"><div class="stat-num" style="color:#3fb950">{len(qualified)}</div><div class="stat-label">符合条件</div></div>
    <div class="stat"><div class="stat-num" style="color:#bc8cff">{feishu_ok}</div><div class="stat-label">已录入飞书</div></div>
  </div>

  <div class="section-title">✅ 符合条件的论文（{len(qualified)} 篇）</div>
  {qualified_rows if qualified else '<div style="background:#161b22;border:1px solid #21262d;border-radius:10px;padding:24px;text-align:center;color:#8b949e">今天没有符合条件的论文</div>'}

  <div style="margin-top:24px;font-size:12px;color:#484f58;text-align:center">
    搜索关键词: {', '.join(CONFIG['search_keywords'])}<br>
    检测关键词: {', '.join(CONFIG['page_check_keywords'])}<br>
    共扫描 {len(all_papers)} 篇，{len(all_papers) - len(qualified)} 篇未检测到足够关键词
  </div>
</div></body></html>"""

    report_path.write_text(html, encoding="utf-8")
    logger.info(f"\n📊 报告已生成: {report_path}")

    import platform
    if platform.system() == "Darwin":
        os.system(f'open "{report_path}"')


if __name__ == "__main__":
    main()
