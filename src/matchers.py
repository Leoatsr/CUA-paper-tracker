"""
模糊匹配规则实现

规范化规则：小写 + 去连字符 + 去空格 + 去下划线
CUA 例外：区分大小写 + 单词边界
"""
import re


def _normalize(text: str) -> str:
    """规范化文本：小写、去连字符、去空格、去下划线"""
    return re.sub(r'[-\s_]', '', text.lower())


def count_web_agent(text: str) -> int:
    """统计 Web Agent 类出现次数（含 web agent, webagent, web-agent, web agents 等）"""
    return _normalize(text).count('webagent')


def count_gui_agent(text: str) -> int:
    """统计 GUI Agent 类出现次数"""
    return _normalize(text).count('guiagent')


def count_computer_use(text: str) -> int:
    """统计 Computer Use 类出现次数（computer use / computer-use / computer-using）"""
    n = _normalize(text)
    # computeruse 涵盖 computer use / computer-use
    # computerusing 涵盖 computer using / computer-using
    return n.count('computeruse') + n.count('computerusing')


def count_mobile_agent(text: str) -> int:
    """统计 Mobile Agent 类出现次数"""
    return _normalize(text).count('mobileagent')


def count_gui_grounding(text: str) -> int:
    """统计 GUI Grounding 类出现次数"""
    return _normalize(text).count('guigrounding')


def count_cua(text: str) -> int:
    """CUA 精确匹配（区分大小写 + 单词边界，避免误伤 evacuation 等词）"""
    return len(re.findall(r'\bCUA\b', text))


def count_claw_agent(text: str) -> int:
    """统计 Claw Agent 类出现次数"""
    return _normalize(text).count('clawagent')


def count_ai_agent(text: str) -> int:
    """统计 AI Agent 类出现次数"""
    return _normalize(text).count('aiagent')


def count_ui_agent(text: str) -> int:
    """统计 UI Agent 类出现次数"""
    return _normalize(text).count('uiagent')


# 一级关键词 → 匹配函数映射
KEYWORD_MATCHERS = {
    'GUI Agent': count_gui_agent,
    'Web Agent': count_web_agent,
    'CUA': count_cua,
    'computer use': count_computer_use,
    'mobile agent': count_mobile_agent,
    'GUI grounding': count_gui_grounding,
    'Claw agent': count_claw_agent,
    'ai agent': count_ai_agent,
    'UI agent': count_ui_agent,
}

# 一级关键词列表（按 SOP 第 0.1 节顺序）
PRIMARY_KEYWORDS = list(KEYWORD_MATCHERS.keys())


def paper_matches_keyword(text: str, keyword: str) -> bool:
    """判断文本是否命中一级关键词"""
    matcher = KEYWORD_MATCHERS.get(keyword)
    if matcher is None:
        raise ValueError(f"未知关键词: {keyword}")
    return matcher(text) > 0
