"""
PDF 下载与分析

核心工作流：
1. 从 arXiv 直接下载 PDF（3 分钟超时）
2. PyMuPDF 提取全文
3. 计数 Web Agent 类、GUI Agent 类
4. 从第一页提取 arXiv 水印日期（末尾日期）
5. 从第一页提取 project_url（按 SOP 优先级）
"""
import re
import asyncio
from datetime import date, datetime
from typing import Optional, Dict, Any

import fitz  # PyMuPDF
import httpx
from loguru import logger

from .matchers import count_web_agent, count_gui_agent

PDF_LOAD_TIMEOUT = 180  # 3 分钟（对齐 SOP）


async def download_pdf(url: str, timeout: int = PDF_LOAD_TIMEOUT) -> Optional[bytes]:
    """下载 PDF 字节。超时或错误返回 None。"""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except (httpx.TimeoutException, asyncio.TimeoutError):
        logger.warning(f"PDF 下载超时: {url}")
        return None
    except Exception as e:
        logger.error(f"PDF 下载失败 {url}: {e}")
        return None


def analyze_pdf(pdf_bytes: bytes) -> Dict[str, Any]:
    """
    分析 PDF：全文计数、首页元信息提取
    返回: {
        'web_agent_count': int,
        'gui_agent_count': int,
        'arxiv_date': date | None,
        'project_url': str | None,
        'page_count': int,
    }
    """
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        full_text = ''.join(page.get_text() for page in doc)
        first_page_text = doc[0].get_text() if len(doc) > 0 else ''

        return {
            'web_agent_count': count_web_agent(full_text),
            'gui_agent_count': count_gui_agent(full_text),
            'arxiv_date': extract_arxiv_date(first_page_text),
            'project_url': extract_project_url(first_page_text),
            'page_count': len(doc),
        }
    finally:
        doc.close()


def extract_arxiv_date(text: str) -> Optional[date]:
    """
    从 arXiv 左侧水印提取日期，取末尾的 DD Mon YYYY。
    水印形如: arXiv:2401.12345v1 [cs.AI] 23 Jan 2024
    """
    patterns = [
        # 完整水印
        r'arXiv:\s*[\d.]+(?:v\d+)?\s*\[[^\]]+\]\s*(\d{1,2}\s+\w{3,9}\s+\d{4})',
        # 回退：仅 [cat] DD Mon YYYY
        r'\[[^\]]+\]\s*(\d{1,2}\s+\w{3,9}\s+\d{4})',
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            date_str = m.group(1).strip()
            for fmt in ('%d %b %Y', '%d %B %Y'):
                try:
                    return datetime.strptime(date_str, fmt).date()
                except ValueError:
                    continue
    return None


def extract_project_url(text: str) -> Optional[str]:
    """
    按 SOP 优先级提取项目链接：
      1. Project page:
      2. Code:
      3. Website:
    """
    patterns = [
        (1, r'[Pp]roject\s*(?:page|website|homepage)?\s*[:：]\s*(https?://\S+)'),
        (2, r'\b[Cc]ode\s*[:：]\s*(https?://\S+)'),
        (3, r'\b[Ww]ebsite\s*[:：]\s*(https?://\S+)'),
    ]
    candidates = []
    for priority, pattern in patterns:
        for m in re.finditer(pattern, text):
            url = m.group(1).rstrip('.,;)"\'')
            candidates.append((priority, url))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def extract_largest_image(pdf_bytes: bytes, min_dim: int = 200) -> Optional[bytes]:
    """
    从 PDF 抽取尺寸最大的一张栅格图（PNG bytes），通常对应论文的"架构图/主图"。

    启发式：
    - 跳过宽或高 < min_dim 的图（小图标、装饰、公式截图等）
    - 取面积最大的那张
    - 返回 PNG 格式的 bytes，方便上传飞书
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    except Exception as e:
        logger.warning(f"PDF 打不开（提图失败）: {e}")
        return None

    try:
        best = None  # (area, png_bytes)
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            # get_images 返回 (xref, smask, w, h, bpc, colorspace, ...)
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                w = img_info[2]
                h = img_info[3]
                if w < min_dim or h < min_dim:
                    continue
                area = w * h
                if best is not None and area <= best[0]:
                    continue
                # 把这张图取出来转 PNG
                try:
                    pix = fitz.Pixmap(doc, xref)
                    # 如果是 CMYK 之类，转换到 RGB
                    if pix.n - pix.alpha >= 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    png_bytes = pix.tobytes('png')
                    pix = None  # 释放
                    best = (area, png_bytes)
                except Exception as e:
                    logger.debug(f"PDF 第 {page_idx+1} 页图片 xref={xref} 提取失败: {e}")
                    continue

        if best is None:
            return None
        return best[1]
    finally:
        doc.close()
