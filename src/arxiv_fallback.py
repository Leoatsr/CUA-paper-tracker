"""
arxiv 官方 API 兜底搜索

使用场景：chatpaper 海外视角下某关键词、某日期 0 命中 + 翻够 N 页，
说明 chatpaper 那块数据缺失。回落到 arxiv 官方 API 直接查。

arxiv API 优势：
- 不分 IP，海外能查任意日期任意关键词
- 完整 Abstract / 作者 / 发布日期 / arxiv_id / PDF 链接
- 但没有 chatpaper 的中文翻译标题和 AI 概要
"""
from datetime import date, datetime, timedelta
from typing import List, Optional

import httpx
from loguru import logger
from xml.etree import ElementTree as ET

ARXIV_API = "http://export.arxiv.org/api/query"
NS = {
    'a': 'http://www.w3.org/2005/Atom',
    'arxiv': 'http://arxiv.org/schemas/atom',
}


class ArxivPaper:
    """arxiv 兜底返回的论文（精简结构，对齐 Paper model 但只有 arxiv 能给的字段）"""
    def __init__(self, arxiv_id: str, title_en: str, abstract_en: str,
                 authors: List[str], publish_date: date, pdf_url: str,
                 arxiv_url: str):
        self.arxiv_id = arxiv_id
        self.title_en = title_en
        self.abstract_en = abstract_en
        self.authors = authors
        self.publish_date = publish_date
        self.pdf_url = pdf_url
        self.arxiv_url = arxiv_url


async def search_arxiv(
    keyword: str,
    target_date: date,
    max_results: int = 50,
) -> List[ArxivPaper]:
    """
    arxiv API 按关键词查论文，过滤出发布日期 == target_date 的。

    Args:
        keyword: 关键词，如 "GUI Agent" / "Web Agent"
        target_date: 发布日期（按 arxiv 的 published 字段）
        max_results: 单次请求拉多少条（默认 50，防 API 限速）

    Returns:
        发布日期等于 target_date 的论文列表
    """
    # arxiv API 用 search_query 字段，all: 表示在标题/摘要/全文里搜
    # 加上日期范围限定 [start TO end]，缩小返回结果
    # 日期范围多给 1 天容错（时区转换可能差 1 天）
    date_start = target_date - timedelta(days=1)
    date_end = target_date + timedelta(days=1)
    date_query = (
        f'submittedDate:'
        f'[{date_start.strftime("%Y%m%d")}0000+TO+'
        f'{date_end.strftime("%Y%m%d")}2359]'
    )
    keyword_query = f'all:"{keyword}"'
    full_query = f'{keyword_query}+AND+{date_query}'

    params = {
        'search_query': full_query,
        'sortBy': 'submittedDate',
        'sortOrder': 'descending',
        'max_results': max_results,
    }

    logger.info(f"[arxiv兜底] 查询关键词 '{keyword}' 日期 {target_date}")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            # arxiv API 对 + 是 AND，不能再 url-encode
            url = ARXIV_API + '?' + '&'.join(
                f"{k}={v}" if k == 'search_query' else f"{k}={v}"
                for k, v in params.items()
            )
            resp = await client.get(url)
            resp.raise_for_status()
            xml_text = resp.text
    except Exception as e:
        logger.error(f"[arxiv兜底] API 请求失败: {e}")
        return []

    # 解析 Atom XML
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error(f"[arxiv兜底] XML 解析失败: {e}")
        return []

    papers = []
    for entry in root.findall('a:entry', NS):
        try:
            paper = _parse_entry(entry, target_date)
            if paper:
                papers.append(paper)
        except Exception as e:
            logger.debug(f"[arxiv兜底] 跳过无法解析的 entry: {e}")
            continue

    logger.info(f"[arxiv兜底] '{keyword}' {target_date} 找到 {len(papers)} 篇")
    return papers


def _parse_entry(entry, target_date: date) -> Optional[ArxivPaper]:
    """从 atom entry 解析出一篇论文，过滤掉发布日期不匹配的"""
    # 发布日期：arxiv 的 published 字段是 ISO 时间，取日期部分
    published_el = entry.find('a:published', NS)
    if published_el is None:
        return None
    pub_str = published_el.text  # e.g. "2026-04-09T15:23:45Z"
    try:
        publish_dt = datetime.fromisoformat(pub_str.replace('Z', '+00:00'))
        publish_date = publish_dt.date()
    except ValueError:
        return None

    # 严格按 target_date 过滤
    if publish_date != target_date:
        return None

    # arxiv_id 从 id 链接提取（最后一段）
    id_el = entry.find('a:id', NS)
    if id_el is None:
        return None
    arxiv_url = id_el.text  # http://arxiv.org/abs/2401.12345v1
    # 去掉 v1 / v2 等版本后缀
    arxiv_id = arxiv_url.rsplit('/', 1)[-1]
    arxiv_id = arxiv_id.split('v')[0]

    title_el = entry.find('a:title', NS)
    title_en = (title_el.text or '').strip().replace('\n', ' ').replace('  ', ' ') if title_el is not None else ''

    summary_el = entry.find('a:summary', NS)
    abstract_en = (summary_el.text or '').strip().replace('\n', ' ').replace('  ', ' ') if summary_el is not None else ''

    authors = []
    for author in entry.findall('a:author', NS):
        name_el = author.find('a:name', NS)
        if name_el is not None and name_el.text:
            authors.append(name_el.text.strip())

    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

    return ArxivPaper(
        arxiv_id=arxiv_id,
        title_en=title_en,
        abstract_en=abstract_en,
        authors=authors,
        publish_date=publish_date,
        pdf_url=pdf_url,
        arxiv_url=arxiv_url,
    )
