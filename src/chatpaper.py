"""
chatpaper.com 采集器 v2.0 - URL 搜索 + 自动翻页 + 目标日期停止

核心改动 vs v1:
- 直接构造搜索页 URL，不再依赖前端 input.fill + Enter 的不稳定交互
  URL 格式: /zh-CN/search?keywords=xxx&type=all&sort=date&page=N
- 搜索结果卡片里**直接显示日期**（span.el-tag 里的 "DD Mon YYYY"），
  不需要进详情页就能判定是否为目标日
- 逐页翻（page=1, 2, 3...），遇到日期 < 目标日的卡片即停止本关键词
- 只对"日期命中目标日"的卡片访问详情页抽取完整信息

关键词拼接规则: 空格用 +，如 "GUI Agent" → "GUI+Agent"
"""
import re
from datetime import datetime, date
from typing import Optional, AsyncIterator, List
from urllib.parse import quote_plus

from playwright.async_api import async_playwright, Page, ElementHandle
from loguru import logger

from .models import Paper


# ─────────────────────────────────────────────────────────────
# 选择器（基于 chatpaper.com 真实 DOM 验证过）
# ─────────────────────────────────────────────────────────────
SELECTORS = {
    # === 搜索结果页 ===
    # 结果页单张卡片（跟 simple 模式不同，结果页无 simple class）
    'search_card': 'div.document',
    # 卡片里的详情链接（中文标题 a 标签）
    'card_link': 'a.doc-name-content',
    # 卡片里的英文标题
    'card_title_en': 'div.doc-info-name',
    # 卡片里的所有 el-tag（含 cs.AI / cs.LG / 日期）
    'card_tags': 'span.el-tag__content',

    # === 详情页 ===
    'detail_title_zh': 'a.doc-name-content',
    'detail_title_en': 'div.doc-info-name',
    'detail_date_tags': 'div.doc-collect span.el-tag__content',
    'detail_authors': 'div.doc-author span.text-wrapper',
    'detail_organizations': 'div.doc-organization span.organization',
    'detail_abstract_zh': 'div.doc-info > div.doc-abstract',
    # Core Points 面板里的第一个 markdown-body（tldr 总结），优先用 is-tldr，
    # 没有则退回 div.extra-content 下第一个纯 class markdown-body
    'detail_core_points_primary': 'div.answer-item.is-tldr div.markdown-body',
    'detail_core_points_fallback': 'div.extra-content div.markdown-body',
    'detail_images': 'div.summary-content img',
    'detail_arxiv_link': 'a[href^="https://arxiv.org/abs/"]',
    # Project URL: Abstract 区域里所有 <a href>（先尝试 DOM 抽取）
    'detail_abstract_links': 'div.doc-abstract a[href^="http"]',
    # Project URL 兜底：直接取 Abstract 纯文本内找 URL
    'detail_abstract_all': 'div.doc-abstract',
}


class ChatPaperScraper:
    """chatpaper 异步采集器（URL 搜索 + 分页）"""

    BASE_URL = "https://chatpaper.com/zh-CN"
    # "17 Apr 2026" 格式
    DATE_PATTERN = re.compile(
        r'^\s*(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})\s*$'
    )
    MAX_PAGES = 30  # 防御：超过就停，避免无限翻

    def __init__(self, headless: bool = True, navigation_timeout_ms: int = 60000):
        self.headless = headless
        self.nav_timeout = navigation_timeout_ms
        self._pw = None
        self._browser = None
        self._context = None

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context(
            locale='zh-CN',
            viewport={'width': 1440, 'height': 900},
        )
        self._context.set_default_navigation_timeout(self.nav_timeout)
        return self

    async def __aexit__(self, *args):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    # ────────────────────────────────────────────────────
    # 核心: 按目标日期采集某关键词
    # ────────────────────────────────────────────────────

    async def collect_for_date(
        self,
        keyword: str,
        target_date: date,
        should_process_callback=None,
    ) -> AsyncIterator[Paper]:
        """
        采集某个关键词下发布日期 = target_date 的所有论文。

        Args:
            keyword: 关键词，如 "GUI Agent"
            target_date: 目标日期（卡片上显示的日期）
            should_process_callback: 可选回调 (arxiv_id_from_card) -> bool，
                返回 False 则跳过该卡片（不进详情页）。
                注意：卡片里没有 arxiv_id，只有 chatpaper 内部 detail_url，
                所以实际上去重要等进详情页拿到 arxiv_id 后才能做。这个
                回调暂时保留为接口，方便以后如果 chatpaper 卡片里加 ID。
        """
        page = await self._context.new_page()
        try:
            for page_num in range(1, self.MAX_PAGES + 1):
                url = self._build_search_url(keyword, page_num)
                logger.info(f"[{keyword}] 访问第 {page_num} 页: {url}")
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=self.nav_timeout)
                    await page.wait_for_timeout(3500)  # 等 Vue 渲染卡片
                except Exception as e:
                    logger.error(f"[{keyword}] 页面加载失败 page={page_num}: {e}")
                    break

                # 等卡片出现
                try:
                    await page.wait_for_selector(SELECTORS['search_card'], timeout=15000)
                except Exception:
                    logger.warning(f"[{keyword}] 第 {page_num} 页无卡片，结束")
                    break

                cards = await page.query_selector_all(SELECTORS['search_card'])
                if not cards:
                    logger.warning(f"[{keyword}] 第 {page_num} 页 0 个卡片，结束")
                    break

                # 抽每张卡片的日期和 detail_url
                card_metas = []
                for card in cards:
                    meta = await self._extract_card_meta(card)
                    if meta:
                        card_metas.append(meta)

                # 判断是否需要停止
                should_stop = False
                page_target_metas = []
                for meta in card_metas:
                    card_date = meta.get('date')
                    if card_date is None:
                        # 无日期卡片就跳过该卡片（可能是置顶/广告之类）
                        continue
                    if card_date == target_date:
                        page_target_metas.append(meta)
                    elif card_date < target_date:
                        # 遇到更早日期，说明本关键词下 target_date 的论文已采完
                        logger.info(
                            f"[{keyword}] 第 {page_num} 页遇到日期 {card_date} < 目标 {target_date}，停止翻页"
                        )
                        should_stop = True
                        break
                    # card_date > target_date 的卡片略过（理论上不该出现，除非排序偶有异常）

                logger.info(
                    f"[{keyword}] 第 {page_num} 页: 本页 {len(card_metas)} 张 / 命中目标日 {len(page_target_metas)} 张"
                )

                # 逐一进入详情页采集
                for meta in page_target_metas:
                    paper = await self._fetch_detail(meta)
                    if paper is None:
                        continue
                    yield paper

                if should_stop:
                    break
        finally:
            await page.close()

    @staticmethod
    def _build_search_url(keyword: str, page_num: int) -> str:
        """拼接 chatpaper 搜索 URL"""
        # chatpaper 用 + 作分词的分隔（而非 %20）
        kw_encoded = quote_plus(keyword, safe='+').replace('%20', '+').replace(' ', '+')
        return (
            f"https://chatpaper.com/zh-CN/search"
            f"?keywords={kw_encoded}&type=all&sort=date&page={page_num}"
        )

    # ────────────────────────────────────────────────────
    # 卡片/详情页解析
    # ────────────────────────────────────────────────────

    async def _extract_card_meta(self, card: ElementHandle) -> Optional[dict]:
        """从搜索结果卡片抽取: detail_url, title_zh, title_en, date"""
        try:
            link_el = await card.query_selector(SELECTORS['card_link'])
            if not link_el:
                return None
            href = await link_el.get_attribute('href') or ''
            title_zh = (await link_el.inner_text()).strip()
            if not href.startswith('http'):
                detail_url = f"https://chatpaper.com{href}" if href.startswith('/') else f"https://chatpaper.com/{href}"
            else:
                detail_url = href

            title_en_el = await card.query_selector(SELECTORS['card_title_en'])
            title_en = (await title_en_el.inner_text()).strip() if title_en_el else ''

            # 日期：在 span.el-tag__content 里找符合 DD Mon YYYY 格式的那一个
            tag_els = await card.query_selector_all(SELECTORS['card_tags'])
            card_date = None
            for t in tag_els:
                text = (await t.inner_text()).strip()
                m = self.DATE_PATTERN.match(text)
                if m:
                    day, mon, year = m.groups()
                    try:
                        card_date = datetime.strptime(f"{day} {mon} {year}", "%d %b %Y").date()
                        break
                    except ValueError:
                        continue

            return {
                'detail_url': detail_url,
                'title_zh': title_zh,
                'title_en': title_en,
                'date': card_date,
            }
        except Exception as e:
            logger.error(f"卡片元数据抽取失败: {e}")
            return None

    async def _fetch_detail(self, meta: dict) -> Optional[Paper]:
        """打开详情页，抽取 Paper 全部字段"""
        page = await self._context.new_page()
        try:
            await page.goto(meta['detail_url'], wait_until='domcontentloaded', timeout=self.nav_timeout)
            await page.wait_for_timeout(4000)

            arxiv_url, arxiv_id = await self._extract_arxiv(page)
            if not arxiv_id:
                logger.warning(f"未能从详情页提取 arXiv ID: {meta['detail_url']}")
                return None

            title_zh = await self._safe_text(page, SELECTORS['detail_title_zh']) or meta['title_zh']
            title_en = await self._safe_text(page, SELECTORS['detail_title_en']) or meta['title_en']

            paper_date = await self._extract_date(page) or meta.get('date')
            authors = await self._extract_authors(page)

            institutions = await self._collect_text_list(page, SELECTORS['detail_organizations'])
            institutions = [inst.rstrip(';').strip() for inst in institutions if inst.strip()]

            abstract_zh = await self._safe_text(page, SELECTORS['detail_abstract_zh'])
            # Core Points: 优先 is-tldr，否则 extra-content 下第一个
            core_points = await self._safe_text(page, SELECTORS['detail_core_points_primary'])
            if not core_points:
                core_points = await self._safe_text(page, SELECTORS['detail_core_points_fallback'])
            image_url = await self._extract_first_figure(page)
            project_url = await self._extract_project_url(page)

            return Paper(
                arxiv_id=arxiv_id,
                arxiv_url=arxiv_url,
                title_zh=title_zh,
                title_en=title_en,
                institutions=institutions,
                authors=authors,
                date=paper_date,
                core_points=core_points,
                abstract_zh=abstract_zh,
                image_url=image_url,
                project_url=project_url,
            )
        except Exception as e:
            logger.error(f"详情页解析失败 {meta['detail_url']}: {e}")
            return None
        finally:
            await page.close()

    # ────────────────────────────────────────────────────
    # 字段解析工具
    # ────────────────────────────────────────────────────

    async def _extract_arxiv(self, page: Page):
        try:
            a = page.locator(SELECTORS['detail_arxiv_link']).first
            if await a.count() > 0:
                url = await a.get_attribute('href') or ''
                m = re.search(r'arxiv\.org/abs/(\d{4}\.\d{4,5})', url)
                if m:
                    return url, m.group(1)
        except Exception as e:
            logger.debug(f"arXiv 链接提取失败: {e}")
        return '', None

    async def _extract_project_url(self, page: Page) -> Optional[str]:
        """
        从 Abstract 区域抽取 project_url。两层保险:
        1) 先从 <a href> 标签里找第一个非 arxiv/chatpaper 外链
        2) 如果 DOM 里没有 a 标签，从 Abstract 纯文本用正则提取第一个非 arxiv URL
        """
        # 方案 1: DOM 里的 a 标签
        try:
            links = await page.query_selector_all(SELECTORS['detail_abstract_links'])
            for a in links:
                href = await a.get_attribute('href') or ''
                if not href.startswith('http'):
                    continue
                lower = href.lower()
                if 'arxiv.org' in lower:
                    continue
                if 'chatpaper.com' in lower or 'chatdoc.com' in lower:
                    continue
                return href.rstrip('.,;)"\'')
        except Exception as e:
            logger.debug(f"project_url DOM 提取失败: {e}")

        # 方案 2: 从 Abstract 纯文本里用正则找 URL
        try:
            abstracts = await page.query_selector_all(SELECTORS['detail_abstract_all'])
            for abs_el in abstracts:
                text = (await abs_el.inner_text()).strip()
                # 匹配 https://... 或 http://...，到遇到空白/中文标点/行尾为止
                for m in re.finditer(r'https?://[^\s\u4e00-\u9fff，。；、"\'）\)]+', text):
                    url = m.group(0).rstrip('.,;)"\'')
                    lower = url.lower()
                    if 'arxiv.org' in lower:
                        continue
                    if 'chatpaper.com' in lower or 'chatdoc.com' in lower:
                        continue
                    return url
        except Exception as e:
            logger.debug(f"project_url 正则提取失败: {e}")

        return None

    async def _extract_date(self, page: Page) -> Optional[date]:
        try:
            tags = await page.query_selector_all(SELECTORS['detail_date_tags'])
            for tag in tags:
                text = (await tag.inner_text()).strip()
                m = self.DATE_PATTERN.match(text)
                if m:
                    day, mon, year = m.groups()
                    try:
                        return datetime.strptime(f"{day} {mon} {year}", "%d %b %Y").date()
                    except ValueError:
                        continue
        except Exception:
            pass
        return None

    async def _extract_authors(self, page: Page) -> List[str]:
        try:
            el = await page.query_selector(SELECTORS['detail_authors'])
            if not el:
                return []
            text = (await el.inner_text()).strip()
            text = re.sub(r'^(Authors?|作者)\s*[:：]\s*', '', text)
            return [a.strip() for a in text.split(',') if a.strip()]
        except Exception:
            return []

    async def _extract_first_figure(self, page: Page) -> Optional[str]:
        try:
            imgs = await page.query_selector_all(SELECTORS['detail_images'])
            for img in imgs:
                src = await img.get_attribute('src') or ''
                if src and ('chatdoc-arxiv' in src or 'arxiv' in src.lower()):
                    return self._absolutize_url(src)
            for img in imgs:
                src = await img.get_attribute('src') or ''
                if src and not src.startswith('data:') and 'logo' not in src.lower():
                    return self._absolutize_url(src)
        except Exception:
            pass
        return None

    @staticmethod
    async def _safe_text(page: Page, selector: str) -> str:
        try:
            el = await page.query_selector(selector)
            if el:
                return (await el.inner_text()).strip()
        except Exception:
            pass
        return ""

    @staticmethod
    async def _collect_text_list(page: Page, selector: str) -> List[str]:
        items = []
        try:
            elements = await page.query_selector_all(selector)
            seen = set()
            for el in elements:
                text = (await el.inner_text()).strip()
                if text and text not in seen:
                    items.append(text)
                    seen.add(text)
        except Exception:
            pass
        return items

    @staticmethod
    def _absolutize_url(url: str) -> str:
        if url.startswith('//'):
            return 'https:' + url
        if url.startswith('/'):
            return 'https://chatpaper.com' + url
        return url
