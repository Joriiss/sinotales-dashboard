"""
Fetch available LLM models from provider APIs (Ollama, OpenAI, Gemini).
"""
from __future__ import annotations

import requests
from django.conf import settings

GEMINI_MODELS_URL = 'https://generativelanguage.googleapis.com/v1beta/models'
OPENAI_MODELS_URL = 'https://api.openai.com/v1/models'

GEMINI_DEFAULT_FALLBACK = 'gemini-3.6-flash'
OPENAI_DEFAULT_FALLBACK = 'gpt-4o-mini'


def _strip_models_prefix(name: str) -> str:
    if name.startswith('models/'):
        return name[7:]
    return name


def fetch_gemini_models(api_key: str) -> list[str]:
    """List Gemini models that support generateContent."""
    models: list[str] = []
    page_token = None
    headers = {'x-goog-api-key': api_key}

    while True:
        params: dict = {'pageSize': 100}
        if page_token:
            params['pageToken'] = page_token
        response = requests.get(
            GEMINI_MODELS_URL,
            headers=headers,
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        for model in data.get('models', []):
            methods = model.get('supportedGenerationMethods', [])
            if 'generateContent' not in methods:
                continue
            short_name = _strip_models_prefix(model.get('name', ''))
            if not short_name.startswith('gemini'):
                continue
            if any(
                skip in short_name.lower()
                for skip in ('embed', 'aqa', 'tts', 'image', 'computer')
            ):
                continue
            models.append(short_name)

        page_token = data.get('nextPageToken')
        if not page_token:
            break

    return _sort_gemini_models(models)


def _sort_gemini_models(models: list[str]) -> list[str]:
    """Prefer stable pro/flash models; deprioritize preview/experimental."""

    def sort_key(name: str) -> tuple:
        n = name.lower()
        priority = 5
        if '3.6-pro' in n:
            priority = 0
        elif '3.6-flash' in n and 'lite' not in n:
            priority = 0
        elif '3-pro' in n or '3.0-pro' in n:
            priority = 1
        elif '2.5-pro' in n:
            priority = 1
        elif '2.5-flash-lite' in n:
            priority = 3
        elif '2.5-flash' in n or '3.6-flash-lite' in n:
            priority = 2
        elif 'pro' in n:
            priority = 2
        elif 'flash' in n:
            priority = 3
        if 'preview' in n or 'experimental' in n or 'exp' in n:
            priority += 4
        return (priority, n)

    return sorted(models, key=sort_key)


def fetch_openai_models(api_key: str) -> list[str]:
    """List OpenAI chat models available to the API key."""
    response = requests.get(
        OPENAI_MODELS_URL,
        headers={'Authorization': f'Bearer {api_key}'},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()

    models = []
    for entry in data.get('data', []):
        model_id = entry.get('id', '')
        if not model_id.startswith('gpt-'):
            continue
        lower = model_id.lower()
        if any(
            skip in lower
            for skip in (
                'audio',
                'realtime',
                'transcribe',
                'search',
                'codex',
                'instruct',
            )
        ):
            continue
        models.append(model_id)

    return sorted(models, key=lambda m: m.lower(), reverse=True)


def get_default_gemini_model() -> str:
    api_key = getattr(settings, 'GEMINI_API_KEY', None)
    if api_key:
        try:
            models = fetch_gemini_models(api_key)
            if models:
                return models[0]
        except Exception:
            pass
    return GEMINI_DEFAULT_FALLBACK


def get_default_model_for_provider(provider: str) -> str:
    """Default model when none is specified for a provider."""
    provider = provider.strip().lower()
    if provider == 'ollama':
        try:
            from .models import Settings
            return Settings.get_settings().default_tagging_model
        except Exception:
            return 'gpt-oss:20b-cloud'
    if provider == 'openai':
        return get_default_openai_model()
    if provider == 'gemini':
        return get_default_gemini_model()
    raise ValueError(f'Unsupported provider: {provider}')


def get_default_openai_model() -> str:
    api_key = getattr(settings, 'OPENAI_API_KEY', None)
    if api_key:
        try:
            models = fetch_openai_models(api_key)
            for preferred in ('gpt-4o-mini', 'gpt-4o', 'gpt-4-turbo', 'gpt-3.5-turbo'):
                if preferred in models:
                    return preferred
            if models:
                return models[0]
        except Exception:
            pass
    return OPENAI_DEFAULT_FALLBACK


def list_models_for_provider(provider: str) -> tuple[list[str] | None, str | None]:
    """
    Return (models, error) for a provider name.
    models is None when error is set.
    """
    provider = provider.strip().lower()

    if provider == 'gemini':
        api_key = getattr(settings, 'GEMINI_API_KEY', None)
        if not api_key:
            return None, 'GEMINI_API_KEY is not set in settings'
        try:
            models = fetch_gemini_models(api_key)
            if not models:
                return None, 'No Gemini models returned by the API'
            return models, None
        except requests.exceptions.HTTPError as e:
            detail = ''
            try:
                detail = e.response.json().get('error', {}).get('message', '')
            except Exception:
                pass
            return None, detail or str(e)
        except Exception as e:
            return None, str(e)

    if provider == 'openai':
        api_key = getattr(settings, 'OPENAI_API_KEY', None)
        if not api_key:
            return None, 'OPENAI_API_KEY is not set in settings'
        try:
            models = fetch_openai_models(api_key)
            if not models:
                return None, 'No OpenAI chat models returned by the API'
            return models, None
        except requests.exceptions.HTTPError as e:
            detail = ''
            try:
                detail = e.response.json().get('error', {}).get('message', '')
            except Exception:
                pass
            return None, detail or str(e)
        except Exception as e:
            return None, str(e)

    return None, 'Invalid provider'
