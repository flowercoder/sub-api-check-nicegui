#!/usr/bin/env python3
"""
API Key 连通性测试工具 - NiceGUI版本
在浏览器里直接测试 API Key 的连通性，并自动生成对应的 curl 命令
"""

import json
import os
import time
import asyncio
import sys
import httpx
from httpx_socks import AsyncProxyTransport
from html import escape
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote
from nicegui import ui, app, welcome
from fastapi import Request
from fastapi.responses import JSONResponse

if sys.version_info < (3, 8, 5):
    raise RuntimeError('本项目需要 Python 3.8.5 或更高版本')


async def _skip_nicegui_url_collection() -> None:
    """Avoid ifaddr.get_adapters() startup failure when binding to 0.0.0.0."""
    return None


welcome.collect_urls = _skip_nicegui_url_collection

# 配置常量
MODELS = ["claude-opus-4-6", "claude-opus-4-8", "gpt-5.5", "gpt-5.4"]

FORMATS = [
    {"id": "anthropic", "title": "Anthropic 消息格式", "desc": "POST /v1/messages 使用 x-api-key"},
    {"id": "openai-chat", "title": "OpenAI v1 格式", "desc": "POST /v1/chat/completions 兼容 chat/completions"},
    {"id": "openai-responses", "title": "OpenAI Responses API", "desc": "POST /v1/responses"},
]

FORMAT_TITLES = {item['id']: item['title'] for item in FORMATS}

DEFAULT_PROMPT = "just say hi, nothing else"


def render_code_block(text: str, extra_style: str = '') -> str:
    """Render escaped monospace HTML."""
    style_attr = f' style="{extra_style}"' if extra_style else ''
    return f'<pre class="mono"{style_attr}>{escape(str(text))}</pre>'


def render_kv_grid(items) -> str:
    """Render escaped key-value rows."""
    rows = []
    for key, value in items:
        rows.append(f'<div class="kv-key">{escape(str(key))}</div><div>{escape(str(value))}</div>')
    return '<div class="kv-grid">' + ''.join(rows) + '</div>'


def normalize_text(text: str) -> str:
    return ' '.join(str(text).split())


def shorten_text(text: str, limit: int = 96) -> str:
    value = normalize_text(text)
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 0)].rstrip() + '…'


def format_timestamp_text(iso_text: str) -> str:
    try:
        return datetime.fromisoformat(iso_text).strftime('%m-%d %H:%M')
    except Exception:
        return shorten_text(iso_text, 16)


def get_format_title(format_id: str) -> str:
    return FORMAT_TITLES.get(format_id, format_id)


def build_socks_proxy_url(proxy_config: Optional[Dict]) -> str:
    """Build a SOCKS5 proxy URL from persisted UI config."""
    if not proxy_config or not proxy_config.get('enabled'):
        return ''

    host = str(proxy_config.get('host') or '').strip()
    port = str(proxy_config.get('port') or '').strip()
    username = str(proxy_config.get('username') or '').strip()
    password = str(proxy_config.get('password') or '').strip()

    if not host or not port:
        return ''

    if username and password:
        return f"socks5://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"
    return f"socks5://{host}:{port}"


def render_chip(text: str, variant: str = 'muted') -> str:
    return f'<span class="expansion-chip expansion-chip-{variant}">{escape(str(text))}</span>'


def render_expansion_header(
    *,
    status_text: str,
    status_variant: str,
    title: str,
    subtitle: str,
    chips: List[Tuple[str, str]],
    right_title: str,
    right_subtitle: str,
) -> str:
    chips_html = ''.join(render_chip(text, variant) for text, variant in chips if text)
    return f'''
        <div class="expansion-head">
            <div class="expansion-head-main">
                <div class="expansion-head-top">
                    <span class="expansion-chip expansion-chip-{status_variant}">{escape(str(status_text))}</span>
                    <span class="expansion-head-title">{escape(str(title))}</span>
                </div>
                <div class="expansion-head-subtitle">{escape(str(subtitle))}</div>
                <div class="expansion-head-chips">{chips_html}</div>
            </div>
            <div class="expansion-head-side">
                <div class="expansion-head-side-main">{escape(str(right_title))}</div>
                <div class="expansion-head-side-sub">{escape(str(right_subtitle))}</div>
            </div>
        </div>
    '''

# 模型列表适配器
MODEL_LIST_ADAPTERS = {
    "anthropic": {
        "label": "Anthropic",
        "path": "/v1/models",
    },
    "openai-chat": {
        "label": "OpenAI",
        "path": "/v1/models",
    },
    "openai-responses": {
        "label": "OpenAI",
        "path": "/v1/models",
    },
}


def build_anthropic_models_url(endpoint: str) -> str:
    """构建Anthropic模型列表URL"""
    raw_endpoint = endpoint.strip().rstrip('/')
    if not raw_endpoint:
        return ""

    try:
        from urllib.parse import urlparse, urlunparse
        import re
        parsed = urlparse(raw_endpoint)
        normalized_path = parsed.path.rstrip('/')

        # 移除 /anthropic 或 /anthropic/v1 后缀
        if re.search(r'/anthropic(/v1)?$', normalized_path, re.IGNORECASE):
            normalized_path = re.sub(r'/anthropic(/v1)?$', '', normalized_path, flags=re.IGNORECASE) or '/'
            clean_url = urlunparse((parsed.scheme, parsed.netloc, normalized_path, '', '', ''))
            return append_api_path(clean_url, '/v1/models')
    except Exception:
        pass

    import re
    clean_endpoint = re.sub(r'/anthropic(/v1)?$', '', raw_endpoint, flags=re.IGNORECASE)
    return append_api_path(clean_endpoint, '/v1/models')


def extract_model_ids(payload, array_keys=None) -> List[str]:
    """从API响应中提取模型ID列表"""
    if array_keys is None:
        array_keys = ["data", "models"]

    source = []
    if isinstance(payload, list):
        source = payload
    elif isinstance(payload, dict):
        for key in array_keys:
            if isinstance(payload.get(key), list):
                source = payload[key]
                break

    ids = []
    for item in source:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict):
            model_id = str(item.get('id') or item.get('name') or item.get('model') or item.get('display_name') or '').strip()
            if model_id:
                ids.append(model_id)

    return sorted(list(set(ids)))


def append_api_path(endpoint: str, api_path: str) -> str:
    """拼接API路径"""
    raw_endpoint = endpoint.strip().rstrip('/')
    normalized_api_path = f"/{api_path.strip().lstrip('/')}"

    if not raw_endpoint:
        return normalized_api_path

    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(raw_endpoint)
        base_path = parsed.path.rstrip('/')

        # 处理 /v1 重复
        if base_path.endswith('/v1') and normalized_api_path.startswith('/v1'):
            new_path = base_path + normalized_api_path.replace('/v1', '', 1)
        else:
            new_path = base_path + normalized_api_path

        return urlunparse((parsed.scheme, parsed.netloc, new_path, '', '', ''))
    except Exception:
        return raw_endpoint + normalized_api_path


def build_request_config(endpoint: str, api_key: str, model: str, format_id: str, prompt: str) -> Dict:
    """构建请求配置"""
    endpoint = endpoint.strip().rstrip('/')
    config = {
        'url': endpoint,
        'method': 'POST',
        'headers': {'content-type': 'application/json'},
        'body': None,
        'model': model,
        'format_id': format_id,
    }

    if format_id == 'anthropic':
        config['url'] = append_api_path(endpoint, '/v1/messages')
        config['headers']['x-api-key'] = api_key.strip()
        config['headers']['anthropic-version'] = '2023-06-01'
        config['headers']['anthropic-dangerous-direct-browser-access'] = 'true'
        config['body'] = {
            'model': model,
            'max_tokens': 64,
            'metadata': {'user_id': 'browser-local-test'},
            'messages': [{'role': 'user', 'content': prompt}],
        }
    elif format_id == 'openai-chat':
        config['url'] = append_api_path(endpoint, '/v1/chat/completions')
        config['headers']['authorization'] = f"Bearer {api_key.strip()}"
        config['body'] = {
            'model': model,
            'max_tokens': 64,
            'messages': [{'role': 'user', 'content': prompt}],
        }
    elif format_id == 'openai-responses':
        config['url'] = append_api_path(endpoint, '/v1/responses')
        config['headers']['authorization'] = f"Bearer {api_key.strip()}"
        config['body'] = {
            'model': model,
            'max_output_tokens': 64,
            'input': prompt,
        }

    return config


def build_curl(config: Dict, proxy_config: Optional[Dict] = None) -> str:
    """生成curl命令"""
    def esc_shell(value: str) -> str:
        return "'" + str(value).replace("'", "'\\''") + "'"

    lines = [f"curl -sS {esc_shell(config['url'])}", f"  -X {config['method']}"]
    proxy_url = build_socks_proxy_url(proxy_config)
    if proxy_url:
        lines.append(f"  --proxy {esc_shell(proxy_url.replace('socks5://', 'socks5h://', 1))}")
    for key, value in config['headers'].items():
        lines.append(f"  -H {esc_shell(f'{key}: {value}')}")
    if config['body']:
        lines.append(f"  --data-binary {esc_shell(json.dumps(config['body'], indent=2))}")
    return " \\\n".join(lines)


def infer_config_format_id(config: Dict) -> str:
    """Return stored or inferred API format id for old monitor entries."""
    format_id = config.get('format_id')
    if format_id in FORMAT_TITLES:
        return format_id

    url = str(config.get('url') or '').lower()
    headers = {str(key).lower(): value for key, value in (config.get('headers') or {}).items()}
    if 'x-api-key' in headers or '/v1/messages' in url:
        return 'anthropic'
    if '/v1/responses' in url:
        return 'openai-responses'
    return 'openai-chat'


def infer_monitor_format_id(monitor: Dict) -> str:
    """Return stored or inferred API format id for monitor entries."""
    format_id = monitor.get('format_id')
    if format_id in FORMAT_TITLES:
        return format_id
    return infer_config_format_id(monitor.get('config', {}))


def get_failure_hint(status: int, text: str) -> str:
    """获取失败提示"""
    if status == 401:
        return "API Key 无效、过期，或头字段不符合服务商要求。"
    if status == 403:
        return "权限、额度或模型访问权限不足。"
    if status == 404:
        return "接口地址或接口路径不对，请对照服务商文档检查。"
    if status == 429:
        return "请求过于频繁，稍后再试。"
    if 'cors' in text.lower() or 'failed to fetch' in text.lower():
        return "浏览器跨域限制，建议直接使用生成的 curl。"
    return ""


async def perform_api_request(config: Dict, proxy_config: Optional[Dict] = None) -> Dict:
    """Execute the configured API request without routing through localhost."""
    started = time.time()

    try:
        client_kwargs = {'timeout': 30.0}
        proxy_url = build_socks_proxy_url(proxy_config)
        if proxy_url:
            client_kwargs['transport'] = AsyncProxyTransport.from_url(proxy_url)

        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.request(
                method=config.get('method', 'POST'),
                url=config.get('url'),
                headers=config.get('headers', {}),
                json=config.get('body') if config.get('body') else None,
            )
            text = response.text
            elapsed = int((time.time() - started) * 1000)

            json_text = ""
            json_state = "不是 JSON"
            try:
                parsed = json.loads(text) if text else None
                json_text = json.dumps(parsed, indent=2, ensure_ascii=False)
                json_state = "可解析"
            except Exception:
                pass

            return {
                'ok': response.is_success,
                'status_code': f"{response.status_code} {response.reason_phrase}",
                'duration': f"{elapsed} ms",
                'content_type': response.headers.get('content-type', '-'),
                'response_size': f"{len(text)} 字符",
                'json_state': json_state,
                'failure_hint': get_failure_hint(response.status_code, text) or ('-' if response.is_success else '请查看状态码和原始返回。'),
                'response_text': text or "(empty response)",
                'json_text': json_text,
                'timestamp': datetime.now().isoformat(),
            }
    except Exception as e:
        elapsed = int((time.time() - started) * 1000)
        text = str(e)
        return {
            'ok': False,
            'status_code': 'Request Failed',
            'duration': f"{elapsed} ms",
            'content_type': '-',
            'response_size': f"{len(text)} 字符",
            'json_state': '-',
            'failure_hint': f'请求失败: {str(e)}',
            'response_text': text,
            'json_text': '',
            'timestamp': datetime.now().isoformat(),
        }


async def execute_request(config: Dict) -> Dict:
    """执行API请求 - 直接在服务端执行，避免依赖 localhost 反向代理。"""
    return await perform_api_request(config, app.storage.user.get('proxy_config', {}))


# ==================== 后端 API 接口 ====================

@app.post('/api/test')
async def api_test(request: Request):
    """后端测试接口 - 执行实际的 API 请求"""
    try:
        data = await request.json()
        config = {
            'url': data.get('url'),
            'method': data.get('method', 'POST'),
            'headers': data.get('headers', {}),
            'body': data.get('body'),
        }
        return JSONResponse(await perform_api_request(config, data.get('proxy_config')))

    except Exception as e:
        text = str(e)
        return JSONResponse({
            'ok': False,
            'status_code': 'Request Failed',
            'duration': '0 ms',
            'content_type': '-',
            'response_size': f"{len(text)} 字符",
            'json_state': '-',
            'failure_hint': f'请求失败: {str(e)}',
            'response_text': text,
            'json_text': '',
            'timestamp': datetime.now().isoformat(),
        })


# ==================== 原 execute_request 函数（已废弃，保留备份）====================

async def execute_request_old(config: Dict) -> Dict:
    """执行API请求"""
    started = time.time()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=config['method'],
                url=config['url'],
                headers=config['headers'],
                json=config['body'] if config['body'] else None,
            )
            text = response.text
            elapsed = int((time.time() - started) * 1000)

            json_text = ""
            json_state = "不是 JSON"
            try:
                parsed = json.loads(text) if text else None
                json_text = json.dumps(parsed, indent=2, ensure_ascii=False)
                json_state = "可解析"
            except Exception:
                pass

            return {
                'ok': response.is_success,
                'status_code': f"{response.status_code} {response.reason_phrase}",
                'duration': f"{elapsed} ms",
                'content_type': response.headers.get('content-type', '-'),
                'response_size': f"{len(text)} 字符",
                'json_state': json_state,
                'failure_hint': get_failure_hint(response.status_code, text) or ('-' if response.is_success else '请查看状态码和原始返回。'),
                'response_text': text or "(empty response)",
                'json_text': json_text,
                'timestamp': datetime.now().isoformat(),
            }
    except Exception as e:
        elapsed = int((time.time() - started) * 1000)
        text = str(e)
        return {
            'ok': False,
            'status_code': 'Failed to fetch',
            'duration': f"{elapsed} ms",
            'content_type': '-',
            'response_size': f"{len(text)} 字符",
            'json_state': '-',
            'failure_hint': get_failure_hint(0, text) or '服务器未拿到响应，通常是跨域或网络错误。',
            'response_text': text,
            'json_text': '',
            'timestamp': datetime.now().isoformat(),
        }


@ui.page('/')
def index():
    """主页面"""

    # 初始化存储
    if 'history' not in app.storage.user:
        app.storage.user['history'] = []
    if 'monitors' not in app.storage.user:
        app.storage.user['monitors'] = []

    # 页面样式 - 参考原版设计
    ui.add_head_html('''
        <style>
            :root {
                --bg: #f4f7fb;
                --surface: #ffffff;
                --surface-soft: #f8fafc;
                --surface-quiet: #eef2f7;
                --text: #0f172a;
                --muted: #64748b;
                --border: #d7dee8;
                --border-strong: #b8c4d5;
                --primary: #2563eb;
                --primary-soft: rgba(37, 99, 235, 0.1);
                --success: #15803d;
                --success-soft: rgba(21, 128, 61, 0.12);
                --danger: #b91c1c;
                --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04);
                --shadow-md: 0 14px 40px rgba(15, 23, 42, 0.08);
                --shadow-lg: 0 24px 70px rgba(15, 23, 42, 0.12);
            }
            html, body {
                background: var(--bg) !important;
                color: var(--text);
            }
            body {
                background-image:
                    radial-gradient(circle at top left, rgba(37, 99, 235, 0.08), transparent 32%),
                    radial-gradient(circle at top right, rgba(21, 128, 61, 0.06), transparent 24%);
                background-attachment: fixed;
            }
            .nice-page { max-width: 100%; margin: 0 auto; padding: 0; }
            .hero-wrapper {
                width: 100%;
                background: linear-gradient(180deg, rgba(255,255,255,0.95), rgba(248,250,252,0.9));
                border-bottom: 1px solid var(--border);
                padding: 14px 32px 12px;
                margin-bottom: 12px;
            }
            .hero-content {
                max-width: 1600px;
                margin: 0 auto;
            }
            .content-wrapper {
                max-width: 1600px;
                margin: 0 auto;
                padding: 0 32px 44px;
            }
            .main-panels {
                display: grid;
                grid-template-columns: minmax(0, 1fr) minmax(0, 2fr);
                gap: 16px;
                align-items: start;
            }
            .main-panel {
                min-width: 0;
            }
            .main-panel-card {
                min-width: 0;
                width: 100%;
            }
            .main-panel-card .q-expansion-item,
            .main-panel-card .q-card {
                max-width: 100%;
            }
            .record-body {
                max-width: 100%;
                min-width: 0;
                overflow-x: auto;
            }
            .hero-shell {
                background: linear-gradient(180deg, rgba(255,255,255,0.9), rgba(255,255,255,0.78));
                border: 1px solid rgba(215, 222, 232, 0.9);
                box-shadow: var(--shadow-md);
                border-radius: 24px;
                padding: 22px 22px 18px;
                backdrop-filter: blur(14px);
            }
            .hero-title-row {
                display: flex;
                align-items: center;
                flex-wrap: wrap;
                gap: 12px;
            }
            .hero-topline { display: flex; flex-wrap: wrap; gap: 8px; margin: 0; }
            .pill {
                display: inline-flex;
                align-items: center;
                border-radius: 999px;
                padding: 5px 10px;
                background: var(--surface-soft);
                color: var(--muted);
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0;
                border: 1px solid var(--border);
            }
            .q-card {
                box-shadow: var(--shadow-md) !important;
                border-radius: 20px !important;
                border: 1px solid rgba(215, 222, 232, 0.9);
                background: rgba(255,255,255,0.92) !important;
                backdrop-filter: blur(10px);
                min-width: 0;
            }
            .q-card.soft-bg { background: var(--surface-soft) !important; }
            .result-card {
                background: linear-gradient(180deg, var(--surface), var(--surface-soft));
                border-radius: 16px;
                padding: 14px;
                margin: 8px 0;
                border: 1px solid var(--border);
                box-shadow: var(--shadow-sm);
                min-width: 0;
            }
            .kv-grid { display: grid; grid-template-columns: 116px 1fr; gap: 10px 12px; font-size: 14px; }
            .kv-key { color: var(--muted); font-weight: 700; }
            .mono {
                font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
                font-size: 12px;
                white-space: pre-wrap;
                word-break: break-word;
                overflow-wrap: anywhere;
                background: #0b1220;
                color: #e2e8f0;
                padding: 16px 18px;
                border-radius: 16px;
                max-height: 28rem;
                overflow: auto;
                line-height: 1.55;
                border: 1px solid rgba(148, 163, 184, 0.18);
                box-shadow: inset 0 1px 0 rgba(255,255,255,0.02);
            }
            .q-input .q-field__control,
            .q-textarea .q-field__control {
                background: linear-gradient(180deg, #ffffff, #f8fafc) !important;
                border-radius: 14px !important;
                border: 1px solid var(--border) !important;
                transition: border-color 0.2s ease, box-shadow 0.2s ease;
            }
            .q-field--focused .q-field__control {
                border-color: var(--primary) !important;
                box-shadow: 0 0 0 4px var(--primary-soft) !important;
            }
            .q-btn {
                border-radius: 14px !important;
                text-transform: none !important;
                font-weight: 700 !important;
                letter-spacing: 0 !important;
            }
            .q-btn:hover { transform: translateY(-1px); }
            .q-btn.model-btn {
                min-height: 76px !important;
                padding: 14px !important;
                border: 1px solid var(--border) !important;
                background: var(--surface) !important;
            }
            .q-btn.model-btn.q-btn--unelevated {
                background: linear-gradient(135deg, var(--text), #1e293b) !important;
                color: #fff !important;
                border-color: transparent !important;
                box-shadow: var(--shadow-md) !important;
            }
            .q-expansion-item {
                background: var(--surface) !important;
                border-radius: 16px;
                box-shadow: var(--shadow-sm);
                margin-bottom: 12px;
                border: 1px solid var(--border);
                min-width: 0;
            }
            .q-expansion-item__container { border-radius: 16px; min-width: 0; }
            .expansion-head {
                display: flex;
                align-items: flex-start;
                justify-content: space-between;
                gap: 16px;
                width: 100%;
                padding: 4px 2px 2px;
            }
            .expansion-head-main {
                min-width: 0;
                flex: 1 1 auto;
                display: flex;
                flex-direction: column;
                gap: 8px;
            }
            .expansion-head-top {
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                gap: 8px;
                min-width: 0;
            }
            .expansion-head-title {
                font-size: 15px;
                font-weight: 800;
                color: var(--text);
                min-width: 0;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }
            .expansion-head-subtitle {
                color: var(--muted);
                font-size: 12px;
                line-height: 1.35;
                word-break: break-word;
            }
            .expansion-head-chips {
                display: flex;
                flex-wrap: wrap;
                gap: 6px;
            }
            .expansion-head-side {
                flex: 0 0 auto;
                text-align: right;
                display: flex;
                flex-direction: column;
                gap: 4px;
                padding-top: 2px;
                max-width: 34%;
            }
            .expansion-head-side-main {
                font-size: 13px;
                font-weight: 800;
                color: var(--text);
                word-break: break-word;
            }
            .expansion-head-side-sub {
                font-size: 12px;
                line-height: 1.35;
                color: var(--muted);
                word-break: break-word;
            }
            .history-head-wrap,
            .monitor-head-wrap {
                width: 100%;
                display: flex;
                align-items: flex-start;
                gap: 10px;
                padding: 4px 0;
            }
            .history-head-content,
            .monitor-head-content {
                flex: 1 1 auto;
                min-width: 0;
            }
            .history-head-actions,
            .monitor-head-actions {
                flex: 0 0 auto;
                display: flex;
                align-items: center;
                padding-top: 2px;
            }
            .expansion-chip {
                display: inline-flex;
                align-items: center;
                border-radius: 999px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 0;
                border: 1px solid var(--border);
                background: var(--surface-soft);
                color: var(--muted);
            }
            .expansion-chip-info {
                background: rgba(37, 99, 235, 0.1);
                border-color: rgba(37, 99, 235, 0.2);
                color: #1d4ed8;
            }
            .expansion-chip-success {
                background: rgba(21, 128, 61, 0.12);
                border-color: rgba(21, 128, 61, 0.2);
                color: var(--success);
            }
            .expansion-chip-danger {
                background: rgba(185, 28, 28, 0.12);
                border-color: rgba(185, 28, 28, 0.2);
                color: var(--danger);
            }
            h1.main-title {
                font-size: clamp(26px, 3vw, 42px);
                line-height: 1.08;
                letter-spacing: 0;
                margin: 0;
                font-weight: 800;
                color: var(--text);
            }
            .intro-text {
                margin: 8px 0 0;
                padding: 0;
                border: 0;
                border-radius: 0;
                background: transparent;
                color: var(--muted);
                font-size: 13px;
                line-height: 1.4;
                box-shadow: none;
            }
            .section-title {
                font-size: 18px;
                line-height: 1.2;
                margin: 0;
                font-weight: 800;
                color: var(--text);
            }
            .helper-text { color: var(--muted); font-size: 13px; margin-top: 8px; }
            .panel-title {
                display: inline-flex;
                align-items: center;
                gap: 8px;
                font-size: 12px;
                font-weight: 800;
                text-transform: uppercase;
                letter-spacing: 0;
                color: var(--muted);
                margin-bottom: 8px;
            }
            .action-btn-primary {
                background: linear-gradient(135deg, var(--primary), #1d4ed8) !important;
                color: #fff !important;
                height: 52px !important;
                min-width: 180px !important;
                box-shadow: 0 12px 28px rgba(37, 99, 235, 0.24) !important;
            }
            .action-btn-primary:hover {
                box-shadow: 0 16px 34px rgba(37, 99, 235, 0.28) !important;
            }
            .subtle-btn {
                border: 1px solid var(--border) !important;
                background: var(--surface) !important;
                color: var(--text) !important;
            }
            .panel-gap { margin-top: 18px; }
        </style>
    ''')

    with ui.column().classes('nice-page w-full'):
        # 标题区域 - 撑满整个页面宽度
        with ui.column().classes('hero-wrapper'):
            with ui.column().classes('hero-content'):
                with ui.row().classes('hero-title-row'):
                    ui.html('<h1 class="main-title">API 连通性测试</h1>')
                    with ui.row().classes('hero-topline'):
                        ui.html('<span class="pill">100% 安全</span>')
                        ui.html('<span class="pill">服务器不保存</span>')
                        ui.html('<span class="pill">即时结果</span>')
                ui.html('<div class="intro-text">这个页面会在服务器端直接发起请求，并根据当前表单自动生成对应的 curl 命令。</div>')

        # 内容区域 - 有最大宽度限制
        with ui.column().classes('content-wrapper w-full'):
            with ui.row().classes('main-panels w-full panel-gap').style('align-items: flex-start;'):
                # 左侧：配置面板
                with ui.column().classes('main-panel'):
                    with ui.card().classes('main-panel-card p-5'):
                        ui.html('<h2 class="section-title mb-4">请求配置</h2>')

                        with ui.column().classes('w-full gap-3 mb-4'):
                            endpoint_input = ui.input('接口地址', placeholder='https://api.example.com') \
                                .classes('w-full').props('outlined dense')

                            api_key_input = ui.input('API Key', placeholder='sk-...', password=True, password_toggle_button=True) \
                                .classes('w-full').props('outlined dense')

                        ui.html('<div class="helper-text">只保存在当前会话内，不会发送给外部任何服务。</div>')

                        # 模型选择
                        ui.html('<div class="panel-title" style="margin-top: 16px;">预设模型</div>')
                        model_buttons = {}
                        selected_model = MODELS[0]

                        with ui.row().classes('gap-2 flex-wrap') as model_row:
                            for model in MODELS:
                                btn = ui.button(model).classes('model-btn').props('size=md no-caps')
                                model_buttons[model] = btn

                        def update_model_buttons(selected):
                            for m, btn in model_buttons.items():
                                if m == selected:
                                    btn.props(remove='outline').props('unelevated').style('background: linear-gradient(135deg, var(--text), #1e293b); color: #fff; border-color: transparent;')
                                else:
                                    btn.props(remove='unelevated').props('outline').style('background: var(--surface); color: var(--text); border-color: var(--border);')

                        def make_model_click_handler(model_name):
                            def handler():
                                nonlocal selected_model
                                selected_model = model_name
                                custom_model_input.value = ''
                                update_model_buttons(model_name)
                                update_curl_display()
                            return handler

                        for model, btn in model_buttons.items():
                            btn.on('click', make_model_click_handler(model))

                        update_model_buttons(selected_model)

                        custom_model_input = ui.input('自定义模型 ID', placeholder='可手填模型名称') \
                            .classes('w-full mt-4').props('outlined dense')

                        def on_custom_model_input():
                            nonlocal selected_model
                            if custom_model_input.value.strip():
                                selected_model = None
                                update_model_buttons(None)
                            update_curl_display()

                        custom_model_input.on('input', on_custom_model_input)

                    # 获取模型功能
                        fetched_models_container = ui.column().classes('w-full')
                        fetch_status_label = ui.html('<div class="helper-text" style="min-height: 20px;"></div>')

                        async def fetch_models():
                            if not endpoint_input.value.strip():
                                ui.notify("请先填写接口地址", type='negative')
                                return
                            if not api_key_input.value.strip():
                                ui.notify("请先填写 API Key", type='negative')
                                return

                            fetch_btn.props('loading')
                            adapter = MODEL_LIST_ADAPTERS[selected_format]

                        # 构建模型列表URL
                            if selected_format == 'anthropic':
                                url = build_anthropic_models_url(endpoint_input.value.strip())
                            else:
                                url = append_api_path(endpoint_input.value.strip(), adapter['path'])

                        # 构建请求头
                            if selected_format == 'anthropic':
                                headers = {
                                    'accept': 'application/json',
                                    'x-api-key': api_key_input.value.strip(),
                                    'anthropic-version': '2023-06-01',
                                }
                            else:
                                headers = {
                                    'accept': 'application/json',
                                    'authorization': f"Bearer {api_key_input.value.strip()}",
                                }

                            fetch_status_label.content = f'<div class="helper-text">正在用 {escape(adapter["label"])} 模型列表接口请求 {escape(url)}</div>'

                            try:
                                client_kwargs = {'timeout': 30.0}
                                proxy_url = build_socks_proxy_url(app.storage.user.get('proxy_config', {}))
                                if proxy_url:
                                    client_kwargs['transport'] = AsyncProxyTransport.from_url(proxy_url)

                                async with httpx.AsyncClient(**client_kwargs) as client:
                                    response = await client.get(url, headers=headers)
                                    text = response.text

                                    if not response.is_success:
                                        fetch_status_label.content = f'<div class="helper-text" style="color: var(--danger);">获取失败：{response.status_code} {escape(response.reason_phrase)}</div>'
                                        fetch_btn.props(remove='loading')
                                        return

                                    if not text.strip():
                                        fetch_status_label.content = '<div class="helper-text" style="color: var(--danger);">模型接口返回了空内容，无法解析 JSON</div>'
                                        fetch_btn.props(remove='loading')
                                        return

                                    try:
                                        payload = json.loads(text)
                                    except Exception:
                                        fetch_status_label.content = '<div class="helper-text" style="color: var(--danger);">模型接口返回的不是 JSON</div>'
                                        fetch_btn.props(remove='loading')
                                        return

                                    models = extract_model_ids(payload)
                                    if not models:
                                        fetch_status_label.content = '<div class="helper-text" style="color: var(--danger);">没有从返回结果中识别到模型 ID</div>'
                                        fetch_btn.props(remove='loading')
                                        return

                                # 显示模型列表
                                    fetched_models_container.clear()
                                    with fetched_models_container:
                                        fetch_status_label.content = f'<div class="helper-text">已获取 {len(models)} 个模型，点击下方模型即可填入</div>'
                                        with ui.row().classes('gap-2 flex-wrap mt-2').style('max-height: 220px; overflow: auto;'):
                                            for model in models:
                                                def make_model_select_handler(m):
                                                    def handler():
                                                        custom_model_input.value = m
                                                        update_curl_display()
                                                        ui.notify(f'已选择模型: {m}', type='positive')
                                                    return handler

                                                ui.button(model, on_click=make_model_select_handler(model)) \
                                                    .props('dense size=sm no-caps outline') \
                                                    .style('font-family: monospace; font-size: 12px; min-height: 42px; background: var(--surface); border: 1px solid var(--border);')

                            except Exception as e:
                                fetch_status_label.content = f'<div class="helper-text" style="color: var(--danger);">获取模型失败: {escape(str(e))}</div>'

                            fetch_btn.props(remove='loading')

                        with ui.row().classes('w-full justify-end mt-2'):
                            fetch_btn = ui.button('获取全部模型', icon='cloud_download', on_click=fetch_models) \
                                .props('outline dense no-caps size=sm').classes('subtle-btn')

                    # 格式选择
                        ui.html('<div class="panel-title" style="margin-top: 16px;">AI 接口格式</div>')
                        format_cards = {}
                        selected_format = FORMATS[0]['id']

                        with ui.column().classes('gap-2') as format_column:
                            for fmt in FORMATS:
                                with ui.card().tight().classes('cursor-pointer').style('background: var(--surface); border-radius: 16px; min-height: 76px; border: 1px solid var(--border);') as card:
                                    format_cards[fmt['id']] = card
                                    with ui.card_section().classes('q-pa-md'):
                                        ui.html(f'<div style="font-weight: 800; font-size: 14px; margin-bottom: 6px; color: #2563eb;">{fmt["title"]}</div>')
                                        ui.html(f'<div style="font-size: 12px; line-height: 1.35; opacity: 0.76; color: #2563eb;">{fmt["desc"]}</div>')

                        def update_format_cards(selected):
                            for fmt_id, card in format_cards.items():
                                if fmt_id == selected:
                                    card.style('background: linear-gradient(135deg, var(--text), #1e293b); color: #fff; border: 3px solid #2563eb;')
                                else:
                                    card.style('background: var(--surface); color: var(--text); border: 1px solid var(--border);')

                        def make_format_click_handler(format_id):
                            def handler():
                                nonlocal selected_format
                                selected_format = format_id
                                update_format_cards(format_id)
                                update_curl_display()
                            return handler

                        for fmt_id, card in format_cards.items():
                            card.on('click', make_format_click_handler(fmt_id))

                        update_format_cards(selected_format)

                    # 测试文本
                        prompt_input = ui.textarea('测试文本', value=DEFAULT_PROMPT) \
                            .classes('w-full mt-6').props('outlined rows=4')
                        prompt_input.on('input', lambda: update_curl_display())

                    # 代理配置
                        if 'proxy_config' not in app.storage.user:
                            app.storage.user['proxy_config'] = {
                                'enabled': False,
                                'host': '',
                                'port': '',
                                'username': '',
                                'password': '',
                            }

                        ui.html('<div class="panel-title" style="margin-top: 16px;">代理设置（可选）</div>')

                        with ui.expansion('SOCKS5 代理配置', icon='vpn_lock').classes('w-full mt-2').props('dense'):
                            with ui.column().classes('w-full gap-3 p-3'):
                                proxy_enabled = ui.checkbox('启用 SOCKS5 代理', value=app.storage.user['proxy_config']['enabled'])

                                with ui.row().classes('w-full gap-3'):
                                    proxy_host_input = ui.input('代理地址', placeholder='例如: 127.0.0.1', value=app.storage.user['proxy_config']['host']) \
                                        .classes('flex-1').props('outlined dense')
                                    proxy_port_input = ui.input('端口', placeholder='1080', value=app.storage.user['proxy_config']['port']) \
                                        .classes('w-32').props('outlined dense')

                                with ui.row().classes('w-full gap-3'):
                                    proxy_username_input = ui.input('用户名（可选）', placeholder='留空表示无需认证', value=app.storage.user['proxy_config']['username']) \
                                        .classes('flex-1').props('outlined dense')
                                    proxy_password_input = ui.input('密码（可选）', placeholder='', value=app.storage.user['proxy_config']['password']) \
                                        .classes('flex-1').props('outlined dense type=password')

                                ui.html('<div class="helper-text">配置后将应用到所有测试请求和长期监测</div>')

                                def save_proxy_config():
                                    app.storage.user['proxy_config'] = {
                                        'enabled': proxy_enabled.value,
                                        'host': proxy_host_input.value.strip(),
                                        'port': proxy_port_input.value.strip(),
                                        'username': proxy_username_input.value.strip(),
                                        'password': proxy_password_input.value.strip(),
                                    }
                                    update_curl_display()
                                    ui.notify('代理配置已保存', type='positive')

                            # 监听变化自动保存
                                proxy_enabled.on('update:model-value', lambda: save_proxy_config())
                                proxy_host_input.on('blur', lambda: save_proxy_config())
                                proxy_port_input.on('blur', lambda: save_proxy_config())
                                proxy_username_input.on('blur', lambda: save_proxy_config())
                                proxy_password_input.on('blur', lambda: save_proxy_config())

                    # 开始测试按钮
                    async def run_test():
                        if not endpoint_input.value.strip():
                            ui.notify("请先填写接口地址", type='negative')
                            return
                        if not api_key_input.value.strip():
                            ui.notify("请先填写 API Key", type='negative')
                            return

                        current_model = custom_model_input.value.strip() or selected_model
                        if not current_model:
                            ui.notify("请先选择或填写模型", type='negative')
                            return

                        test_button.props('loading')

                        config = build_request_config(
                            endpoint_input.value, api_key_input.value,
                            current_model, selected_format, prompt_input.value
                        )
                        result = await execute_request(config)

                        # 更新结果显示
                        result_data = {
                            'config': config,
                            'result': result,
                            'curl': build_curl(config, app.storage.user.get('proxy_config', {})),
                        }

                        # 添加到历史
                        app.storage.user['history'].insert(0, result_data)
                        if len(app.storage.user['history']) > 50:
                            app.storage.user['history'] = app.storage.user['history'][:50]

                        # 更新UI
                        update_result_display(result_data)
                        update_history_display()

                        test_button.props(remove='loading')

                        if result['ok']:
                            ui.notify('测试成功！', type='positive')
                        else:
                            ui.notify('测试完成，但有错误', type='warning')

                    test_button = ui.button('开始检测', on_click=run_test) \
                        .classes('w-full mt-5 action-btn-primary').props('no-caps')

                    # curl 显示
                    with ui.card().classes('p-5 mt-4 soft-bg'):
                        with ui.row().classes('w-full justify-between items-center mb-3'):
                            ui.html('<h2 class="section-title">生成的 curl</h2>')

                            async def copy_curl():
                                current_model = custom_model_input.value.strip() or selected_model
                                if endpoint_input.value and api_key_input.value and current_model:
                                    config = build_request_config(
                                        endpoint_input.value, api_key_input.value,
                                        current_model, selected_format, prompt_input.value
                                    )
                                    curl_text = build_curl(config, app.storage.user.get('proxy_config', {}))
                                    ui.run_javascript(f'navigator.clipboard.writeText({json.dumps(curl_text)})')
                                    ui.notify('已复制到剪贴板', type='positive')

                            ui.button('复制 curl', icon='content_copy',
                                     on_click=copy_curl).props('flat dense no-caps').classes('subtle-btn')

                        curl_display = ui.html(render_code_block('请填写完整配置后自动生成'))

                        def update_curl_display():
                            current_model = custom_model_input.value.strip() or selected_model
                            if endpoint_input.value and api_key_input.value and current_model:
                                config = build_request_config(
                                    endpoint_input.value, api_key_input.value,
                                    current_model, selected_format, prompt_input.value
                                )
                                curl_text = build_curl(config, app.storage.user.get('proxy_config', {}))
                                curl_display.content = render_code_block(curl_text)
                            else:
                                curl_display.content = render_code_block('请填写完整配置后自动生成')

                        # 监听变化自动更新curl
                        endpoint_input.on('input', lambda: update_curl_display())
                        api_key_input.on('input', lambda: update_curl_display())

                    # 测试结果
                    with ui.card().classes('p-5 mt-4'):
                        ui.html('<h2 class="section-title mb-4">测试结果</h2>')
                        result_container = ui.column().classes('w-full')

                        with result_container:
                            ui.html('<div style="padding: 20px; border: 1px dashed var(--border); border-radius: 18px; text-align: center; color: var(--muted); background: var(--surface-soft);">填写配置后即可开始检测。</div>')

                        def update_result_display(data):
                            result_container.clear()
                            with result_container:
                                result = data['result']
                                config = data['config']

                                status_color = 'positive' if result['ok'] else 'negative'
                                ui.badge(result['status_code'], color=status_color).classes('text-base mb-4')

                                with ui.row().classes('w-full gap-4'):
                                    with ui.card().classes('flex-1 result-card'):
                                        ui.html(render_kv_grid([
                                            ('状态码', result['status_code']),
                                            ('耗时', result['duration']),
                                            ('请求方式', config['method']),
                                            ('请求地址', config['url']),
                                        ]))

                                    with ui.card().classes('flex-1 result-card'):
                                        ui.html(render_kv_grid([
                                            ('内容类型', result['content_type']),
                                            ('响应长度', result['response_size']),
                                            ('JSON 解析', result['json_state']),
                                            ('失败提示', result['failure_hint']),
                                        ]))

                                ui.label('原始响应').classes('font-bold text-lg mt-6 mb-2')
                                ui.html(render_code_block(result['response_text']))

                                if result['json_text']:
                                    ui.label('解析后的 JSON').classes('font-bold text-lg mt-6 mb-2')
                                    ui.html(render_code_block(result['json_text']))

                # 右侧：历史记录
                with ui.column().classes('main-panel'):
                    with ui.card().classes('main-panel-card p-5'):
                        with ui.row().classes('w-full justify-between items-center mb-3'):
                            with ui.column():
                                ui.html('<h2 class="section-title">测试历史</h2>')
                                ui.html('<div class="helper-text" style="margin-top: 4px;">每次请求都会自动保存到这里</div>')

                            def clear_history():
                                app.storage.user['history'] = []
                                ui.notify('历史已清空', type='info')
                                update_history_display()

                            ui.button('清空', icon='delete', on_click=clear_history).props('flat dense no-caps color=negative size=sm').classes('subtle-btn')

                        history_container = ui.column().classes('w-full gap-2')

                        def update_history_display():
                            history_container.clear()
                            with history_container:
                                if not app.storage.user['history']:
                                    ui.label('还没有测试记录，开始检测后会显示在这里。') \
                                        .classes('text-center p-6').style('color: var(--muted);')
                                else:
                                    def make_delete_history_handler(entry_data):
                                        def handler():
                                            try:
                                                app.storage.user['history'].remove(entry_data)
                                            except ValueError:
                                                pass
                                            ui.notify('已删除该条历史', type='info')
                                            update_history_display()
                                        return handler

                                    for i, entry in enumerate(app.storage.user['history'][:10]):
                                        result = entry['result']
                                        config = entry['config']
                                        curl_text = entry['curl']

                                        # 遮罩API Key
                                        api_key = config['headers'].get('x-api-key') or config['headers'].get('authorization', '').replace('Bearer ', '')
                                        masked_key = api_key[:8] + '...' + api_key[-4:] if len(api_key) > 12 else api_key

                                        timestamp = format_timestamp_text(result.get('timestamp', ''))

                                        # 构建标题和副标题
                                        title = f"{result['status_code']} · {result['duration']} · {config['model']} · {masked_key}"
                                        format_title = get_format_title(infer_config_format_id(config))
                                        subtitle = f"{format_title} · {shorten_text(config['url'], 120)}"

                                        expansion = ui.expansion().classes('w-full')
                                        with expansion.add_slot('header'):
                                            with ui.row().classes('history-head-wrap no-wrap'):
                                                ui.html(render_expansion_header(
                                                    status_text=result['status_code'],
                                                    status_variant='success' if result['ok'] else 'danger',
                                                    title=title,
                                                    subtitle=subtitle,
                                                    chips=[
                                                        (config['model'], 'muted'),
                                                        (masked_key, 'info'),
                                                        (timestamp or '未记录时间', 'muted'),
                                                    ],
                                                    right_title=result['duration'],
                                                    right_subtitle=shorten_text(result['failure_hint'] if not result['ok'] else '请求成功', 64),
                                                )).classes('history-head-content')
                                                with ui.row().classes('history-head-actions'):
                                                    ui.button(
                                                        icon='delete',
                                                        on_click=make_delete_history_handler(entry),
                                                    ).props('flat dense round color=negative size=sm').tooltip('删除该条历史')

                                        with expansion:
                                            with ui.column().classes('record-body w-full gap-0'):
                                                ui.label('curl 命令:').classes('font-bold mt-2 mb-1')
                                                ui.html(render_code_block(curl_text, 'font-size: 11px;'))

                                                ui.label('完整响应:').classes('font-bold mt-2 mb-1')
                                                ui.html(render_code_block(
                                                    result['response_text'],
                                                    'font-size: 11px; max-height: none; overflow: visible;',
                                                ))

                                                def make_copy_handler(text):
                                                    def handler():
                                                        ui.run_javascript(f'navigator.clipboard.writeText({json.dumps(text)})')
                                                        ui.notify('已复制', type='positive')
                                                    return handler

                                                def make_add_monitor_handler(entry_data):
                                                    def handler():
                                                        monitor_id = f"mon-{int(time.time())}-{i}"
                                                        monitor_entry = {
                                                            'id': monitor_id,
                                                            'config': entry_data['config'],
                                                            'format_id': infer_config_format_id(entry_data['config']),
                                                            'curl': entry_data['curl'],
                                                            'last_result': entry_data['result'],
                                                            'added_at': datetime.now().isoformat(),
                                                        }
                                                        app.storage.user['monitors'].insert(0, monitor_entry)
                                                        ui.notify('已添加到长期监测', type='positive')
                                                        update_monitors_display()
                                                    return handler

                                                with ui.row().classes('gap-2 mt-2'):
                                                    ui.button('复制 curl', icon='content_copy',
                                                             on_click=make_copy_handler(curl_text)).props('flat dense size=sm')
                                                    ui.button('加入长期监测', icon='monitor_heart',
                                                             on_click=make_add_monitor_handler(entry)).props('flat dense size=sm color=primary')

                        update_history_display()

                    # 长期监测面板
                    with ui.card().classes('main-panel-card p-5 mt-4'):
                        with ui.row().classes('w-full justify-between items-center mb-3'):
                            with ui.column():
                                ui.html('<h2 class="section-title">长期监测</h2>')
                                ui.html('<div class="helper-text" style="margin-top: 4px;">从历史中添加，可随时重复执行</div>')

                            monitor_filter = {'value': 'all'}
                            monitor_filter_buttons = {}
                            monitor_filter_options = [
                                ('all', '全部'),
                                ('anthropic', 'Anthropic 消息格式'),
                                ('openai-chat', 'OpenAI v1 格式'),
                                ('openai-responses', 'OpenAI Responses API'),
                            ]

                            def update_monitor_filter_buttons():
                                for value, button in monitor_filter_buttons.items():
                                    if value == monitor_filter['value']:
                                        button.props(remove='outline').props('unelevated')
                                    else:
                                        button.props(remove='unelevated').props('outline')

                            def make_monitor_filter_handler(value):
                                def handler():
                                    monitor_filter['value'] = value
                                    update_monitor_filter_buttons()
                                    update_monitors_display()
                                return handler

                            with ui.row().classes('gap-2 flex-wrap'):
                                for value, label in monitor_filter_options:
                                    monitor_filter_buttons[value] = ui.button(
                                        label,
                                        on_click=make_monitor_filter_handler(value),
                                    ).props('dense no-caps outline size=sm').classes('subtle-btn')

                            update_monitor_filter_buttons()

                            # 一键测试状态控制
                            is_running = {'value': False}
                            should_pause = {'value': False}

                            async def run_all_monitors():
                                if not app.storage.user['monitors']:
                                    ui.notify('没有监测项', type='warning')
                                    return

                                if is_running['value']:
                                    ui.notify('测试正在进行中', type='warning')
                                    return

                                is_running['value'] = True
                                should_pause['value'] = False
                                run_all_btn.props('loading')
                                pause_btn.set_enabled(True)
                                pause_btn.props(remove='loading')

                                try:
                                    monitors_to_run = [
                                        monitor for monitor in app.storage.user['monitors']
                                        if monitor_filter['value'] == 'all'
                                        or infer_monitor_format_id(monitor) == monitor_filter['value']
                                    ]
                                    if not monitors_to_run:
                                        ui.notify('当前筛选下没有监测项', type='warning')
                                        return

                                    total = len(monitors_to_run)
                                    for i, monitor in enumerate(monitors_to_run, 1):
                                        if should_pause['value']:
                                            ui.notify(f'已暂停（已完成 {i-1}/{total}）', type='info')
                                            break

                                        progress_label.content = f'<div class="helper-text" style="color: var(--primary);">正在测试 {i}/{total}：{shorten_text(monitor["config"]["url"], 80)}</div>'

                                        try:
                                            result = await execute_request(monitor['config'])
                                        except Exception as e:
                                            result = {
                                                'ok': False,
                                                'status_code': 'Failed',
                                                'duration': '0 ms',
                                                'content_type': '-',
                                                'response_size': '0 字符',
                                                'json_state': '-',
                                                'failure_hint': str(e),
                                                'response_text': str(e),
                                                'json_text': '',
                                                'timestamp': datetime.now().isoformat(),
                                            }

                                        monitor['last_result'] = result
                                        monitor['last_tested_at'] = datetime.now().isoformat()
                                        update_monitors_display()

                                        if i < total and not should_pause['value']:
                                            await asyncio.sleep(1)

                                    if not should_pause['value']:
                                        progress_label.content = ''
                                        ui.notify('所有监测项已测试完成', type='positive')
                                finally:
                                    run_all_btn.props(remove='loading')
                                    pause_btn.set_enabled(False)
                                    pause_btn.props(remove='loading')
                                    is_running['value'] = False
                                    if should_pause['value']:
                                        progress_label.content = ''

                            def pause_testing():
                                if is_running['value']:
                                    should_pause['value'] = True
                                    pause_btn.props('loading')
                                    ui.notify('将在当前测试完成后暂停...', type='info')

                            with ui.row().classes('gap-2'):
                                run_all_btn = ui.button('一键测试全部', icon='play_arrow', on_click=run_all_monitors).props('no-caps dense').classes('action-btn-primary')
                                pause_btn = ui.button('暂停', icon='pause', on_click=pause_testing).props('no-caps dense outline')
                                pause_btn.set_enabled(False)

                        # 进度提示标签
                        progress_label = ui.html('<div class="helper-text" style="min-height: 20px;"></div>')

                        monitors_container = ui.column().classes('w-full gap-2')

                        def update_monitors_display():
                            monitors_container.clear()
                            with monitors_container:
                                if not app.storage.user['monitors']:
                                    ui.label('还没有监测项。在测试历史中点击「加入长期监测」即可添加。') \
                                        .classes('text-center p-6').style('color: var(--muted);')
                                else:
                                    visible_monitors = [
                                        monitor for monitor in app.storage.user['monitors']
                                        if monitor_filter['value'] == 'all'
                                        or infer_monitor_format_id(monitor) == monitor_filter['value']
                                    ]
                                    if not visible_monitors:
                                        selected_label = dict(monitor_filter_options).get(monitor_filter['value'], '当前格式')
                                        ui.label(f'没有 {selected_label} 的监测项。') \
                                            .classes('text-center p-6').style('color: var(--muted);')
                                        return

                                    for monitor in visible_monitors:
                                        config = monitor['config']
                                        curl_text = monitor['curl']
                                        last_result = monitor.get('last_result')
                                        last_tested_at = format_timestamp_text(monitor.get('last_tested_at', ''))

                                        # 遮罩API Key
                                        api_key = config['headers'].get('x-api-key') or config['headers'].get('authorization', '').replace('Bearer ', '')
                                        masked_key = api_key[:6] + '...' + api_key[-4:] if len(api_key) > 10 else api_key

                                        title = f"{config['model']} · {masked_key}"
                                        if last_result:
                                            title = f"{last_result['status_code']} · {last_result['duration']} · {title}"

                                        format_id = infer_monitor_format_id(monitor)
                                        subtitle = f"{get_format_title(format_id)} · {shorten_text(config['url'], 120)}"
                                        right_title = masked_key
                                        right_subtitle = f"{last_tested_at or '未测试'} · {shorten_text(last_result['failure_hint'] if last_result and not last_result['ok'] else '等待下一次测试', 64)}"

                                        expansion = ui.expansion().classes('w-full')
                                        with expansion.add_slot('header'):
                                            with ui.row().classes('monitor-head-wrap no-wrap'):
                                                ui.html(render_expansion_header(
                                                    status_text=last_result['status_code'] if last_result else '待测试',
                                                    status_variant='success' if last_result and last_result['ok'] else 'muted',
                                                    title=title,
                                                    subtitle=subtitle,
                                                    chips=[
                                                        (config['model'], 'muted'),
                                                        (get_format_title(format_id), 'info'),
                                                        (masked_key, 'info'),
                                                        (last_tested_at or '未测试', 'muted'),
                                                    ],
                                                    right_title=right_title,
                                                    right_subtitle=right_subtitle,
                                                )).classes('monitor-head-content')
                                                with ui.row().classes('monitor-head-actions'):
                                                    ui.icon('monitor_heart').classes('text-primary').style('font-size: 20px;')

                                        with expansion:
                                            with ui.column().classes('record-body w-full gap-0'):
                                                ui.label('curl 命令:').classes('font-bold mt-2 mb-1')
                                                ui.html(render_code_block(curl_text, 'font-size: 11px;'))

                                                if last_result:
                                                    ui.label('最后测试完整响应:').classes('font-bold mt-2 mb-1')
                                                    ui.html(render_code_block(
                                                        last_result['response_text'],
                                                        'font-size: 11px; max-height: none; overflow: visible;',
                                                    ))

                                                def make_monitor_copy_handler(text):
                                                    def handler():
                                                        ui.run_javascript(f'navigator.clipboard.writeText({json.dumps(text)})')
                                                        ui.notify('已复制', type='positive')
                                                    return handler

                                                def make_monitor_run_handler(mon):
                                                    async def handler():
                                                        result = await execute_request(mon['config'])
                                                        mon['last_result'] = result
                                                        mon['last_tested_at'] = datetime.now().isoformat()
                                                        update_monitors_display()
                                                        if result['ok']:
                                                            ui.notify('测试成功', type='positive')
                                                        else:
                                                            ui.notify('测试完成，但有错误', type='warning')
                                                    return handler

                                                def make_monitor_remove_handler(mon_id):
                                                    def handler():
                                                        app.storage.user['monitors'] = [m for m in app.storage.user['monitors'] if m['id'] != mon_id]
                                                        ui.notify('已移除', type='info')
                                                        update_monitors_display()
                                                    return handler

                                                with ui.row().classes('gap-2 mt-2'):
                                                    ui.button('测试', icon='play_arrow',
                                                             on_click=make_monitor_run_handler(monitor)).props('flat dense size=sm color=primary')
                                                    ui.button('复制 curl', icon='content_copy',
                                                             on_click=make_monitor_copy_handler(curl_text)).props('flat dense size=sm')
                                                    ui.button('移除', icon='delete',
                                                             on_click=make_monitor_remove_handler(monitor['id'])).props('flat dense size=sm color=negative')

                        update_monitors_display()

                        # 导入导出功能
                        with ui.card().classes('main-panel-card p-5 mt-4').style('background: var(--surface-soft);'):
                            ui.html('<h3 class="section-title" style="font-size: 16px; margin-bottom: 12px;">导入 / 导出监测配置</h3>')
                            ui.html('<div class="helper-text" style="margin-bottom: 12px;">导出所有监测项为JSON，或从JSON导入监测项</div>')

                            import_export_textarea = ui.textarea('JSON 配置', placeholder='导出时会显示在这里，导入时粘贴JSON到这里') \
                                .classes('w-full').props('outlined rows=6').style('font-family: monospace; font-size: 12px;')

                            def export_monitors():
                                if not app.storage.user['monitors']:
                                    ui.notify('没有监测项可导出', type='warning')
                                    return

                                export_data = {
                                    'version': '1.0',
                                    'exported_at': datetime.now().isoformat(),
                                    'monitors': app.storage.user['monitors']
                                }
                                json_str = json.dumps(export_data, ensure_ascii=False, indent=2)
                                import_export_textarea.value = json_str
                                ui.notify(f'已导出 {len(app.storage.user["monitors"])} 个监测项', type='positive')

                            def import_monitors():
                                json_str = import_export_textarea.value.strip()
                                if not json_str:
                                    ui.notify('请先粘贴JSON配置', type='warning')
                                    return

                                if not json_str.strip():
                                    ui.notify('JSON 内容为空', type='warning')
                                    return

                                try:
                                    data = json.loads(json_str)

                                    # 兼容两种格式：带version的新格式和直接数组的旧格式
                                    if isinstance(data, dict) and 'monitors' in data:
                                        monitors_to_import = data['monitors']
                                    elif isinstance(data, list):
                                        monitors_to_import = data
                                    else:
                                        ui.notify('JSON格式不正确，需要monitors数组', type='negative')
                                        return

                                    if not isinstance(monitors_to_import, list):
                                        ui.notify('monitors必须是数组格式', type='negative')
                                        return

                                    # 生成新的ID避免冲突
                                    imported_count = 0
                                    for monitor in monitors_to_import:
                                        if isinstance(monitor, dict) and 'config' in monitor and 'curl' in monitor:
                                            new_monitor = {
                                            'id': f"mon-import-{int(time.time() * 1000)}-{imported_count}",
                                            'config': monitor['config'],
                                            'format_id': monitor.get('format_id') or infer_config_format_id(monitor['config']),
                                            'curl': monitor['curl'],
                                            'last_result': monitor.get('last_result'),
                                            'added_at': datetime.now().isoformat(),
                                        }
                                            app.storage.user['monitors'].insert(0, new_monitor)
                                            imported_count += 1

                                    if imported_count > 0:
                                        update_monitors_display()
                                        import_export_textarea.value = ''
                                        ui.notify(f'成功导入 {imported_count} 个监测项', type='positive')
                                    else:
                                        ui.notify('没有找到有效的监测项', type='warning')

                                except json.JSONDecodeError as e:
                                    ui.notify(f'JSON解析失败: {str(e)}', type='negative')
                                except Exception as e:
                                    ui.notify(f'导入失败: {str(e)}', type='negative')

                            with ui.row().classes('gap-2 mt-2'):
                                ui.button('导出全部', icon='download', on_click=export_monitors).props('no-caps dense').classes('action-btn-primary')
                                ui.button('导入', icon='upload', on_click=import_monitors).props('outline no-caps dense')

if __name__ == '__main__':
    run_host = os.environ.get('API_KEY_TESTER_HOST', '0.0.0.0')
    run_port = int(os.environ.get('API_KEY_TESTER_PORT', os.environ.get('PORT', '80')))
    print(f'API Key 连通性测试工具启动中: http://{run_host}:{run_port}', flush=True)
    ui.run(
        host=run_host,
        port=run_port,
        title='API Key 连通性测试',
        favicon='🔑',
        dark=False,
        show=False,
        show_welcome_message=False,
        reload=False,
        storage_secret='your-secret-key-change-this-in-production',
    )
