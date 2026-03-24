#!/usr/bin/env python3
"""
claude_usage.py — 从 Chrome 读 cookie，用 Playwright 抓 Claude 用量
输出 JSON 到 stdout，供 orbit_guard.py 调用
"""

import json, sys, re, subprocess
from pathlib import Path


def get_cookies() -> dict:
    """从 Chrome 读 claude.ai 的 cookies"""
    try:
        import browser_cookie3
        jar = browser_cookie3.chrome(domain_name='.claude.ai')
        cookies = {c.name: c.value for c in jar}
        return cookies
    except Exception as e:
        return {}


def fetch_usage(cookies: dict) -> dict:
    from playwright.sync_api import sync_playwright

    cookie_list = [
        {'name': k, 'value': v, 'domain': '.claude.ai', 'path': '/'}
        for k, v in cookies.items()
    ]

    result = {'raw_text': '', 'api': {}}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        ctx.add_cookies(cookie_list)
        page = ctx.new_page()

        # 拦截 API 响应，顺便找 usage 相关接口
        def on_response(response):
            if response.status == 200 and '/api/' in response.url:
                try:
                    data = response.json()
                    if isinstance(data, dict):
                        result['api'][response.url] = data
                except Exception:
                    pass

        page.on('response', on_response)

        try:
            page.goto('https://claude.ai/settings/usage',
                      wait_until='networkidle', timeout=25000)
            page.wait_for_timeout(2000)
            result['raw_text'] = page.inner_text('body')

            # 尝试提取 aria-valuenow（进度条原始值）
            bars = page.query_selector_all('[aria-valuenow]')
            result['progress_values'] = []
            for bar in bars:
                v = bar.get_attribute('aria-valuenow')
                if v is not None:
                    try:
                        result['progress_values'].append(float(v))
                    except Exception:
                        pass

        except Exception as e:
            result['error'] = str(e)

        browser.close()

    return result


def parse_usage(data: dict) -> dict:
    text  = data.get('raw_text', '')
    out   = {}

    # 周用量百分比
    prog  = data.get('progress_values', [])
    if prog:
        out['weekly_pct'] = prog[0]
    else:
        m = re.search(r'(\d+(?:\.\d+)?)\s*%', text)
        if m:
            out['weekly_pct'] = float(m.group(1))

    # 重置倒计时
    m = re.search(r'(\d+)\s*days?\b', text, re.IGNORECASE)
    if m:
        out['reset_days'] = int(m.group(1))

    m = re.search(r'(\d+)\s*hours?\b', text, re.IGNORECASE)
    if m:
        out['reset_hours'] = int(m.group(1))

    # Extra 费用
    m = re.search(r'\$\s*(\d+(?:\.\d{1,2})?)', text)
    if m:
        out['extra_cost'] = float(m.group(1))

    # 调试：截取前 800 字
    out['_preview'] = text[:800]

    return out


if __name__ == '__main__':
    try:
        cookies = get_cookies()
        if not cookies:
            print(json.dumps({'error': 'no_cookies'}))
            sys.exit(1)

        raw   = fetch_usage(cookies)
        usage = parse_usage(raw)
        print(json.dumps(usage, ensure_ascii=False))

    except Exception as e:
        print(json.dumps({'error': str(e)}))
        sys.exit(1)
