"""
HTML 结构 dump 工具 —— 用于抓 chatpaper 真实 DOM 结构

运行：
  python -m tools.dump_html

会打开 Chromium 访问 chatpaper.com/zh-CN，搜 "Web Agent"，
然后把三张页面保存到 data/dump/：
  - home.html          首页
  - search_results.html 搜索结果页
  - detail.html         第一篇论文的详情页

输出这三个文件发给 Claude，可以根据真实 DOM 写出对的 CSS 选择器。
"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright


DUMP_DIR = Path("data/dump")
DUMP_DIR.mkdir(parents=True, exist_ok=True)


async def dump():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)  # 可视化观察
        context = await browser.new_context(
            locale='zh-CN',
            viewport={'width': 1440, 'height': 900},
        )
        page = await context.new_page()

        # ─── 1. 首页
        print("[1/3] 打开首页 https://chatpaper.com/zh-CN ...")
        await page.goto("https://chatpaper.com/zh-CN", wait_until='domcontentloaded', timeout=60000)
        await page.wait_for_timeout(8000)  # 等 JS 渲染
        html = await page.content()
        (DUMP_DIR / "home.html").write_text(html, encoding='utf-8')
        print(f"    已保存 {DUMP_DIR / 'home.html'} ({len(html)} 字符)")

        # ─── 2. 搜索结果页
        print("[2/3] 尝试在搜索框里输入 'Web Agent' ...")
        # 尝试多种可能的搜索框选择器
        search_selectors = [
            'input[type="search"]',
            'input[placeholder*="搜索"]',
            'input[placeholder*="search"]',
            'input[placeholder*="Search"]',
            'input',  # 兜底
        ]
        searched = False
        for sel in search_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.fill("Web Agent")
                    await loc.press("Enter")
                    await page.wait_for_timeout(8000)
                    print(f"    用选择器 '{sel}' 成功搜索")
                    searched = True
                    break
            except Exception as e:
                print(f"    选择器 '{sel}' 失败: {e}")
                continue

        if not searched:
            print("    ⚠️ 所有搜索选择器都失败，保存首页当前状态作为搜索结果")

        html = await page.content()
        (DUMP_DIR / "search_results.html").write_text(html, encoding='utf-8')
        print(f"    已保存 {DUMP_DIR / 'search_results.html'} ({len(html)} 字符)")

        # ─── 3. 详情页（点第一篇论文）
        print("[3/3] 尝试点击第一张论文卡片进入详情页 ...")
        # 尝试各种可能的卡片链接选择器
        link_selectors = [
            'a[href*="/paper/"]',
            'a[href*="/detail/"]',
            'article a',
            '.paper-card a',
            '[class*="card"] a',
            '[class*="paper"] a',
        ]
        clicked = False
        for sel in link_selectors:
            try:
                links = page.locator(sel)
                count = await links.count()
                if count > 0:
                    first_href = await links.first.get_attribute("href")
                    print(f"    用选择器 '{sel}' 找到 {count} 个链接，点第一个: {first_href}")
                    await links.first.click()
                    await page.wait_for_timeout(8000)
                    clicked = True
                    break
            except Exception as e:
                continue

        if not clicked:
            print("    ⚠️ 未能进入详情页")

        html = await page.content()
        (DUMP_DIR / "detail.html").write_text(html, encoding='utf-8')
        print(f"    已保存 {DUMP_DIR / 'detail.html'} ({len(html)} 字符)")

        print("\n✅ Dump 完成，浏览器保持打开 20 秒供你观察，然后会自动关闭")
        await page.wait_for_timeout(20000)

        await context.close()
        await browser.close()


if __name__ == '__main__':
    asyncio.run(dump())
