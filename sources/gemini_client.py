"""
Gemini generateContent via REST, with controllable egress.

The official google.generativeai SDK often hits "User location is not supported"
on VPS hosts (IPv6 geotagging / blocked datacenter IPs). This client:
- calls the public REST API with requests/urllib3
- can force IPv4-only DNS for those requests
- can route through GEMINI_HTTP_PROXY / HTTPS_PROXY
"""

from __future__ import annotations

import json
import logging
import socket
from typing import Any, Optional
from urllib.parse import quote

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

GEMINI_API_BASE = 'https://generativelanguage.googleapis.com/v1beta'

DEFAULT_SAFETY_SETTINGS = [
    {'category': 'HARM_CATEGORY_HARASSMENT', 'threshold': 'BLOCK_NONE'},
    {'category': 'HARM_CATEGORY_HATE_SPEECH', 'threshold': 'BLOCK_NONE'},
    {'category': 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'threshold': 'BLOCK_NONE'},
    {'category': 'HARM_CATEGORY_DANGEROUS_CONTENT', 'threshold': 'BLOCK_ONLY_HIGH'},
]


class _IPv4HTTPAdapter(HTTPAdapter):
    """Force urllib3 connections to resolve/connect over IPv4 only."""

    def init_poolmanager(self, *args, **kwargs):
        import urllib3.util.connection as urllib3_cn

        urllib3_cn.allowed_gai_family = lambda: socket.AF_INET
        return super().init_poolmanager(*args, **kwargs)


def _truthy(value: Optional[str], default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def _proxy_url() -> Optional[str]:
    return (
        getattr(settings, 'GEMINI_HTTP_PROXY', None)
        or getattr(settings, 'HTTPS_PROXY', None)
        or None
    )


def _force_ipv4() -> bool:
    # Default on: same intent as n8n NODE_OPTIONS=--dns-result-order=ipv4first
    return _truthy(getattr(settings, 'GEMINI_FORCE_IPV4', None) or '1', default=True)


_urllib3_ipv4_applied = False


def _apply_urllib3_ipv4_only() -> None:
    """Make urllib3/requests resolve only A records (IPv4)."""
    global _urllib3_ipv4_applied
    if _urllib3_ipv4_applied:
        return
    import urllib3.util.connection as urllib3_cn

    urllib3_cn.allowed_gai_family = lambda: socket.AF_INET
    _urllib3_ipv4_applied = True


def build_gemini_session() -> requests.Session:
    session = requests.Session()
    if _force_ipv4():
        _apply_urllib3_ipv4_only()
        adapter = _IPv4HTTPAdapter()
        session.mount('https://', adapter)
        session.mount('http://', adapter)

    proxy = _proxy_url()
    if proxy:
        session.proxies.update({'http': proxy, 'https': proxy})
        logger.info('Gemini REST client using proxy %s', proxy.split('@')[-1])

    return session


def generate_content(
    prompt: str,
    model: str,
    *,
    max_tokens: int = 2000,
    temperature: float = 0.7,
    api_key: Optional[str] = None,
    timeout: int = 300,
) -> str:
    """
    Call models/{model}:generateContent and return response text.
    Raises Exception with a clear message on API / empty / blocked responses.
    """
    api_key = api_key or getattr(settings, 'GEMINI_API_KEY', None)
    if not api_key:
        raise ValueError('GEMINI_API_KEY is not set in settings. Please configure it to use Gemini.')

    model_name = model[7:] if model.startswith('models/') else model
    url = f'{GEMINI_API_BASE}/models/{quote(model_name, safe="")}:generateContent'

    payload: dict[str, Any] = {
        'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
        'generationConfig': {
            'temperature': temperature,
            'maxOutputTokens': max_tokens,
        },
        'safetySettings': DEFAULT_SAFETY_SETTINGS,
    }

    session = build_gemini_session()
    try:
        response = session.post(
            url,
            params={'key': api_key},
            headers={'Content-Type': 'application/json'},
            data=json.dumps(payload),
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise Exception(f'Gemini API request failed: {e}') from e

    try:
        data = response.json()
    except ValueError:
        raise Exception(f'Gemini API returned non-JSON ({response.status_code}): {response.text[:500]}')

    if response.status_code >= 400:
        err = data.get('error') or {}
        message = err.get('message') or response.text[:500]
        raise Exception(f'Gemini API error: {response.status_code} {message}')

    return _extract_text(data, max_tokens=max_tokens)


def _extract_text(data: dict, *, max_tokens: int) -> str:
    candidates = data.get('candidates') or []
    if not candidates:
        prompt_feedback = data.get('promptFeedback') or {}
        block_reason = prompt_feedback.get('blockReason')
        if block_reason:
            raise Exception(f'Gemini API response was blocked ({block_reason}).')
        raise Exception('Gemini API returned an empty response (no candidates).')

    candidate = candidates[0]
    finish_reason = str(candidate.get('finishReason') or '').upper()

    if finish_reason == 'MAX_TOKENS':
        # Still try to return partial text if present
        text = _parts_text(candidate)
        if text:
            return text
        raise Exception(
            f'Gemini API response hit the token limit (MAX_TOKENS). '
            f'The max_output_tokens ({max_tokens}) is too low.'
        )
    if finish_reason == 'SAFETY':
        raise Exception('Gemini API response was blocked (SAFETY).')
    if finish_reason == 'RECITATION':
        raise Exception('Gemini API response was blocked (RECITATION).')
    if finish_reason and finish_reason not in ('STOP', 'FINISH_REASON_UNSPECIFIED', ''):
        text = _parts_text(candidate)
        if not text:
            raise Exception(f'Gemini API response was blocked (finish_reason: {finish_reason}).')

    text = _parts_text(candidate)
    if not text:
        raise Exception(
            'Gemini API returned an empty response. '
            'The content may have been filtered or the model could not generate a response.'
        )
    return text


def _parts_text(candidate: dict) -> str:
    content = candidate.get('content') or {}
    parts = content.get('parts') or []
    chunks = []
    for part in parts:
        if isinstance(part, dict) and part.get('text'):
            chunks.append(part['text'])
    return ''.join(chunks).strip()


def diagnose_egress(api_key: Optional[str] = None, model: str = 'gemini-2.0-flash') -> dict:
    """
    Small connectivity probe used by the management command.
    Does not print the API key.
    """
    api_key = api_key or getattr(settings, 'GEMINI_API_KEY', None)
    result = {
        'force_ipv4': _force_ipv4(),
        'proxy_configured': bool(_proxy_url()),
        'proxy_host': (_proxy_url() or '').split('@')[-1] if _proxy_url() else None,
        'host_ipv4': [],
        'host_ipv6': [],
        'list_models_ok': False,
        'generate_ok': False,
        'error': None,
        'egress_hint': None,
    }

    host = 'generativelanguage.googleapis.com'
    try:
        for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
            family, _, _, _, sockaddr = info
            ip = sockaddr[0]
            if family == socket.AF_INET:
                result['host_ipv4'].append(ip)
            elif family == socket.AF_INET6:
                result['host_ipv6'].append(ip)
    except OSError as e:
        result['error'] = f'DNS failed: {e}'
        return result

    if not api_key:
        result['error'] = 'GEMINI_API_KEY not set'
        return result

    session = build_gemini_session()
    try:
        # See which public IP Google (or a checker) might associate — best-effort.
        try:
            ip_resp = session.get('https://api.ipify.org?format=json', timeout=10)
            if ip_resp.ok:
                result['egress_hint'] = ip_resp.json().get('ip')
        except Exception:
            pass

        models_resp = session.get(
            f'{GEMINI_API_BASE}/models',
            params={'key': api_key, 'pageSize': 1},
            timeout=30,
        )
        if models_resp.status_code >= 400:
            err = models_resp.json().get('error', {}) if models_resp.content else {}
            result['error'] = err.get('message') or models_resp.text[:300]
            return result
        result['list_models_ok'] = True

        generate_content('Reply with exactly: ok', model, max_tokens=16, api_key=api_key, timeout=60)
        result['generate_ok'] = True
    except Exception as e:
        result['error'] = str(e)

    return result
