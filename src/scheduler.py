"""
定时调度

北京时间每日 01:30 触发一次（采集北京前一天、前两天两天发布的论文）。
"""
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

BJ_TZ = pytz.timezone('Asia/Shanghai')
DEFAULT_TIMES = [(1, 30)]  # 北京时间 01:30（凌晨，冷门时段延迟小）


def create_scheduler(async_job, times=None):
    """
    创建并启动异步调度器。

    :param async_job: async 函数，每次触发时被调用
    :param times: [(hour, minute), ...] 北京时间
    """
    if times is None:
        times = DEFAULT_TIMES

    scheduler = AsyncIOScheduler(timezone=BJ_TZ)
    for hour, minute in times:
        scheduler.add_job(
            async_job,
            CronTrigger(hour=hour, minute=minute, timezone=BJ_TZ),
            id=f'paper_task_{hour:02d}{minute:02d}',
            max_instances=1,  # 防止长任务重叠
            coalesce=True,    # 错过触发时只补执行一次
        )
        logger.info(f"已注册: 每日北京时间 {hour:02d}:{minute:02d}")

    return scheduler
