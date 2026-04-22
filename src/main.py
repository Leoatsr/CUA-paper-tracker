"""
主入口：采集 → PDF 分析 → 飞书录入 → 生成可视化报告

运行模式:
  python -m src.main                     # 常驻调度（北京时间 22:00 触发）
  python -m src.main --once              # 立即执行一次（默认采昨天北京日期的论文）
  python -m src.main --once --dry-run    # 不写飞书，调试用
  python -m src.main --once --date YYYY-MM-DD
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

import yaml
from dotenv import load_dotenv
from loguru import logger

from .chatpaper import ChatPaperScraper
from .dedup import HistoryStore
from .feishu import FeishuClient
from .matchers import PRIMARY_KEYWORDS
from .models import TaskLog, KeywordStats, PaperRecord
from .pdf_analyzer import analyze_pdf, download_pdf
from .report import write_report
from .scheduler import create_scheduler


# ─────────────────────────────────────────
# 配置加载
# ─────────────────────────────────────────

load_dotenv()
CONFIG_PATH = os.getenv('CONFIG_PATH', 'config/config.yaml')
CONFIG = yaml.safe_load(Path(CONFIG_PATH).read_text(encoding='utf-8'))

log_dir = Path(CONFIG.get('log_dir', 'data/logs'))
log_dir.mkdir(parents=True, exist_ok=True)
logger.add(
    str(log_dir / 'paper_task_{time:YYYY-MM-DD}.log'),
    rotation='00:00',
    retention='30 days',
    encoding='utf-8',
)

ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}.pdf"
BJ_TZ = timezone(timedelta(hours=8))


def default_target_dates() -> list:
    """默认目标日期 = [北京昨天, 北京前天]（从新到旧）

    两天合采的原因：chatpaper 对海外 IP 有 ~1-2 天数据延迟，
    采 today-1 保证有最新数据（海外已同步的），
    采 today-2 兜底（海外肯定已同步），共同保证不漏采。
    """
    now_bj = datetime.now(BJ_TZ)
    return [
        (now_bj - timedelta(days=1)).date(),  # 昨天
        (now_bj - timedelta(days=2)).date(),  # 前天
    ]


def parse_date_arg(arg_value: str) -> date:
    return datetime.strptime(arg_value, '%Y-%m-%d').date()


async def run_task(
    dry_run: bool = False,
    target_date=None,  # 可传 date（单日）、list[date]（多日），None → default_target_dates()
    keywords: list = None,
) -> TaskLog:
    """完整执行一次采集 → 分析 → 录入流程，结束后生成报告

    :param target_date: 单个 date、多个 date 列表，或 None 使用默认（昨天+前天）
    :param keywords: 可选，只跑指定关键词列表（不区分大小写匹配 PRIMARY_KEYWORDS）
                     传 None 则跑全部 6 个关键词
    """
    task_time_utc = datetime.now(timezone.utc)
    task_time_bj = task_time_utc.astimezone(BJ_TZ)

    # 归一化 target_date 为 list[date]
    if target_date is None:
        target_dates = default_target_dates()
    elif isinstance(target_date, date):
        target_dates = [target_date]
    else:
        target_dates = list(target_date)

    # 解析 keywords 参数
    if keywords:
        kw_lower = {k.lower() for k in keywords}
        keywords_to_run = [k for k in PRIMARY_KEYWORDS if k.lower() in kw_lower]
        missing = [k for k in keywords if k.lower() not in {x.lower() for x in PRIMARY_KEYWORDS}]
        if missing:
            logger.warning(f"未识别的关键词（已忽略）: {missing}")
    else:
        keywords_to_run = list(PRIMARY_KEYWORDS)

    logger.info("=" * 60)
    if dry_run:
        logger.warning("DRY-RUN 模式：不会写入飞书，不会更新 history_set")
    logger.info(
        f"任务开始: 北京 {task_time_bj:%Y-%m-%d %H:%M:%S} "
        f"/ UTC {task_time_utc:%Y-%m-%d %H:%M:%S}"
    )
    logger.info(f"目标采集日期: {[d.isoformat() for d in target_dates]}")
    logger.info(f"本次运行关键词: {keywords_to_run}")
    logger.info("=" * 60)

    history = HistoryStore(CONFIG.get('history_path', 'data/history.json'))
    feishu = None
    if not dry_run:
        feishu = FeishuClient(
            app_id=os.getenv('FEISHU_APP_ID'),
            app_secret=os.getenv('FEISHU_APP_SECRET'),
            app_token=os.getenv('FEISHU_APP_TOKEN'),
            table_id=os.getenv('FEISHU_TABLE_ID'),
            field_mapping=CONFIG['feishu']['field_mapping'],
            is_wiki=CONFIG['feishu'].get('is_wiki', False),
        )

    # TaskLog.target_date 取"最新的那天"用于报告标题/文件名；另有详细列表字段
    task_log = TaskLog(
        task_time_bj=task_time_bj,
        task_time_utc=task_time_utc,
        target_date=target_dates[0],
        dry_run=dry_run,
    )
    processed_set = set()

    # 从环境变量加载 ChatPaper 登录 cookies（用于绕过 AI Summary 的登录遮罩）
    chatpaper_cookies = os.getenv('CHATPAPER_COOKIES', '').strip()

    async with ChatPaperScraper(
        headless=CONFIG.get('headless', True),
        cookies_json=chatpaper_cookies or None,
    ) as scraper:
        # 记录 ChatPaper 登录状态（给报告顶部徽章用）
        task_log.chatpaper_cookies_count = scraper.cookies_injected
        task_log.chatpaper_logged_in = scraper.cookies_injected > 0

        for keyword in keywords_to_run:
            logger.info(f"━━━━━━ 关键词: {keyword} ━━━━━━")
            ks = KeywordStats(keyword=keyword)
            try:
                for td in target_dates:
                    logger.info(f"  → 采集日期 {td}")
                    async for paper in scraper.collect_for_date(keyword, td):
                        ks.cards_seen += 1

                        # 记录本关键词遍历到的首末篇（用于报告"采集范围"）
                        if ks.first_arxiv_id is None:
                            ks.first_arxiv_id = paper.arxiv_id
                            ks.first_title = paper.title_zh or paper.title_en
                        ks.last_arxiv_id = paper.arxiv_id
                        ks.last_title = paper.title_zh or paper.title_en

                        # 字段完整性日志（帮助排查概要/图片为空的 bug）
                        logger.info(
                            f"📄 采集到 {paper.arxiv_id} | "
                            f"标题={(paper.title_zh or paper.title_en)[:30]} | "
                            f"概要长度={len(paper.core_points or '')} | "
                            f"图片={'有' if paper.image_url else '无'} | "
                            f"project={'有' if paper.project_url else '无'} | "
                            f"简介长度={len(paper.abstract_zh or '')}"
                        )

                        # 跨关键词 / 跨任务去重
                        if paper.arxiv_id in processed_set or history.contains(paper.arxiv_id):
                            logger.debug(f"跳过（已处理）: {paper.arxiv_id}")
                            ks.deduped += 1
                            continue

                        processed_set.add(paper.arxiv_id)
                        paper.matched_keyword = keyword

                        # PDF 下载与分析
                        pdf_url = ARXIV_PDF_URL.format(arxiv_id=paper.arxiv_id)
                        pdf_bytes = await download_pdf(pdf_url, timeout=180)
                        if pdf_bytes is None:
                            paper.pdf_timeout = True
                            task_log.timeout_queue.append(paper.arxiv_id)
                            ks.timeout += 1
                            task_log.records.append(PaperRecord(
                                arxiv_id=paper.arxiv_id,
                                arxiv_url=paper.arxiv_url,
                                chatpaper_url=paper.chatpaper_url,
                                date=paper.date,
                                title_zh=paper.title_zh,
                                title_en=paper.title_en,
                                matched_keyword=keyword,
                                institutions=paper.institutions,
                                status='timeout',
                            ))
                            logger.warning(f"PDF 超时 → timeout_queue: {paper.arxiv_id}")
                            continue

                        try:
                            pdf_info = analyze_pdf(pdf_bytes)
                            paper.web_agent_count = pdf_info['web_agent_count']
                            paper.gui_agent_count = pdf_info['gui_agent_count']
                            if pdf_info.get('arxiv_date'):
                                paper.date = pdf_info['arxiv_date']
                            if pdf_info.get('project_url') and not paper.project_url:
                                paper.project_url = pdf_info['project_url']
                        except Exception as e:
                            logger.error(f"PDF 分析失败 {paper.arxiv_id}: {e}")
                            continue

                        # 命中过滤
                        # 命中过滤: web_agent + gui_agent 合计 < 4 则跳过（即必须 ≥ 4 才录入）
                        total_hits = paper.web_agent_count + paper.gui_agent_count
                        if total_hits < 4:
                            ks.filtered += 1
                            task_log.records.append(PaperRecord(
                                arxiv_id=paper.arxiv_id,
                                arxiv_url=paper.arxiv_url,
                                chatpaper_url=paper.chatpaper_url,
                                date=paper.date,
                                title_zh=paper.title_zh,
                                title_en=paper.title_en,
                                matched_keyword=keyword,
                                web_agent_count=paper.web_agent_count,
                                gui_agent_count=paper.gui_agent_count,
                                institutions=paper.institutions,
                                status='filtered',
                            ))
                            logger.info(
                                f"⊘ 跳过（未命中关键词）: {paper.arxiv_id} | "
                                f"web_agent={paper.web_agent_count} | "
                                f"gui_agent={paper.gui_agent_count} | "
                                f"标题={paper.title_zh[:30]}"
                            )
                            if not dry_run:
                                history.add(paper.arxiv_id)
                            continue

                        # DRY-RUN 分支
                        if dry_run:
                            ks.recorded += 1
                            task_log.papers_processed.append(paper.arxiv_id)
                            task_log.records.append(PaperRecord(
                                arxiv_id=paper.arxiv_id,
                                arxiv_url=paper.arxiv_url,
                                chatpaper_url=paper.chatpaper_url,
                                date=paper.date,
                                title_zh=paper.title_zh,
                                title_en=paper.title_en,
                                matched_keyword=keyword,
                                web_agent_count=paper.web_agent_count,
                                gui_agent_count=paper.gui_agent_count,
                                institutions=paper.institutions,
                                status='recorded',
                            ))
                            logger.success(
                                f"[DRY-RUN] ✓ {paper.arxiv_id} | "
                                f"web_agent={paper.web_agent_count} | "
                                f"gui_agent={paper.gui_agent_count} | "
                                f"命中={keyword} | "
                                f"中文标题={paper.title_zh[:40]}..."
                            )
                            continue

                        # 写飞书
                        try:
                            if feishu.exists(paper.arxiv_url):
                                task_log.feishu_skipped.append(paper.arxiv_id)
                                ks.feishu_skipped += 1
                                history.add(paper.arxiv_id)
                                task_log.records.append(PaperRecord(
                                    arxiv_id=paper.arxiv_id,
                                    arxiv_url=paper.arxiv_url,
                                    chatpaper_url=paper.chatpaper_url,
                                    date=paper.date,
                                    title_zh=paper.title_zh,
                                    title_en=paper.title_en,
                                    matched_keyword=keyword,
                                    web_agent_count=paper.web_agent_count,
                                    gui_agent_count=paper.gui_agent_count,
                                    institutions=paper.institutions,
                                    status='feishu_skipped',
                                ))
                                logger.info(f"飞书已存在，跳过: {paper.arxiv_id}")
                                continue

                            image_token = None
                            if paper.image_url:
                                image_token = await feishu.upload_image_from_url(paper.image_url)

                            feishu.insert(paper, image_token=image_token)
                            history.add(paper.arxiv_id)
                            task_log.papers_processed.append(paper.arxiv_id)
                            ks.recorded += 1
                            task_log.records.append(PaperRecord(
                                arxiv_id=paper.arxiv_id,
                                arxiv_url=paper.arxiv_url,
                                chatpaper_url=paper.chatpaper_url,
                                date=paper.date,
                                title_zh=paper.title_zh,
                                title_en=paper.title_en,
                                matched_keyword=keyword,
                                web_agent_count=paper.web_agent_count,
                                gui_agent_count=paper.gui_agent_count,
                                institutions=paper.institutions,
                                status='recorded',
                                has_core_points=bool(paper.core_points),
                                has_image=bool(image_token),
                                has_project=bool(paper.project_url),
                            ))
                            logger.success(
                                f"✓ 录入 {paper.arxiv_id} | "
                                f"web_agent={paper.web_agent_count} | "
                                f"gui_agent={paper.gui_agent_count} | "
                                f"命中={keyword}"
                            )
                            # 空字段告警（宽松版：只告 chatpaper 采到 URL 但飞书上传失败 / 预期字段为空）
                            warnings = []
                            if paper.image_url and not image_token:
                                warnings.append("图片 URL 存在但上传飞书失败")
                            if not paper.abstract_zh:
                                warnings.append("中文 Abstract 为空")
                            if not paper.title_en:
                                warnings.append("英文标题为空")
                            if task_log.chatpaper_logged_in and not paper.core_points:
                                # 登录状态下还采不到概要，值得警告
                                warnings.append("概要为空（已登录但未采到 Core Points）")
                            if warnings:
                                logger.warning(f"⚠️  {paper.arxiv_id} 字段问题: " + " | ".join(warnings))
                        except Exception as e:
                            task_log.feishu_failed.append({
                                'arxiv_id': paper.arxiv_id,
                                'error': str(e),
                            })
                            ks.feishu_failed += 1
                            task_log.records.append(PaperRecord(
                                arxiv_id=paper.arxiv_id,
                                arxiv_url=paper.arxiv_url,
                                chatpaper_url=paper.chatpaper_url,
                                date=paper.date,
                                title_zh=paper.title_zh,
                                title_en=paper.title_en,
                                matched_keyword=keyword,
                                web_agent_count=paper.web_agent_count,
                                gui_agent_count=paper.gui_agent_count,
                                institutions=paper.institutions,
                                status='feishu_failed',
                                error=str(e),
                            ))
                            logger.error(f"飞书录入失败 {paper.arxiv_id}: {e}")

            except Exception as e:
                logger.exception(f"关键词 '{keyword}' 采集异常: {e}")

            task_log.keyword_counts[keyword] = ks.recorded
            task_log.keyword_stats.append(ks)
            logger.info(
                f"关键词 '{keyword}': 见 {ks.cards_seen} | "
                f"录入 {ks.recorded} | 过滤 {ks.filtered} | "
                f"超时 {ks.timeout} | 去重 {ks.deduped}"
            )

    logger.info("=" * 60)
    logger.info(
        f"任务结束 | 目标日期 {[d.isoformat() for d in target_dates]} | "
        f"录入 {len(task_log.papers_processed)} | "
        f"超时 {len(task_log.timeout_queue)} | "
        f"跳过 {len(task_log.feishu_skipped)} | "
        f"失败 {len(task_log.feishu_failed)}"
    )
    logger.info("=" * 60)

    # 生成可视化报告
    report_dir = CONFIG.get('report_dir', 'data/reports')
    report_path = write_report(task_log, output_dir=report_dir)
    if report_path:
        logger.info(f"📊 打开报告: file:///{str(report_path.resolve()).replace(chr(92), '/')}")

    return task_log


# ─────────────────────────────────────────
# 入口
# ─────────────────────────────────────────

def parse_cli_args():
    args = {
        'once': '--once' in sys.argv,
        'dry_run': '--dry-run' in sys.argv,
        'target_date': None,
        'keywords': None,
    }
    if '--date' in sys.argv:
        i = sys.argv.index('--date')
        if i + 1 < len(sys.argv):
            try:
                args['target_date'] = parse_date_arg(sys.argv[i + 1])
            except ValueError:
                logger.error(f"--date 参数格式错误（需 YYYY-MM-DD）: {sys.argv[i + 1]}")
                sys.exit(1)
    if '--keywords' in sys.argv:
        i = sys.argv.index('--keywords')
        if i + 1 < len(sys.argv):
            # 逗号分隔的关键词列表，如 "GUI Agent,Web Agent"
            args['keywords'] = [k.strip() for k in sys.argv[i + 1].split(',') if k.strip()]
    return args


def main():
    args = parse_cli_args()
    if args['once'] or args['dry_run']:
        logger.info(
            f"单次执行模式 (dry_run={args['dry_run']}, "
            f"target_date={args['target_date'] or '默认=北京昨日'}, "
            f"keywords={args['keywords'] or '全部 6 个'})"
        )
        asyncio.run(run_task(
            dry_run=args['dry_run'],
            target_date=args['target_date'],
            keywords=args['keywords'],
        ))
        return

    logger.info("常驻调度模式 (每日北京时间 22:00 自动执行)")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    scheduler = create_scheduler(lambda: run_task(dry_run=False))
    scheduler.start()
    logger.info("调度器已启动，Ctrl+C 退出")
    try:
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        logger.info("正在停止调度器...")
        scheduler.shutdown()
        loop.close()


if __name__ == '__main__':
    main()
