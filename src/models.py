"""数据模型定义"""
from datetime import date as date_type, datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class Paper(BaseModel):
    arxiv_id: str
    arxiv_url: str
    title_zh: str = ""
    title_en: str = ""
    institutions: List[str] = Field(default_factory=list)
    authors: List[str] = Field(default_factory=list)
    date: Optional[date_type] = None
    project_url: Optional[str] = None
    core_points: str = ""
    abstract_zh: str = ""
    image_url: Optional[str] = None
    chatpaper_url: Optional[str] = None
    matched_keyword: Optional[str] = None
    web_agent_count: int = 0
    gui_agent_count: int = 0
    pdf_timeout: bool = False


class PaperRecord(BaseModel):
    """报告里每条论文的明细。既能描述成功录入的，也能描述过滤/超时的。"""
    arxiv_id: str
    arxiv_url: str = ""
    chatpaper_url: Optional[str] = None
    title_zh: str = ""
    title_en: str = ""
    date: Optional[date_type] = None  # PDF 水印日期
    matched_keyword: Optional[str] = None
    web_agent_count: int = 0
    gui_agent_count: int = 0
    institutions: List[str] = Field(default_factory=list)
    status: str = "recorded"  # recorded / filtered / timeout / feishu_skipped / feishu_failed
    error: Optional[str] = None
    # 字段完整性标志（给报告 B 指标用）
    has_core_points: bool = False   # 概要是否非空
    has_image: bool = False         # 图片 token 是否获取到
    has_project: bool = False       # project URL 是否存在


class KeywordStats(BaseModel):
    """单个关键词的统计"""
    keyword: str
    pages_crawled: int = 0
    cards_seen: int = 0         # 本关键词 target_date 命中的总卡片数
    deduped: int = 0            # 被跨关键词去重过滤的数量
    recorded: int = 0           # 成功写飞书的
    filtered: int = 0           # PDF 未命中过滤掉
    timeout: int = 0            # PDF 下载超时
    feishu_skipped: int = 0     # 飞书里已存在
    feishu_failed: int = 0      # 写飞书报错
    # 报告"采集范围": 记录本关键词在 chatpaper 上实际遍历到的第一篇和最后一篇
    first_arxiv_id: Optional[str] = None
    first_title: Optional[str] = None
    last_arxiv_id: Optional[str] = None
    last_title: Optional[str] = None
    # arxiv 兜底相关
    arxiv_fallback_triggered: bool = False  # 是否触发了 arxiv 兜底
    arxiv_fallback_recorded: int = 0        # 兜底找到并录入飞书的数


class TaskLog(BaseModel):
    """单次任务运行日志（给报告用）"""
    task_time_bj: datetime
    task_time_utc: datetime
    target_date: Optional[date_type] = None
    dry_run: bool = False

    # ChatPaper 登录状态（给报告顶部徽章用）
    chatpaper_logged_in: bool = False
    chatpaper_cookies_count: int = 0

    # 统计
    keyword_counts: Dict[str, int] = Field(default_factory=dict)
    keyword_stats: List[KeywordStats] = Field(default_factory=list)

    # 明细
    papers_processed: List[str] = Field(default_factory=list)   # 成功录入的 arxiv_id
    timeout_queue: List[str] = Field(default_factory=list)
    feishu_skipped: List[str] = Field(default_factory=list)
    feishu_failed: List[Dict[str, Any]] = Field(default_factory=list)

    # 全部论文记录（含录入/过滤/超时的每一篇，按顺序）
    records: List[PaperRecord] = Field(default_factory=list)
