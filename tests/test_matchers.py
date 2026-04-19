"""
模糊匹配单元测试
运行: python -m pytest tests/ -v
或:   python tests/test_matchers.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.matchers import (
    count_web_agent, count_gui_agent, count_cua,
    count_computer_use, count_mobile_agent, count_gui_grounding,
)


def test_web_agent_variants():
    """Web Agent 各种变体都应命中"""
    assert count_web_agent("Web Agent is cool") == 1
    assert count_web_agent("WebAgent") == 1
    assert count_web_agent("web-agent framework") == 1
    assert count_web_agent("web agents are trending") == 1
    assert count_web_agent("We propose WebAgents") == 1
    assert count_web_agent("WEB AGENT") == 1
    assert count_web_agent("no match here") == 0


def test_web_agent_multiple_occurrences():
    """多次出现应累计"""
    text = "WebAgent is a web agent. Another web-agent paper discusses web agents."
    assert count_web_agent(text) == 4


def test_gui_agent_variants():
    assert count_gui_agent("GUI Agent") == 1
    assert count_gui_agent("GUIAgent") == 1
    assert count_gui_agent("gui-agents") == 1
    assert count_gui_agent("UI agent") == 0


def test_cua_case_sensitive():
    """CUA 必须区分大小写，避免误伤"""
    assert count_cua("We propose CUA, a new method") == 1
    assert count_cua("CUA achieves SOTA. CUA outperforms...") == 2
    # 不应误伤
    assert count_cua("evacuation plan") == 0
    assert count_cua("accumulate cua") == 0
    assert count_cua("cua in lowercase") == 0


def test_computer_use():
    assert count_computer_use("Computer Use benchmarks") == 1
    assert count_computer_use("computer-use agents") == 1
    assert count_computer_use("computer using strategy") == 1
    # 同一文本不同变体累计
    assert count_computer_use("Computer use and computer using") == 2


def test_mobile_agent():
    assert count_mobile_agent("Mobile Agent v2") == 1
    assert count_mobile_agent("MobileAgents") == 1


def test_gui_grounding():
    assert count_gui_grounding("GUI Grounding task") == 1
    assert count_gui_grounding("gui-grounding") == 1


if __name__ == '__main__':
    tests = [
        test_web_agent_variants,
        test_web_agent_multiple_occurrences,
        test_gui_agent_variants,
        test_cua_case_sensitive,
        test_computer_use,
        test_mobile_agent,
        test_gui_grounding,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"✓ {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"✗ {t.__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
