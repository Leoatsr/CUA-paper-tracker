"""
任务报告生成器

产出:
- data/reports/report_<target_date>.html   可视化中文仪表盘（自包含 HTML）
- data/reports/report_<target_date>.json   原始 TaskLog 数据（给以后扩展用）

用法:
    from src.report import write_report
    write_report(task_log, output_dir='data/reports')

风格: B - 中文仪表盘风，顶部大数字卡片 + Chart.js 柱状图
"""
import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from .models import TaskLog


# Chart.js CDN（国内备用镜像）
CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"


def _serialize_task_log(task_log: TaskLog) -> dict:
    """TaskLog → 可 JSON 序列化的 dict"""
    return json.loads(task_log.model_dump_json())


def _html_escape(text: str) -> str:
    """HTML 转义"""
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _render_html(task_log: TaskLog) -> str:
    """把 TaskLog 渲染成 HTML"""
    target_date_str = (
        task_log.target_date.isoformat() if task_log.target_date else "未设置"
    )
    task_time_bj_str = task_log.task_time_bj.strftime("%Y-%m-%d %H:%M:%S")

    # 汇总统计
    total_seen = sum(k.cards_seen for k in task_log.keyword_stats)
    total_recorded = len(task_log.papers_processed)
    total_filtered = sum(k.filtered for k in task_log.keyword_stats)
    total_timeout = len(task_log.timeout_queue)
    total_feishu_skipped = len(task_log.feishu_skipped)
    total_feishu_failed = len(task_log.feishu_failed)

    # 关键词统计（供 Chart.js 使用）
    kw_labels = [ks.keyword for ks in task_log.keyword_stats]
    kw_recorded = [ks.recorded for ks in task_log.keyword_stats]
    kw_filtered = [ks.filtered for ks in task_log.keyword_stats]
    kw_timeout = [ks.timeout for ks in task_log.keyword_stats]

    # 录入明细（按关键词分组）
    recorded_records = [r for r in task_log.records if r.status == "recorded"]

    # 其他明细
    filtered_records = [r for r in task_log.records if r.status == "filtered"]
    timeout_records = [r for r in task_log.records if r.status == "timeout"]
    failed_records = [r for r in task_log.records if r.status == "feishu_failed"]

    # ─── 构建 HTML ───
    html_parts = []
    html_parts.append(f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>论文追踪报告 - {target_date_str}</title>
<script src="{CHART_JS_CDN}"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: linear-gradient(180deg, #f6f8fd 0%, #eef1f9 100%);
    color: #2d3748;
    min-height: 100vh;
    padding: 24px;
  }}
  .container {{ max-width: 1280px; margin: 0 auto; }}

  /* 顶部 Header */
  .header {{
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 28px 32px;
    border-radius: 16px;
    box-shadow: 0 10px 30px rgba(102, 126, 234, 0.25);
    margin-bottom: 24px;
  }}
  .header h1 {{ font-size: 24px; margin-bottom: 6px; font-weight: 700; }}
  .header .subtitle {{ font-size: 14px; opacity: 0.9; }}
  .header .dry-run-badge {{
    display: inline-block;
    background: #fbbf24;
    color: #1f2937;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 12px;
    margin-left: 8px;
    font-weight: 600;
  }}

  /* 顶部大卡片 */
  .stat-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }}
  .stat-card {{
    background: white;
    padding: 20px;
    border-radius: 14px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
    position: relative;
    overflow: hidden;
    transition: transform 0.2s;
  }}
  .stat-card:hover {{ transform: translateY(-2px); }}
  .stat-card .label {{
    font-size: 13px;
    color: #64748b;
    margin-bottom: 8px;
    font-weight: 500;
  }}
  .stat-card .value {{
    font-size: 32px;
    font-weight: 700;
    line-height: 1.1;
  }}
  .stat-card .accent {{
    position: absolute;
    top: 0; right: 0;
    width: 60px; height: 60px;
    border-radius: 0 14px 0 60px;
    opacity: 0.12;
  }}
  .stat-card.primary .value {{ color: #6366f1; }}
  .stat-card.primary .accent {{ background: #6366f1; }}
  .stat-card.success .value {{ color: #10b981; }}
  .stat-card.success .accent {{ background: #10b981; }}
  .stat-card.warn .value {{ color: #f59e0b; }}
  .stat-card.warn .accent {{ background: #f59e0b; }}
  .stat-card.danger .value {{ color: #ef4444; }}
  .stat-card.danger .accent {{ background: #ef4444; }}
  .stat-card.gray .value {{ color: #64748b; }}
  .stat-card.gray .accent {{ background: #64748b; }}

  /* 区块 */
  .section {{
    background: white;
    border-radius: 14px;
    padding: 24px;
    margin-bottom: 24px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
  }}
  .section h2 {{
    font-size: 17px;
    margin-bottom: 16px;
    color: #1e293b;
    border-left: 4px solid #6366f1;
    padding-left: 12px;
  }}

  /* Chart */
  .chart-wrapper {{ position: relative; height: 340px; }}

  /* 表格 */
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  thead th {{
    background: #f1f5f9;
    padding: 10px 12px;
    text-align: left;
    color: #475569;
    font-weight: 600;
    border-bottom: 2px solid #e2e8f0;
  }}
  tbody td {{
    padding: 10px 12px;
    border-bottom: 1px solid #f1f5f9;
    vertical-align: top;
  }}
  tbody tr:hover {{ background: #f8fafc; }}
  .arxiv-id {{ font-family: "SF Mono", Consolas, monospace; color: #6366f1; }}
  .title-zh {{ font-weight: 500; color: #1e293b; max-width: 300px; }}
  .title-en {{ font-size: 12px; color: #64748b; margin-top: 2px; max-width: 300px; }}
  .keyword-tag {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    background: #ede9fe;
    color: #6d28d9;
    font-weight: 500;
  }}
  .count-badge {{
    display: inline-block;
    min-width: 32px;
    padding: 2px 6px;
    text-align: center;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 600;
  }}
  .count-web {{ background: #dbeafe; color: #1d4ed8; }}
  .count-gui {{ background: #dcfce7; color: #15803d; }}
  .institutions {{ font-size: 12px; color: #64748b; max-width: 240px; }}
  .link-btn {{
    color: #6366f1;
    text-decoration: none;
    font-size: 12px;
    font-weight: 500;
  }}
  .link-btn:hover {{ text-decoration: underline; }}

  /* 空状态 */
  .empty {{
    text-align: center;
    padding: 32px;
    color: #94a3b8;
    font-size: 13px;
  }}

  /* 错误消息 */
  .error-box {{
    background: #fef2f2;
    border: 1px solid #fecaca;
    color: #991b1b;
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 12px;
    font-family: "SF Mono", Consolas, monospace;
    margin-top: 4px;
  }}

  /* 折叠表格 */
  details {{ margin-top: 8px; }}
  details summary {{
    cursor: pointer;
    padding: 10px 14px;
    background: #f8fafc;
    border-radius: 8px;
    font-size: 13px;
    color: #475569;
    list-style: none;
    user-select: none;
  }}
  details summary::before {{ content: "▶ "; font-size: 10px; color: #94a3b8; }}
  details[open] summary::before {{ content: "▼ "; }}
  details[open] summary {{ background: #ede9fe; color: #6d28d9; }}
</style>
</head>
<body>
<div class="container">

  <!-- Header -->
  <div class="header">
    <h1>📑 论文追踪报告
      {'<span class="dry-run-badge">DRY-RUN 模式</span>' if task_log.dry_run else ''}
    </h1>
    <div class="subtitle">
      目标采集日期 <strong>{target_date_str}</strong>
      · 执行时间 {task_time_bj_str} (北京时间)
    </div>
  </div>

  <!-- 统计卡片 -->
  <div class="stat-grid">
    <div class="stat-card primary">
      <div class="accent"></div>
      <div class="label">采集总数</div>
      <div class="value">{total_seen}</div>
    </div>
    <div class="stat-card success">
      <div class="accent"></div>
      <div class="label">命中录入</div>
      <div class="value">{total_recorded}</div>
    </div>
    <div class="stat-card gray">
      <div class="accent"></div>
      <div class="label">未命中过滤</div>
      <div class="value">{total_filtered}</div>
    </div>
    <div class="stat-card warn">
      <div class="accent"></div>
      <div class="label">PDF 超时</div>
      <div class="value">{total_timeout}</div>
    </div>
    <div class="stat-card gray">
      <div class="accent"></div>
      <div class="label">飞书已有</div>
      <div class="value">{total_feishu_skipped}</div>
    </div>
    <div class="stat-card danger">
      <div class="accent"></div>
      <div class="label">飞书失败</div>
      <div class="value">{total_feishu_failed}</div>
    </div>
  </div>

  <!-- Chart -->
  <div class="section">
    <h2>关键词分布</h2>
    <div class="chart-wrapper">
      <canvas id="kwChart"></canvas>
    </div>
  </div>
""")

    # 采集范围：每个关键词在 chatpaper 上遍历到的首末篇
    range_rows = []
    for ks in task_log.keyword_stats:
        if not ks.first_arxiv_id and not ks.last_arxiv_id:
            continue
        range_rows.append(ks)

    if range_rows:
        html_parts.append('''
  <!-- 采集范围 -->
  <div class="section">
    <h2>🧭 本次采集范围（每个关键词首末篇）</h2>
    <table>
      <thead>
        <tr>
          <th style="width:140px">关键词</th>
          <th>第一篇</th>
          <th>最后一篇</th>
          <th style="width:80px">本关键词总见</th>
        </tr>
      </thead>
      <tbody>
''')
        for ks in range_rows:
            first_cell = (
                f'<span class="arxiv-id">{_html_escape(ks.first_arxiv_id or "")}</span>'
                f'<div class="title-en" style="margin-top:4px">{_html_escape((ks.first_title or "")[:80])}</div>'
                if ks.first_arxiv_id else '<span style="color:#cbd5e1">—</span>'
            )
            last_cell = (
                f'<span class="arxiv-id">{_html_escape(ks.last_arxiv_id or "")}</span>'
                f'<div class="title-en" style="margin-top:4px">{_html_escape((ks.last_title or "")[:80])}</div>'
                if ks.last_arxiv_id else '<span style="color:#cbd5e1">—</span>'
            )
            html_parts.append(f'''
        <tr>
          <td><span class="keyword-tag">{_html_escape(ks.keyword)}</span></td>
          <td>{first_cell}</td>
          <td>{last_cell}</td>
          <td style="text-align:center">{ks.cards_seen}</td>
        </tr>
''')
        html_parts.append('      </tbody>\n    </table>\n  </div>\n')

    # 录入明细
    html_parts.append(f'''
  <!-- 录入明细 -->
  <div class="section">
    <h2>✓ 录入明细 ({len(recorded_records)} 篇)</h2>
''')
    if recorded_records:
        html_parts.append('''
    <table>
      <thead>
        <tr>
          <th>arXiv</th>
          <th>标题</th>
          <th>发布日期</th>
          <th>命中</th>
          <th>web_agent</th>
          <th>gui_agent</th>
          <th>机构</th>
          <th style="width:90px">链接</th>
        </tr>
      </thead>
      <tbody>
''')
        for r in recorded_records:
            date_str = r.date.isoformat() if r.date else "-"
            html_parts.append(f'''
        <tr>
          <td class="arxiv-id">{_html_escape(r.arxiv_id)}</td>
          <td>
            <div class="title-zh">{_html_escape(r.title_zh)}</div>
            <div class="title-en">{_html_escape(r.title_en)}</div>
          </td>
          <td style="white-space:nowrap;color:#64748b;font-size:12px">{_html_escape(date_str)}</td>
          <td><span class="keyword-tag">{_html_escape(r.matched_keyword or "")}</span></td>
          <td><span class="count-badge count-web">{r.web_agent_count}</span></td>
          <td><span class="count-badge count-gui">{r.gui_agent_count}</span></td>
          <td class="institutions">{_html_escape("; ".join(r.institutions))}</td>
          <td><a class="link-btn" href="{_html_escape(r.chatpaper_url or r.arxiv_url)}" target="_blank">ChatPaper →</a></td>
        </tr>
''')
        html_parts.append('      </tbody>\n    </table>\n')
    else:
        html_parts.append('    <div class="empty">今日没有论文命中关键词</div>\n')
    html_parts.append('  </div>\n')

    # 过滤明细（折叠）
    if filtered_records:
        html_parts.append(f'''
  <!-- 过滤明细（折叠）-->
  <div class="section">
    <h2>⊘ 未命中过滤 ({len(filtered_records)} 篇)</h2>
    <details>
      <summary>展开查看全部 {len(filtered_records)} 篇未命中关键词的论文</summary>
      <table style="margin-top:12px">
        <thead><tr><th style="width:140px">arXiv</th><th>Title</th></tr></thead>
        <tbody>
''')
        for r in filtered_records:
            arxiv_link = r.arxiv_url or (f"https://arxiv.org/abs/{r.arxiv_id}" if r.arxiv_id else "")
            title_to_show = r.title_en or r.title_zh
            html_parts.append(f'''
          <tr>
            <td><a class="arxiv-id link-btn" href="{_html_escape(arxiv_link)}" target="_blank">{_html_escape(r.arxiv_id)}</a></td>
            <td><div class="title-zh">{_html_escape(title_to_show)}</div></td>
          </tr>
''')
        html_parts.append('        </tbody>\n      </table>\n    </details>\n  </div>\n')

    # 超时队列
    if timeout_records:
        html_parts.append(f'''
  <!-- 超时队列 -->
  <div class="section" style="background:#fffbeb">
    <h2 style="border-left-color:#f59e0b">⌛ PDF 下载超时 ({len(timeout_records)} 篇)</h2>
    <table>
      <thead><tr><th>arXiv</th><th>标题</th><th>命中关键词</th></tr></thead>
      <tbody>
''')
        for r in timeout_records:
            html_parts.append(f'''
        <tr>
          <td class="arxiv-id">{_html_escape(r.arxiv_id)}</td>
          <td><div class="title-zh">{_html_escape(r.title_zh)}</div></td>
          <td><span class="keyword-tag">{_html_escape(r.matched_keyword or "")}</span></td>
        </tr>
''')
        html_parts.append('      </tbody>\n    </table>\n  </div>\n')

    # 飞书失败
    if failed_records:
        html_parts.append(f'''
  <!-- 飞书失败 -->
  <div class="section" style="background:#fef2f2">
    <h2 style="border-left-color:#ef4444">✕ 飞书写入失败 ({len(failed_records)} 篇)</h2>
    <table>
      <thead><tr><th>arXiv</th><th>标题</th><th>错误信息</th></tr></thead>
      <tbody>
''')
        for r in failed_records:
            html_parts.append(f'''
        <tr>
          <td class="arxiv-id">{_html_escape(r.arxiv_id)}</td>
          <td><div class="title-zh">{_html_escape(r.title_zh)}</div></td>
          <td><div class="error-box">{_html_escape(r.error or "")}</div></td>
        </tr>
''')
        html_parts.append('      </tbody>\n    </table>\n  </div>\n')

    # Chart.js 脚本
    html_parts.append(f'''
</div>

<script>
const ctx = document.getElementById('kwChart').getContext('2d');
new Chart(ctx, {{
  type: 'bar',
  data: {{
    labels: {json.dumps(kw_labels, ensure_ascii=False)},
    datasets: [
      {{
        label: '✓ 命中录入',
        data: {json.dumps(kw_recorded)},
        backgroundColor: 'rgba(16, 185, 129, 0.85)',
        borderRadius: 6,
        stack: 's'
      }},
      {{
        label: '⊘ 未命中过滤',
        data: {json.dumps(kw_filtered)},
        backgroundColor: 'rgba(148, 163, 184, 0.65)',
        borderRadius: 6,
        stack: 's'
      }},
      {{
        label: '⌛ PDF 超时',
        data: {json.dumps(kw_timeout)},
        backgroundColor: 'rgba(245, 158, 11, 0.85)',
        borderRadius: 6,
        stack: 's'
      }}
    ]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    scales: {{
      x: {{ stacked: true, grid: {{ display: false }} }},
      y: {{ stacked: true, beginAtZero: true, ticks: {{ precision: 0 }} }}
    }},
    plugins: {{
      legend: {{ position: 'top', align: 'end' }},
      tooltip: {{ mode: 'index', intersect: false }}
    }}
  }}
}});
</script>
</body>
</html>
''')

    return ''.join(html_parts)


def write_report(
    task_log: TaskLog,
    output_dir: str = "data/reports",
) -> Optional[Path]:
    """
    生成报告并返回 HTML 文件路径。

    产出:
      {output_dir}/report_{target_date}.html
      {output_dir}/report_{target_date}.json
    """
    try:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        target = task_log.target_date
        suffix = target.isoformat() if target else task_log.task_time_bj.strftime("%Y%m%d_%H%M%S")

        json_path = out_dir / f"report_{suffix}.json"
        html_path = out_dir / f"report_{suffix}.html"

        json_path.write_text(
            json.dumps(_serialize_task_log(task_log), ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

        html = _render_html(task_log)
        html_path.write_text(html, encoding='utf-8')

        logger.info(f"报告已生成: {html_path}")
        return html_path
    except Exception as e:
        logger.error(f"报告生成失败: {e}")
        return None
