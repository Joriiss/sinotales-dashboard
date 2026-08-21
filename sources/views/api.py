from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.db.models.functions import Length
from django.http import JsonResponse
from django.conf import settings
from django.utils.html import json_script
from django.utils.text import slugify
from django.core.cache import cache
import json
from ..models import Source, Content, Tag, PostIdea, BlogPost, BlogPostImage, Settings
from ..utils import log_activity, is_idea_too_similar_with_embeddings
from ..rag_service import RAGService
from ..content_processing_service import ContentProcessingService
from ..embedding_service import EmbeddingService
from pgvector.django import CosineDistance
import random
import re
from datetime import datetime
from .utils import _validate_api_token, _parse_blog_content_sections, _format_acf_field, _format_faq_acf_fields
from .blog_posts import _parse_and_create_blog_post_images
from .post_ideas import _generate_post_ideas

_ANCHOR_STOPWORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'best', 'by', 'for', 'from', 'guide',
    'how', 'in', 'into', 'is', 'it', 'of', 'on', 'or', 'the', 'to', 'tips', 'travel',
    'with', 'your', 'you', 'first', 'time', 'china'
}

_GENERIC_ANCHOR_TOKENS = {
    'area', 'areas', 'city', 'countries', 'country', 'destination', 'destinations',
    'inside', 'local', 'place', 'places', 'region', 'spot', 'spots', 'trip', 'visit'
}


def _is_natural_anchor(anchor_text):
    """Require anchors to be specific phrases, not weak single words."""
    if not anchor_text:
        return False
    tokens = re.findall(r"[a-zA-Z0-9']+", anchor_text.lower())
    if len(tokens) < 2:
        return False
    if len(anchor_text.strip()) < 8:
        return False
    meaningful = [t for t in tokens if len(t) >= 3 and t not in _ANCHOR_STOPWORDS]
    if len(meaningful) < 2:
        return False
    if all(t in _GENERIC_ANCHOR_TOKENS for t in meaningful):
        return False
    return True


def _extract_anchor_candidate(source_plain_text, candidate_title):
    """
    Try to extract a natural anchor phrase from candidate title that exists in source text.
    Returns None when no clean/natural match is found.
    """
    source_lc = source_plain_text.lower()
    title_tokens = re.findall(r"[a-zA-Z0-9']+", candidate_title.lower())
    meaningful_tokens = [t for t in title_tokens if len(t) >= 4 and t not in _ANCHOR_STOPWORDS]

    # Prefer longer, more specific phrases
    for size in (4, 3, 2):
        for i in range(0, len(meaningful_tokens) - size + 1):
            phrase = ' '.join(meaningful_tokens[i:i + size])
            if phrase in source_lc and _is_natural_anchor(phrase):
                return phrase

    return None


def _build_internal_link_suggestions(source_post, limit=5):
    """Build ranked internal-link suggestions for a blog post object."""
    limit = max(1, min(int(limit), 10))
    source_tag_ids = list(source_post.tags.values_list('id', flat=True))
    source_plain_text = re.sub(r'<[^>]+>', ' ', source_post.content or '')
    source_plain_text = re.sub(r'\s+', ' ', source_plain_text).strip()

    candidate_qs = BlogPost.objects.filter(
        published=True
    ).exclude(
        pk=source_post.pk
    ).prefetch_related('tags')

    if source_tag_ids:
        candidates = list(
            candidate_qs.annotate(
                shared_tag_count=Count('tags', filter=Q(tags__in=source_tag_ids))
            ).order_by('-shared_tag_count', '-created_at').distinct()[:100]
        )
    else:
        candidates = list(candidate_qs.order_by('-created_at')[:100])
        for candidate in candidates:
            candidate.shared_tag_count = 0

    suggestions = []
    used_targets = set()
    for candidate in candidates:
        if len(suggestions) >= limit:
            break
        if candidate.pk in used_targets:
            continue

        shared_tags = []
        if source_tag_ids:
            shared_tags = list(
                candidate.tags.filter(id__in=source_tag_ids).values_list('name', flat=True)[:3]
            )

        target_url = candidate.online_url or f"/blog/{candidate.slug}/"
        anchor = _extract_anchor_candidate(source_plain_text, candidate.title)
        title_in_source = candidate.title.lower() in source_plain_text.lower() if source_plain_text else False

        if not anchor:
            continue

        shared_tag_count = int(getattr(candidate, 'shared_tag_count', 0))
        has_strong_relevance = shared_tag_count > 0 or bool(title_in_source)
        if not has_strong_relevance:
            continue

        suggestions.append({
            'target_post_id': candidate.id,
            'target_title': candidate.title,
            'target_slug': candidate.slug,
            'target_url': target_url,
            'suggested_anchor': anchor,
            'shared_tag_count': shared_tag_count,
            'shared_tags': shared_tags,
            'title_appears_in_source': title_in_source,
            'relevance_score': shared_tag_count + (1 if title_in_source else 0),
        })
        used_targets.add(candidate.pk)

    return suggestions


def _apply_internal_links_to_html(content_html, suggestions, max_links=5):
    """
    Insert internal links in <p> blocks only, avoiding paragraphs that already contain links.
    Returns (updated_html, applied_links).
    """
    max_links = max(1, min(int(max_links), 10))
    if not content_html or not suggestions:
        return content_html, []

    paragraph_pattern = re.compile(r'(<p\b[^>]*>)(.*?)(</p>)', re.IGNORECASE | re.DOTALL)
    matches = list(paragraph_pattern.finditer(content_html))
    if not matches:
        return content_html, []

    rebuilt_parts = []
    cursor = 0
    applied_links = []
    used_urls = set()

    for match in matches:
        rebuilt_parts.append(content_html[cursor:match.start()])
        open_tag, paragraph_inner, close_tag = match.group(1), match.group(2), match.group(3)
        updated_inner = paragraph_inner

        if '<a ' not in paragraph_inner.lower() and len(applied_links) < max_links:
            for suggestion in suggestions:
                if len(applied_links) >= max_links:
                    break

                anchor = (suggestion.get('suggested_anchor') or '').strip()
                target_url = (suggestion.get('target_url') or '').strip()
                target_post_id = suggestion.get('target_post_id')

                if not anchor or not target_url or target_url in used_urls:
                    continue

                escaped_anchor = re.escape(anchor)
                anchor_pattern = re.compile(rf'(?<!\w)({escaped_anchor})(?!\w)', re.IGNORECASE)

                if not anchor_pattern.search(updated_inner):
                    continue

                updated_inner, substitutions = anchor_pattern.subn(
                    rf'<a href="{target_url}">\1</a>',
                    updated_inner,
                    count=1
                )
                if substitutions > 0:
                    applied_links.append({
                        'target_post_id': target_post_id,
                        'target_url': target_url,
                        'anchor': anchor,
                    })
                    used_urls.add(target_url)
                    break

        rebuilt_parts.append(f'{open_tag}{updated_inner}{close_tag}')
        cursor = match.end()

    rebuilt_parts.append(content_html[cursor:])
    return ''.join(rebuilt_parts), applied_links


def _extract_json_object(raw_text):
    """Extract first JSON object from model output."""
    if not raw_text:
        return None
    text = raw_text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*```$', '', text)
    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        snippet = text[start:end + 1]
        try:
            return json.loads(snippet)
        except Exception:
            return None
    return None


def _build_linking_context_excerpt(content_html, max_chars=9000):
    """Build a compact context excerpt for AI link planning."""
    if not content_html:
        return ''
    # Keep text-rich blocks and drop large media markup noise.
    compact = re.sub(r'<img\b[^>]*>', ' ', content_html, flags=re.IGNORECASE)
    compact = re.sub(r'\[wpcode[^\]]*\]', ' ', compact, flags=re.IGNORECASE)
    compact = re.sub(r'\s+', ' ', compact).strip()
    return compact[:max_chars]


def _token_budget_for_internal_link_ai(provider, model):
    """Choose a safer max token budget for AI linking calls."""
    model_lc = (model or '').lower()
    if provider == 'gemini':
        if '3.1' in model_lc or 'pro' in model_lc:
            return 8000
        return 5000
    if provider == 'openai':
        return 5000
    return 4000


def _apply_internal_links_with_ai(content_html, suggestions, rag_service, provider, model, max_links=5):
    """
    Let the model propose natural links/anchors using suggestions as allowed targets.
    Returns (updated_html, applied_links, used_ai, failure_reason, failure_details).
    """
    if not content_html or not suggestions:
        return content_html, [], False, 'no_content_or_suggestions', {}

    max_links = max(1, min(int(max_links), 10))
    allowed = []
    for s in suggestions[:max_links]:
        allowed.append({
            'target_post_id': s.get('target_post_id'),
            'target_url': s.get('target_url'),
            'target_title': s.get('target_title'),
            'suggested_anchor': s.get('suggested_anchor'),
        })

    context_excerpt = _build_linking_context_excerpt(content_html, max_chars=9000)
    max_tokens = _token_budget_for_internal_link_ai(provider, model)

    prompt = f"""You are an SEO editor. Pick the most natural internal links for the provided article context.

Rules:
1) Use ONLY the URLs from allowed_links.
2) Add at most {max_links} links total.
3) Use natural multi-word anchors (no weak single words like "city" or "inside").
4) Prefer clear topical relevance over quantity (it is okay to return fewer links).
5) Return strict JSON only, with key:
   - "applied_links": array of objects with keys "target_post_id", "target_url", "anchor"

allowed_links:
{json.dumps(allowed, ensure_ascii=False)}

article_context:
{context_excerpt}
"""

    if provider == 'ollama':
        raw_response = rag_service._call_ollama(prompt, model, max_tokens=max_tokens)
    elif provider == 'openai':
        raw_response = rag_service._call_openai(prompt, model, max_tokens=max_tokens)
    else:
        raw_response = rag_service._call_gemini(prompt, model, max_tokens=max_tokens)

    parsed = _extract_json_object(raw_response)
    if not parsed or not isinstance(parsed, dict):
        return content_html, [], False, 'invalid_json', {'response_preview': (raw_response or '')[:500]}

    applied_links = parsed.get('applied_links', [])
    if not isinstance(applied_links, list):
        return content_html, [], False, 'invalid_applied_links_type', {'type': str(type(applied_links))}

    allowed_urls = {item['target_url'] for item in allowed if item.get('target_url')}
    sanitized_links = []
    invalid_url_count = 0
    weak_anchor_count = 0
    non_dict_count = 0
    for item in applied_links[:max_links]:
        if not isinstance(item, dict):
            non_dict_count += 1
            continue
        target_url = (item.get('target_url') or '').strip()
        anchor = (item.get('anchor') or '').strip()
        if not target_url or target_url not in allowed_urls:
            invalid_url_count += 1
            continue
        if not _is_natural_anchor(anchor):
            weak_anchor_count += 1
            continue
        sanitized_links.append({
            'target_post_id': item.get('target_post_id'),
            'target_url': target_url,
            'anchor': anchor,
        })

    if not sanitized_links:
        return content_html, [], False, 'no_valid_links_after_validation', {
            'proposed_links_count': len(applied_links),
            'invalid_url_count': invalid_url_count,
            'weak_anchor_count': weak_anchor_count,
            'non_dict_count': non_dict_count,
        }

    ai_suggestions = []
    for item in sanitized_links:
        ai_suggestions.append({
            'target_post_id': item.get('target_post_id'),
            'target_url': item.get('target_url'),
            'suggested_anchor': item.get('anchor'),
        })

    updated_html, applied = _apply_internal_links_to_html(
        content_html,
        ai_suggestions,
        max_links=max_links
    )
    if not applied:
        return content_html, [], False, 'no_insertions_from_ai_plan', {
            'validated_ai_links_count': len(sanitized_links),
        }

    return updated_html, applied, True, None, {}


@csrf_exempt
def internal_link_suggestions_api(request):
    """
    API endpoint to suggest internal links for a blog post (token-based authentication).

    Query parameters:
    - post_id: BlogPost ID (optional if slug is provided)
    - slug: BlogPost slug (optional if post_id is provided)
    - limit: Number of suggestions to return (default 5, max 10)
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response

    try:
        post_id_param = (request.GET.get('post_id') or '').strip()
        slug_param = (request.GET.get('slug') or '').strip()
        limit_param = (request.GET.get('limit') or '').strip()

        if not post_id_param and not slug_param:
            return JsonResponse({
                'success': False,
                'error': 'Either post_id or slug is required'
            }, status=400)

        limit = 5
        if limit_param:
            try:
                limit = int(limit_param)
            except ValueError:
                return JsonResponse({
                    'success': False,
                    'error': 'limit must be a valid integer'
                }, status=400)
        limit = max(1, min(limit, 10))

        # Resolve source post
        source_post = None
        if post_id_param:
            try:
                source_post = BlogPost.objects.prefetch_related('tags').get(pk=int(post_id_param))
            except (ValueError, BlogPost.DoesNotExist):
                return JsonResponse({
                    'success': False,
                    'error': f'Blog post with id {post_id_param} not found'
                }, status=404)
        else:
            try:
                source_post = BlogPost.objects.prefetch_related('tags').get(slug=slug_param)
            except BlogPost.DoesNotExist:
                return JsonResponse({
                    'success': False,
                    'error': f'Blog post with slug "{slug_param}" not found'
                }, status=404)

        suggestions = _build_internal_link_suggestions(source_post, limit=limit)

        return JsonResponse({
            'success': True,
            'source_post': {
                'id': source_post.id,
                'title': source_post.title,
                'slug': source_post.slug,
            },
            'count': len(suggestions),
            'suggestions': suggestions,
            'filters': {
                'limit': limit,
                'published_only': True,
                'exclude_source_post': True,
            }
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@csrf_exempt
def post_idea_generate_api(request):
    """API endpoint to generate post ideas (for n8n or other automation)"""
    if request.method not in ['POST', 'GET']:
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate API token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    # Get parameters (support both POST and GET)
    if request.method == 'POST':
        try:
            import json as json_lib
            if request.content_type == 'application/json':
                data = json_lib.loads(request.body)
                num_ideas = data.get('num_ideas')
                provider = data.get('provider')
                model = data.get('model')
                similarity_threshold = data.get('similarity_threshold')
            else:
                num_ideas = request.POST.get('num_ideas')
                provider = request.POST.get('provider')
                model = request.POST.get('model')
                similarity_threshold = request.POST.get('similarity_threshold')
        except:
            num_ideas = request.POST.get('num_ideas')
            provider = request.POST.get('provider')
            model = request.POST.get('model')
            similarity_threshold = request.POST.get('similarity_threshold')
    else:
        num_ideas = request.GET.get('num_ideas')
        provider = request.GET.get('provider')
        model = request.GET.get('model')
        similarity_threshold = request.GET.get('similarity_threshold')
    
    # Validate required parameters
    if not num_ideas:
        return JsonResponse({'error': 'num_ideas parameter is required'}, status=400)
    
    if not provider:
        return JsonResponse({'error': 'provider parameter is required (ollama, openai, or gemini)'}, status=400)
    
    if not model:
        return JsonResponse({'error': 'model parameter is required'}, status=400)
    
    # Validate and convert num_ideas
    try:
        num_ideas = int(num_ideas)
        if num_ideas < 1 or num_ideas > 50:
            return JsonResponse({'error': 'num_ideas must be between 1 and 50'}, status=400)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'num_ideas must be a valid integer'}, status=400)
    
    # Validate provider
    if provider not in ['ollama', 'openai', 'gemini']:
        return JsonResponse({'error': 'provider must be one of: ollama, openai, gemini'}, status=400)
    
    # Validate and convert similarity_threshold (optional, default 0.70)
    if similarity_threshold is not None:
        try:
            similarity_threshold = float(similarity_threshold)
            if not 0.0 <= similarity_threshold <= 1.0:
                return JsonResponse({'error': 'similarity_threshold must be between 0.0 and 1.0'}, status=400)
        except (ValueError, TypeError):
            return JsonResponse({'error': 'similarity_threshold must be a valid number between 0.0 and 1.0'}, status=400)
    else:
        similarity_threshold = 0.70  # Default value
    
    # Generate ideas
    success, created_count, created_ideas, error_message, skipped_similar = _generate_post_ideas(
        num_ideas=num_ideas,
        provider=provider,
        model=model,
        selected_tags=None,
        selected_contents=None,
        user=None,  # API calls are system-generated
        similarity_threshold=similarity_threshold
    )
    
    if success:
        response_data = {
            'success': True,
            'created_count': created_count,
            'num_requested': num_ideas,
            'provider': provider,
            'model': model,
            'similarity_threshold': similarity_threshold,
            'ideas': created_ideas
        }
        if skipped_similar > 0:
            response_data['skipped_similar'] = skipped_similar
            response_data['message'] = f'Generated {created_count} idea(s), {skipped_similar} similar idea(s) were skipped'
        return JsonResponse(response_data)
    else:
        return JsonResponse({
            'success': False,
            'error': error_message
        }, status=500)



def agent_models_api(request):
    """API endpoint to fetch available models for a given provider"""
    provider = request.GET.get('provider', 'ollama').strip().lower()
    
    if provider == 'ollama':
        try:
            import requests
        except ImportError:
            return JsonResponse({'error': 'requests library required'}, status=500)
        
        ollama_url = getattr(settings, 'OLLAMA_URL', 'http://localhost:11434')
        url = f"{ollama_url}/api/tags"
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Extract model names
            models = []
            if 'models' in data:
                for model_info in data['models']:
                    model_name = model_info.get('name', '')
                    if model_name:
                        models.append(model_name)
            
            # Sort models (prefer Chinese models first, then smaller models)
            chinese_models = [m for m in models if any(keyword in m.lower() for keyword in ['qwen', 'chinese', 'zh', 'cn'])]
            other_models = [m for m in models if m not in chinese_models]
            
            # Within each group, prefer smaller models (3b, 4b) first
            def sort_key(model_name):
                name_lower = model_name.lower()
                # Smaller models first
                if ':3b' in name_lower or '3b' in name_lower:
                    return (0, name_lower)
                elif ':4b' in name_lower or '4b' in name_lower:
                    return (1, name_lower)
                elif ':7b' in name_lower or '7b' in name_lower:
                    return (2, name_lower)
                elif ':8b' in name_lower or '8b' in name_lower:
                    return (3, name_lower)
                else:
                    return (4, name_lower)
            
            chinese_models.sort(key=sort_key)
            other_models.sort(key=sort_key)
            sorted_models = chinese_models + other_models
            
            return JsonResponse({'models': sorted_models})
        except requests.exceptions.ConnectionError:
            return JsonResponse({'error': 'Could not connect to Ollama. Make sure Ollama is running.'}, status=503)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    
    elif provider in ('openai', 'gemini'):
        from ..llm_models import list_models_for_provider

        models, error = list_models_for_provider(provider)
        if error:
            status = 400 if 'not set' in error else 502
            return JsonResponse({'error': error}, status=status)
        return JsonResponse({'models': models})
    
    else:
        return JsonResponse({'error': 'Invalid provider'}, status=400)



@csrf_exempt
def youtube_channels_api(request):
    """API endpoint to get all YouTube channel sources (token-based authentication)"""
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    try:
        # Get all YouTube channel sources
        youtube_sources = Source.objects.filter(
            source_type='youtube',
            channel_id__isnull=False
        ).exclude(channel_id='').order_by('name')
        
        # Build response data
        channels = []
        for source in youtube_sources:
            channels.append({
                'id': source.id,
                'name': source.name,
                'channel_id': source.channel_id,
                'include_shorts': source.include_shorts
            })
        
        return JsonResponse({
            'success': True,
            'channels': channels,
            'count': len(channels)
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)



@csrf_exempt
def blog_sources_api(request):
    """API endpoint to get all blog sources with sitemap links (token-based authentication)"""
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    try:
        # Get all blog sources that have a sitemap link
        blog_sources = Source.objects.filter(
            source_type='blog',
            sitemap__isnull=False
        ).exclude(sitemap='').order_by('name')
        
        # Build response data
        sources = []
        for source in blog_sources:
            sources.append({
                'id': source.id,
                'name': source.name,
                'sitemap': source.sitemap,
                'filter_china': source.filter_china
            })
        
        return JsonResponse({
            'success': True,
            'sources': sources,
            'count': len(sources)
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)



@csrf_exempt
def create_video_content_api(request):
    """API endpoint to create video content (token-based authentication)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    try:
        data = json.loads(request.body)
        
        # Required fields
        source_id = data.get('source_id')
        external_id = data.get('external_id')  # Video ID
        title = data.get('title')
        link = data.get('link')  # YouTube URL
        
        # Optional fields
        date = data.get('date')  # YYYY-MM-DD format
        auto_process = data.get('auto_process', True)  # Whether to automatically process (extract transcript, tag, embed)
        description = data.get('description', '')  # Video description (for filtering)
        tags = data.get('tags', [])  # Video tags (for filtering) - can be list or comma-separated string
        
        # Validate required fields
        if not source_id:
            return JsonResponse({
                'success': False,
                'error': 'source_id is required'
            }, status=400)
        
        if not external_id:
            return JsonResponse({
                'success': False,
                'error': 'external_id (video ID) is required'
            }, status=400)
        
        if not title:
            return JsonResponse({
                'success': False,
                'error': 'title is required'
            }, status=400)
        
        # Get source
        try:
            source = Source.objects.get(pk=source_id, source_type='youtube')
        except Source.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': f'Source with id {source_id} not found or is not a YouTube source'
            }, status=404)
        
        # Check if content already exists
        if Content.objects.filter(source=source, external_id=external_id).exists():
            return JsonResponse({
                'success': False,
                'error': f'Video with external_id "{external_id}" already exists for this source'
            }, status=409)
        
        # Apply China filter if enabled for this source
        if source.filter_videos:
            from ..youtube_service import is_video_relevant_to_china_with_details
            
            # Normalize tags - handle both list and comma-separated string
            if isinstance(tags, str):
                tags_list = [tag.strip() for tag in tags.split(',') if tag.strip()]
            elif isinstance(tags, list):
                tags_list = tags
            else:
                tags_list = []
            
            # Check if video is relevant to China
            print(f"\n{'='*60}", flush=True)
            print(f"Filtering video via API: {title}", flush=True)
            print(f"Video ID: {external_id}", flush=True)
            print(f"Source: {source.name} (Filter enabled: {source.filter_videos})", flush=True)
            print(f"Title: {title}", flush=True)
            print(f"Description: {description[:100] if description else '(none)'}...", flush=True)
            print(f"Tags: {tags_list if tags_list else '(none)'}", flush=True)
            print(f"{'='*60}\n", flush=True)
            
            is_relevant, matched_keywords, _ = is_video_relevant_to_china_with_details(
                title=title,
                description=description or '',
                tags=tags_list,
                video_id=external_id
            )
            
            if not is_relevant:
                # Extract reason from matched_keywords (may contain Ollama reasoning or be empty)
                reason_text = matched_keywords[0] if matched_keywords and len(matched_keywords) > 0 else 'Not China-related'
                
                # Log the filtering result
                log_activity(
                    'content_created',
                    f'Video "{title}" (ID: {external_id}) was filtered out - not China-related',
                    user=None,  # API request, no user
                    source=source,
                    metadata={
                        'external_id': external_id,
                        'filtered': True,
                        'reason': reason_text,
                        'title': title,
                        'description': description[:200] if description else '',
                        'tags': tags_list,
                        'matched_keywords': matched_keywords
                    }
                )
                
                print(f"  [FILTER] ✗ Video filtered out: Not China-related", flush=True)
                print(f"  [FILTER] Title: {title}", flush=True)
                if matched_keywords and len(matched_keywords) > 0:
                    # If Ollama was used, matched_keywords contains reasoning
                    if 'transcript' in matched_keywords[0].lower() or 'ollama' in matched_keywords[0].lower() or len(matched_keywords[0]) > 50:
                        print(f"  [FILTER] Reason: {matched_keywords[0][:200]}...", flush=True)
                    else:
                        print(f"  [FILTER] Matched keywords: {', '.join(matched_keywords)}", flush=True)
                else:
                    print(f"  [FILTER] No China-related keywords found in title, description, or tags", flush=True)
                    print(f"  [FILTER] Matched keywords: (none)", flush=True)
                    print(f"{'='*60}\n", flush=True)
                
                return JsonResponse({
                    'success': False,
                    'error': f'Video was filtered out - not China-related',
                    'filtered': True,
                    'reason': 'Video does not appear to be relevant to China based on title, description, and tags',
                    'matched_keywords': []
                }, status=200)  # 200 because it's not an error, just filtered
            
            # Log successful filter pass
            print(f"  [FILTER] ✓ Video passed filter: China-related", flush=True)
            print(f"  [FILTER] Title: {title}", flush=True)
            print(f"  [FILTER] Matched keywords: {', '.join(matched_keywords)}", flush=True)
            print(f"  [FILTER] Total matches: {len(matched_keywords)}", flush=True)
            print(f"{'='*60}\n", flush=True)
            
            log_activity(
                'content_created',
                f'Video "{title}" (ID: {external_id}) passed China filter',
                user=None,  # API request, no user
                source=source,
                metadata={
                    'external_id': external_id,
                    'filtered': False,
                    'filter_passed': True,
                    'title': title,
                    'matched_keywords': matched_keywords,
                    'match_count': len(matched_keywords)
                }
            )
        
        # Parse date if provided
        parsed_date = None
        if date:
            try:
                from datetime import datetime
                # Try ISO 8601 format first (with time and timezone)
                try:
                    dt = datetime.fromisoformat(date.replace('Z', '+00:00'))
                    parsed_date = dt.date()
                except ValueError:
                    # Fallback to YYYY-MM-DD format
                    parsed_date = datetime.strptime(date, '%Y-%m-%d').date()
            except ValueError:
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid date format. Use YYYY-MM-DD or ISO 8601 format (e.g., 2025-11-07T15:15:00+00:00)'
                }, status=400)
        
        # Create content
        # Note: date might be required, so use today's date if not provided
        if not parsed_date:
            from datetime import date
            parsed_date = date.today()
        
        content = Content.objects.create(
            source=source,
            external_id=external_id,
            title=title,
            link=link or f"https://www.youtube.com/watch?v={external_id}",
            content_type='video',
            date=parsed_date,
            content='',  # Empty - will be filled during processing
            processed=False,
        )
        
        # Update last_collected timestamp on source
        from django.utils import timezone
        source.last_collected = timezone.now()
        source.save(update_fields=['last_collected'])
        
        # Log content creation
        log_activity(
            'content_created',
            f'Content "{content.title}" (Video) was created via API',
            user=None,  # API request, no user
            content=content,
            source=content.source
        )
        
        # Process content if requested
        processing_results = {}
        if auto_process:
            try:
                print(f"\n{'='*60}", flush=True)
                print(f"Processing video content via API: {content.title}", flush=True)
                print(f"Video ID: {content.external_id}", flush=True)
                print(f"{'='*60}\n", flush=True)
                
                processing_service = ContentProcessingService(use_proxy=True)
                processing_results = processing_service.process_content(
                    content,
                    extract=True,
                    translate=True,
                    tag=True,
                    embed=True
                )
                content.refresh_from_db()
                
                print(f"\n{'='*60}", flush=True)
                print(f"Processing results: {processing_results}", flush=True)
                print(f"Has content: {content.has_content}, Processed: {content.processed}", flush=True)
                print(f"{'='*60}\n", flush=True)
            except Exception as e:
                # Log error but don't fail the request
                import traceback
                error_trace = traceback.format_exc()
                print(f"Error processing content {content.id}: {str(e)}", flush=True)
                print(error_trace, flush=True)
                processing_results = {'error': str(e), 'traceback': error_trace}
        
        return JsonResponse({
            'success': True,
            'content': {
                'id': content.id,
                'title': content.title,
                'external_id': content.external_id,
                'link': content.link,
                'date': str(content.date) if content.date else None,
                'has_content': content.has_content,
                'processed': content.processed,
            },
            'processing': processing_results
        }, status=201)
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON in request body'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)



@csrf_exempt
def create_blog_post_api(request):
    """API endpoint to create blog post content (token-based authentication)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    try:
        data = json.loads(request.body)
        
        # Required fields
        source_id = data.get('source_id')
        title = data.get('title')
        link = data.get('link')
        
        # Optional fields
        date = data.get('date')  # YYYY-MM-DD format or ISO 8601
        auto_process = data.get('auto_process', True)  # Whether to automatically process (extract content, translate, tag, embed)
        
        # Validate required fields
        if not source_id:
            return JsonResponse({
                'success': False,
                'error': 'source_id is required'
            }, status=400)
        
        if not title:
            return JsonResponse({
                'success': False,
                'error': 'title is required'
            }, status=400)
        
        if not link:
            return JsonResponse({
                'success': False,
                'error': 'link is required'
            }, status=400)
        
        # Get source
        try:
            source = Source.objects.get(pk=source_id, source_type='blog')
        except Source.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': f'Source with id {source_id} not found or is not a blog source'
            }, status=404)
        
        # Use link as external_id for blog posts (URL is unique identifier)
        external_id = link
        
        # Check if content already exists
        if Content.objects.filter(source=source, external_id=external_id).exists():
            return JsonResponse({
                'success': False,
                'error': f'Blog post with link "{link}" already exists for this source'
            }, status=409)
        
        # Parse date if provided
        parsed_date = None
        if date:
            try:
                from datetime import datetime
                # Try ISO 8601 format first (with time and timezone)
                try:
                    dt = datetime.fromisoformat(date.replace('Z', '+00:00'))
                    parsed_date = dt.date()
                except ValueError:
                    # Fallback to YYYY-MM-DD format
                    parsed_date = datetime.strptime(date, '%Y-%m-%d').date()
            except ValueError:
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid date format. Use YYYY-MM-DD or ISO 8601 format (e.g., 2025-11-07T15:15:00+00:00)'
                }, status=400)
        
        # Use today's date if not provided
        if not parsed_date:
            from datetime import date
            parsed_date = date.today()
        
        # Create content
        content = Content.objects.create(
            source=source,
            external_id=external_id,
            title=title,
            link=link,
            content_type='blog_post',
            date=parsed_date,
            content='',  # Empty - will be filled during processing
            processed=False,
        )
        
        # Update last_collected timestamp on source
        from django.utils import timezone
        source.last_collected = timezone.now()
        source.save(update_fields=['last_collected'])
        
        # Log content creation
        log_activity(
            'content_created',
            f'Content "{content.title}" (Blog Post) was created via API',
            user=None,  # API request, no user
            content=content,
            source=content.source
        )
        
        # Process content if requested
        processing_results = {}
        if auto_process:
            try:
                print(f"\n{'='*60}", flush=True)
                print(f"Processing blog post content via API: {content.title}", flush=True)
                print(f"Link: {content.link}", flush=True)
                print(f"{'='*60}\n", flush=True)
                
                processing_service = ContentProcessingService(use_proxy=True)
                processing_results = processing_service.process_content(
                    content,
                    extract=True,
                    translate=True,
                    tag=True,
                    embed=True
                )
                
                print(f"\n{'='*60}", flush=True)
                print(f"Processing completed for: {content.title}", flush=True)
                print(f"Extracted: {processing_results.get('extracted', False)}", flush=True)
                print(f"Translated: {processing_results.get('translated', False)}", flush=True)
                print(f"Tagged: {processing_results.get('tagged', False)}", flush=True)
                print(f"Embedded: {processing_results.get('embedded', False)}", flush=True)
                print(f"{'='*60}\n", flush=True)
                
            except Exception as e:
                print(f"  [API] ⚠️  Error during processing: {str(e)}", flush=True)
                import traceback
                print(f"  [API] Traceback: {traceback.format_exc()}", flush=True)
                processing_results = {
                    'error': str(e),
                    'extracted': False,
                    'translated': False,
                    'tagged': False,
                    'embedded': False
                }
        
        return JsonResponse({
            'success': True,
            'content_id': content.id,
            'message': 'Blog post created successfully',
            'processing': processing_results if auto_process else None
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON in request body'
        }, status=400)
    except Exception as e:
        import traceback
        print(f"  [API] ❌ Error creating blog post: {str(e)}", flush=True)
        print(f"  [API] Traceback: {traceback.format_exc()}", flush=True)
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)



@csrf_exempt
def post_ideas_api(request):
    """API endpoint to list all post ideas (token-based authentication)
    
    Query parameters:
    - exclude_with_posts: If set to 'true', filters out post ideas that already have blog posts associated with them.
    - search: Search query to filter ideas by title, description, or primary keyword (case-insensitive partial match).
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    try:
        # Get all post ideas, ordered by creation date (newest first)
        post_ideas = PostIdea.objects.all().order_by('-created_at')
        
        # Filter out ideas with blog posts if requested
        exclude_with_posts = request.GET.get('exclude_with_posts', '').strip().lower()
        if exclude_with_posts == 'true':
            # Exclude post ideas that have at least one blog post
            post_ideas = post_ideas.annotate(
                blog_posts_count=Count('blog_posts')
            ).filter(blog_posts_count=0)
        
        # Search filter - search in title, description, and primary_keyword
        search_query = request.GET.get('search', '').strip()
        if search_query:
            post_ideas = post_ideas.filter(
                Q(title__icontains=search_query) |
                Q(description__icontains=search_query) |
                Q(primary_keyword__icontains=search_query)
            )
        
        # Build response data - only return essential fields for lighter payload
        ideas = []
        for idea in post_ideas:
            ideas.append({
                'id': idea.id,
                'title': idea.title,
                'description': idea.description,
                'keyword': idea.primary_keyword,  # Using 'keyword' instead of 'primary_keyword' for consistency
            })
        
        return JsonResponse({
            'success': True,
            'post_ideas': ideas,
            'count': len(ideas),
            'filter_applied': {
                'exclude_with_posts': exclude_with_posts == 'true' if exclude_with_posts else False,
                'search': search_query if search_query else None
            }
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)



@csrf_exempt
def blog_posts_api(request):
    """API endpoint to list blog posts (token-based authentication)
    
    Query parameters:
    - published: Filter by published status (true/false). If not provided, returns all posts.
    - limit: Maximum number of posts to return (integer). If not provided, returns all posts.
    - search: Search query to filter posts by title, meta_title, meta_description, or tag names (case-insensitive partial match).
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    try:
        # Get all blog posts, ordered by creation date (newest first)
        blog_posts = BlogPost.objects.all().order_by('-created_at')
        
        # Filter by published status if provided
        published_param = request.GET.get('published', '').strip().lower()
        if published_param == 'true':
            blog_posts = blog_posts.filter(published=True)
        elif published_param == 'false':
            blog_posts = blog_posts.filter(published=False)
        # If not provided or empty, return all posts
        
        # Search filter - search in title, meta_title, meta_description, and tag names
        search_query = request.GET.get('search', '').strip()
        if search_query:
            blog_posts = blog_posts.filter(
                Q(title__icontains=search_query) |
                Q(meta_title__icontains=search_query) |
                Q(meta_description__icontains=search_query) |
                Q(tags__name__icontains=search_query)
            ).distinct()  # Use distinct() to avoid duplicates when matching multiple tags
        
        # Apply limit if provided
        limit_param = request.GET.get('limit', '').strip()
        limit = None
        if limit_param:
            try:
                limit = int(limit_param)
                if limit > 0:
                    blog_posts = blog_posts[:limit]
            except ValueError:
                # Invalid limit value, ignore it
                pass
        
        # Build response data - only title and created_at
        posts = []
        for post in blog_posts:
            posts.append({
                'title': post.title,
                'created_at': post.created_at.isoformat() if post.created_at else None,
            })
        
        return JsonResponse({
            'success': True,
            'blog_posts': posts,
            'count': len(posts),
            'filter_applied': {
                'published': published_param if published_param else None,
                'limit': limit if limit else None,
                'search': search_query if search_query else None
            }
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)



@csrf_exempt
def blog_post_update_status_api(request, pk):
    """API endpoint to update blog post published status (token-based authentication)
    
    Methods: PATCH or POST
    Body (JSON or form-data):
    - published: boolean (true/false) - whether to publish the post
    - online_url: string (optional) - URL of the published blog post on the live website
    """
    if request.method not in ['PATCH', 'POST']:
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    try:
        # Get the blog post
        blog_post = get_object_or_404(BlogPost, pk=pk)
        
        # Get published status and online_url from request
        if request.content_type == 'application/json':
            import json
            data = json.loads(request.body)
            published = data.get('published', None)
            online_url = data.get('online_url', None)
        else:
            # Form data
            published = request.POST.get('published', None)
            online_url = request.POST.get('online_url', None)
        
        if published is None:
            return JsonResponse({
                'success': False,
                'error': 'Missing required parameter: published'
            }, status=400)
        
        # Convert to boolean
        if isinstance(published, str):
            published = published.lower() in ('true', '1', 'yes', 'on')
        elif not isinstance(published, bool):
            return JsonResponse({
                'success': False,
                'error': 'Invalid published value. Must be boolean or string representation of boolean.'
            }, status=400)
        
        # Validate online_url if provided
        if online_url is not None:
            online_url = online_url.strip()
            if online_url == '':
                online_url = None
            elif not online_url.startswith(('http://', 'https://')):
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid online_url. Must be a valid URL starting with http:// or https://'
                }, status=400)
        
        # Update published status and online_url
        update_fields = ['published']
        blog_post.published = published
        
        if online_url is not None:
            blog_post.online_url = online_url
            update_fields.append('online_url')
        
        blog_post.save(update_fields=update_fields)
        
        # Log activity
        log_message = f'Blog post "{blog_post.title}" status updated to {"published" if published else "draft"}'
        if online_url and published:
            log_message += f' with URL: {online_url}'
        
        log_activity(
            'blog_post_status_updated',
            log_message,
            user=None,  # API call, no user
            metadata={
                'blog_post_id': blog_post.id,
                'published': published,
                'online_url': online_url if online_url else None
            }
        )
        
        return JsonResponse({
            'success': True,
            'blog_post': {
                'id': blog_post.id,
                'title': blog_post.title,
                'slug': blog_post.slug,
                'published': blog_post.published,
                'online_url': blog_post.online_url or None,
            }
        })
    except BlogPost.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Blog post not found'
        }, status=404)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)



@csrf_exempt
def blog_posts_export_wordpress_api(request):
    """API endpoint to export blog posts in WordPress ACF-compatible format for n8n integration
    
    Query parameters:
    - published: Filter by published status (true/false). If not provided, returns all posts.
    - limit: Maximum number of posts to return (integer). If not provided, returns all posts.
    - post_id: Return a single post by ID (overrides other filters)
    - oldest_unpublished: If true, returns only the oldest unpublished post (overrides other filters except post_id)
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    try:
        # Check if single post requested
        post_id_param = request.GET.get('post_id', '').strip()
        if post_id_param:
            try:
                post_id = int(post_id_param)
                blog_posts = BlogPost.objects.filter(id=post_id).prefetch_related('images', 'tags')
            except ValueError:
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid post_id parameter'
                }, status=400)
        else:
            # Check if oldest unpublished is requested
            oldest_unpublished = request.GET.get('oldest_unpublished', '').strip().lower() == 'true'
            
            if oldest_unpublished:
                # Get only the oldest unpublished post
                blog_posts = BlogPost.objects.filter(published=False).prefetch_related('images', 'tags').order_by('created_at')[:1]
            else:
                # Get all blog posts, ordered by creation date (newest first)
                blog_posts = BlogPost.objects.all().prefetch_related('images', 'tags').order_by('-created_at')
                
                # Filter by published status if provided
                published_param = request.GET.get('published', '').strip().lower()
                if published_param == 'true':
                    blog_posts = blog_posts.filter(published=True)
                elif published_param == 'false':
                    blog_posts = blog_posts.filter(published=False)
                
                # Apply limit if provided
                limit_param = request.GET.get('limit', '').strip()
                if limit_param:
                    try:
                        limit = int(limit_param)
                        if limit > 0:
                            blog_posts = blog_posts[:limit]
                    except ValueError:
                        pass
        
        # Build WordPress-compatible response with ACF fields
        posts = []
        for post in blog_posts:
            # Parse content sections
            sections = _parse_blog_content_sections(post.content)
            
            # Get featured image URL if exists
            featured_image_url = None
            if post.featured_image:
                featured_image_url = request.build_absolute_uri(post.featured_image.url)
            
            # Get all images for this post (both content images and featured image)
            images_data = []
            for img in post.images.all():
                if img.image_file:  # Only include images that have been uploaded
                    # For featured images, prioritize post.featured_image_description over img.alt_text
                    if img.is_featured:
                        alt_text = post.featured_image_description or img.alt_text or ''
                    else:
                        alt_text = img.alt_text or ''
                    
                    images_data.append({
                        'id': img.id,
                        'filename': img.filename,
                        'url': request.build_absolute_uri(img.image_file.url),
                        'alt_text': alt_text,
                        'is_featured': img.is_featured,
                    })
            
            # Also include featured image from BlogPost model if it exists and not already in images_data
            if post.featured_image and not any(img['is_featured'] for img in images_data):
                # Try to find the BlogPostImage record for the featured image
                featured_img_record = post.images.filter(is_featured=True).first()
                images_data.append({
                    'id': featured_img_record.id if featured_img_record else None,
                    'filename': 'featured_image',
                    'url': featured_image_url,
                    'alt_text': post.featured_image_description or '',
                    'is_featured': True,
                })
            
            # Replace <img> tags in main_content with [IMG-{id}] placeholders
            main_content = sections['main_content']
            if images_data:
                # Create a mapping from filename to image ID
                filename_to_id = {img['filename']: img['id'] for img in images_data if img['id'] is not None}
                
                # Pattern to match <img> tags with data-filename attribute
                import re
                img_pattern = r'<img[^>]*data-filename=["\']([^"\']+)["\'][^>]*>'
                
                def replace_img_with_placeholder(match):
                    filename = match.group(1)
                    img_id = filename_to_id.get(filename)
                    if img_id:
                        return f'[IMG-{img_id}]'
                    # If no matching image found, return the original tag
                    return match.group(0)
                
                main_content = re.sub(img_pattern, replace_img_with_placeholder, main_content, flags=re.IGNORECASE)
            
            # Get tag names
            tag_names = [tag.name for tag in post.tags.all()]
            
            # Format FAQ fields
            faq_fields = _format_faq_acf_fields(post)
            
            # Build ACF object
            acf = {
                'intro': sections['intro'],
                'intro_source': _format_acf_field(sections['intro'], 'Intro', 'wysiwyg'),
                'main_content': main_content,
                'main_content_source': _format_acf_field(main_content, 'Main content', 'wysiwyg'),
                'conclusion': sections['conclusion'],
                'conclusion_source': _format_acf_field(sections['conclusion'], 'Conclusion', 'wysiwyg'),
                'summary_title': sections['summary_title'],
                'summary_title_source': _format_acf_field(sections['summary_title'], 'Title', 'text'),
                'summary_content': sections['summary_content'],
                'summary_content_source': _format_acf_field(sections['summary_content'], 'Content', 'wysiwyg'),
                **faq_fields  # Spread FAQ fields into ACF object
            }
            
            posts.append({
                'id': post.id,
                'title': post.title,
                'slug': post.slug,
                'content': post.content,  # Full content for reference
                'excerpt': post.meta_description or '',
                'status': 'publish' if post.published else 'draft',
                'date': post.created_at.isoformat() if post.created_at else None,
                'meta': {
                    'meta_title': post.meta_title or '',
                    'meta_description': post.meta_description or '',
                    'featured_image_alt': post.featured_image_description or '',
                },
                'acf': acf,
                'tags': tag_names,
                'featured_image_url': featured_image_url,
                'images': images_data,  # Include all images with their URLs and metadata
            })
        
        return JsonResponse({
            'success': True,
            'blog_posts': posts,
            'count': len(posts)
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)



@csrf_exempt
def check_idea_similarity_api(request):
    """API endpoint to check if a post idea is too similar to existing ideas (token-based authentication)
    
    Request Body:
    - title: Title of the idea to check (required)
    - similarity_threshold: Similarity threshold (0.0-1.0, default: 0.7)
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    try:
        # Parse request body
        if request.content_type == 'application/json':
            data = json.loads(request.body)
        else:
            data = request.POST
        
        title = data.get('title', '').strip()
        similarity_threshold = data.get('similarity_threshold', 0.7)
        
        # Validate required fields
        if not title:
            return JsonResponse({
                'success': False,
                'error': 'title is required'
            }, status=400)
        
        # Validate and convert similarity_threshold
        try:
            similarity_threshold = float(similarity_threshold)
            if not 0.0 <= similarity_threshold <= 1.0:
                return JsonResponse({
                    'success': False,
                    'error': 'similarity_threshold must be between 0.0 and 1.0'
                }, status=400)
        except (ValueError, TypeError):
            return JsonResponse({
                'success': False,
                'error': 'similarity_threshold must be a valid number between 0.0 and 1.0'
            }, status=400)
        
        # Initialize embedding service
        embedding_service = None
        try:
            embedding_service = EmbeddingService()
        except (ValueError, ImportError) as e:
            return JsonResponse({
                'success': False,
                'error': f'Embedding service not available: {str(e)}'
            }, status=500)
        
        # Get existing ideas with embeddings
        existing_ideas = list(
            PostIdea.objects.filter(title_embedding__isnull=False)
        )
        
        if not existing_ideas:
            # No existing ideas, so it's not similar
            return JsonResponse({
                'success': True,
                'is_similar': False,
                'similarity_score': 0.0,
                'most_similar_idea': None,
                'threshold': similarity_threshold,
                'message': 'No existing ideas to compare against'
            })
        
        # Generate embedding for the new title
        new_embedding = embedding_service.generate_embedding(title)
        if not new_embedding:
            return JsonResponse({
                'success': False,
                'error': 'Failed to generate embedding for the title'
            }, status=500)
        
        # Calculate max_distance from similarity threshold
        max_distance = 1.0 - similarity_threshold
        
        # Find most similar ideas using vector search
        similar_ideas = PostIdea.objects.filter(
            title_embedding__isnull=False,
            id__in=[idea.id for idea in existing_ideas]
        ).annotate(
            distance=CosineDistance('title_embedding', new_embedding)
        ).order_by('distance')[:1]  # Get the most similar one
        
        if similar_ideas.exists():
            most_similar = similar_ideas[0]
            distance = float(most_similar.distance)
            similarity_score = max(0.0, min(1.0, 1.0 - distance))
            is_similar = similarity_score >= similarity_threshold
            
            return JsonResponse({
                'success': True,
                'is_similar': is_similar,
                'similarity_score': round(similarity_score, 4),
                'most_similar_idea': {
                    'id': most_similar.id,
                    'title': most_similar.title,
                    'similarity': round(similarity_score, 4)
                },
                'threshold': similarity_threshold
            })
        else:
            # No similar ideas found
            return JsonResponse({
                'success': True,
                'is_similar': False,
                'similarity_score': 0.0,
                'most_similar_idea': None,
                'threshold': similarity_threshold
            })
            
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON in request body'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)



@csrf_exempt
def create_post_idea_api(request):
    """API endpoint to create a post idea (token-based authentication)
    
    Request Body:
    - title: Title of the idea (required)
    - description: Description of the idea (optional)
    - primary_keyword: Primary keyword for the idea (optional)
    - similarity_threshold: Similarity threshold (0.0-1.0, default: 0.7)
    
    Returns error if idea is too similar to existing ideas.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    try:
        # Parse request body
        if request.content_type == 'application/json':
            data = json.loads(request.body)
        else:
            data = request.POST
        
        title = data.get('title', '').strip()
        description = data.get('description', '').strip() or ''
        primary_keyword = data.get('primary_keyword', '').strip() or None
        similarity_threshold = data.get('similarity_threshold', 0.7)
        
        # Validate required fields
        if not title:
            return JsonResponse({
                'success': False,
                'error': 'title is required'
            }, status=400)
        
        # Validate and convert similarity_threshold
        try:
            similarity_threshold = float(similarity_threshold)
            if not 0.0 <= similarity_threshold <= 1.0:
                return JsonResponse({
                    'success': False,
                    'error': 'similarity_threshold must be between 0.0 and 1.0'
                }, status=400)
        except (ValueError, TypeError):
            similarity_threshold = 0.7  # Default value
        
        # Initialize embedding service
        embedding_service = None
        try:
            embedding_service = EmbeddingService()
        except (ValueError, ImportError) as e:
            # If embedding service not available, skip similarity check but log warning
            print(f"Warning: Embedding service not available: {str(e)}. Skipping similarity check.")
        
        # Check similarity if embedding service is available
        new_embedding = None
        if embedding_service:
            # Generate embedding for the new title (needed for both similarity check and storage)
            new_embedding = embedding_service.generate_embedding(title)
            if not new_embedding:
                # Failed to generate embedding, but continue (will create without embedding)
                print(f"Warning: Could not generate embedding for '{title}'")
            else:
                # Get existing ideas with embeddings
                existing_ideas = list(
                    PostIdea.objects.filter(title_embedding__isnull=False)
                )
                
                if existing_ideas:
                    # Calculate max_distance from similarity threshold
                    max_distance = 1.0 - similarity_threshold
                    
                    # Find most similar ideas using vector search
                    similar_ideas = PostIdea.objects.filter(
                        title_embedding__isnull=False,
                        id__in=[idea.id for idea in existing_ideas]
                    ).annotate(
                        distance=CosineDistance('title_embedding', new_embedding)
                    ).order_by('distance')[:1]  # Get the most similar one
                    
                    if similar_ideas.exists():
                        most_similar = similar_ideas[0]
                        distance = float(most_similar.distance)
                        similarity_score = max(0.0, min(1.0, 1.0 - distance))
                        
                        if similarity_score >= similarity_threshold:
                            # Idea is too similar, return rejection (200 status so n8n doesn't treat it as fatal error)
                            return JsonResponse({
                                'success': False,
                                'rejected': True,
                                'reason': 'idea_too_similar',
                                'error': 'idea_too_similar',
                                'message': f'Idea is too similar to existing idea (similarity: {similarity_score:.4f}, threshold: {similarity_threshold})',
                                'similarity_score': round(similarity_score, 4),
                                'most_similar_idea': {
                                    'id': most_similar.id,
                                    'title': most_similar.title,
                                    'similarity': round(similarity_score, 4)
                                },
                                'threshold': similarity_threshold
                            }, status=200)  # 200 so n8n doesn't stop execution
        
        # Create the post idea
        post_idea = PostIdea.objects.create(
            title=title,
            description=description,
            primary_keyword=primary_keyword,
            title_embedding=new_embedding
        )
        
        return JsonResponse({
            'success': True,
            'idea': {
                'id': post_idea.id,
                'title': post_idea.title,
                'description': post_idea.description,
                'primary_keyword': post_idea.primary_keyword,
                'created_at': post_idea.created_at.isoformat() if post_idea.created_at else None
            }
        })
            
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON in request body'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)



@csrf_exempt
def get_idea_context_api(request):
    """API endpoint to get context for idea generation (token-based authentication)
    
    Query parameters:
    - random_tags: Number of random tags to return (default: 5)
    - random_contents: Number of random content items to return (default: 5)
    - tag_ids: Optional comma-separated list of specific tag IDs to include
    - content_ids: Optional comma-separated list of specific content IDs to include
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    try:
        # Get query parameters
        random_tags_param = request.GET.get('random_tags', '5').strip()
        random_contents_param = request.GET.get('random_contents', '5').strip()
        tag_ids_param = request.GET.get('tag_ids', '').strip()
        content_ids_param = request.GET.get('content_ids', '').strip()
        
        # Parse and validate random_tags
        try:
            random_tags_count = int(random_tags_param)
            if random_tags_count < 0:
                random_tags_count = 5
        except ValueError:
            random_tags_count = 5
        
        # Parse and validate random_contents
        try:
            random_contents_count = int(random_contents_param)
            if random_contents_count < 0:
                random_contents_count = 5
        except ValueError:
            random_contents_count = 5
        
        # Parse tag_ids
        tag_ids = []
        if tag_ids_param:
            try:
                tag_ids = [int(tid.strip()) for tid in tag_ids_param.split(',') if tid.strip()]
            except ValueError:
                pass
        
        # Parse content_ids
        content_ids = []
        if content_ids_param:
            try:
                content_ids = [int(cid.strip()) for cid in content_ids_param.split(',') if cid.strip()]
            except ValueError:
                pass
        
        # Get tags
        tags = []
        if tag_ids:
            # Get specific tags
            specific_tags = Tag.objects.filter(id__in=tag_ids)
            tags.extend([{'id': tag.id, 'name': tag.name} for tag in specific_tags])
        
        # Get random tags if needed
        if random_tags_count > 0:
            all_tags = Tag.objects.exclude(id__in=tag_ids) if tag_ids else Tag.objects.all()
            random_tags = list(all_tags.order_by('?')[:random_tags_count])
            tags.extend([{'id': tag.id, 'name': tag.name} for tag in random_tags])
        
        # Get contents
        contents = []
        if content_ids:
            # Get specific contents
            specific_contents = Content.objects.filter(id__in=content_ids)
            for content in specific_contents:
                summary = content.content[:300] if content.content else ''
                contents.append({
                    'id': content.id,
                    'title': content.title,
                    'summary': summary
                })
        
        # Get random contents if needed
        if random_contents_count > 0:
            all_contents = Content.objects.exclude(id__in=content_ids) if content_ids else Content.objects.all()
            random_contents = list(all_contents.order_by('?')[:random_contents_count])
            for content in random_contents:
                summary = content.content[:300] if content.content else ''
                contents.append({
                    'id': content.id,
                    'title': content.title,
                    'summary': summary
                })
        
        # Get recent ideas sample
        recent_ideas = PostIdea.objects.all().order_by('-created_at')[:10]
        recent_ideas_sample = [{'id': idea.id, 'title': idea.title} for idea in recent_ideas]
        
        return JsonResponse({
            'success': True,
            'context': {
                'tags': tags,
                'contents': contents,
                'recent_ideas_sample': recent_ideas_sample
            }
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)



@csrf_exempt
def generate_blog_post_api(request):
    """API endpoint to generate a blog post from a post idea and automatically generate metadata
    
    This endpoint:
    1. Generates the blog post content from a post idea
    2. Automatically generates metadata (slug, meta title, meta description, tags, featured image alt text)
    
    Request body (JSON):
    - post_idea_id (required): ID of the post idea to generate from
    - provider (optional): AI provider for content generation ('ollama', 'openai', 'gemini'). Default: 'gemini'
    - model (optional): Model name for content generation. Default: provider default (e.g. gemini-2.5-pro)
    - use_rag (optional): Whether to use RAG context. Default: false
    - num_chunks (optional): Number of RAG chunks to use. Default: 5
    - metadata_provider (optional): AI provider for metadata generation. Default: same as provider
    - metadata_model (optional): Model name for metadata generation. Default: same as model
    - enable_internal_links (optional): Whether to auto-insert internal links. Default: true
    - internal_links_limit (optional): Max inserted links (1-10). Default: 5
    - internal_links_mode (optional): 'ai' or 'rule_based'. Default: 'ai'
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    import re
    import json  # Ensure json is available in function scope
    
    try:
        data = json.loads(request.body)
        
        # Required fields
        post_idea_id = data.get('post_idea_id')
        if not post_idea_id:
            return JsonResponse({
                'success': False,
                'error': 'post_idea_id is required'
            }, status=400)
        
        # Get post idea
        try:
            post_idea = PostIdea.objects.get(pk=post_idea_id)
        except PostIdea.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': f'Post idea with id {post_idea_id} not found'
            }, status=404)
        
        # Optional parameters for content generation
        from ..llm_models import get_default_model_for_provider

        provider = data.get('provider', 'gemini').strip().lower()
        model = (data.get('model') or '').strip()
        use_rag = data.get('use_rag', False)
        num_chunks = int(data.get('num_chunks', 5))
        
        # Optional parameters for metadata generation (default to same as content generation)
        metadata_provider = data.get('metadata_provider', provider).strip().lower()
        metadata_model = (data.get('metadata_model') or '').strip()
        enable_internal_links = data.get('enable_internal_links', True)
        internal_links_limit = int(data.get('internal_links_limit', 5))
        internal_links_limit = max(1, min(internal_links_limit, 10))
        internal_links_mode = (data.get('internal_links_mode', 'ai') or 'ai').strip().lower()
        if internal_links_mode not in ['ai', 'rule_based']:
            internal_links_mode = 'ai'
        
        # Validate providers
        if provider not in ['ollama', 'openai', 'gemini']:
            return JsonResponse({
                'success': False,
                'error': f'Invalid provider: {provider}. Must be one of: ollama, openai, gemini'
            }, status=400)
        
        if metadata_provider not in ['ollama', 'openai', 'gemini']:
            return JsonResponse({
                'success': False,
                'error': f'Invalid metadata_provider: {metadata_provider}. Must be one of: ollama, openai, gemini'
            }, status=400)
        
        if not model:
            model = get_default_model_for_provider(provider)
        if not metadata_model:
            metadata_model = model
        
        # Load prompt templates
        import os
        from django.conf import settings as django_settings
        
        from ..prompt_paths import resolve_metadata_prompt_path

        prompt_file_path = os.path.join(django_settings.BASE_DIR, 'prompt-post-generation.md')
        metadata_prompt_path = resolve_metadata_prompt_path()
        
        try:
            with open(prompt_file_path, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
        except FileNotFoundError:
            return JsonResponse({
                'success': False,
                'error': 'Prompt template file not found: prompt-post-generation.md'
            }, status=500)
        
        if not metadata_prompt_path:
            return JsonResponse({
                'success': False,
                'error': 'Metadata prompt template file not found: prompt-metadata-generator.md'
            }, status=500)
        with open(metadata_prompt_path, 'r', encoding='utf-8') as f:
            metadata_prompt_template = f.read()
        
        # Step 1: Generate blog post content
        rag_service = RAGService()
        
        # Get RAG context if enabled
        rag_context = ""
        if use_rag:
            try:
                chunks = rag_service.search_similar_chunks(
                    query_text=post_idea.title,
                    num_chunks=num_chunks
                )
                if chunks:
                    rag_context = rag_service._format_context(chunks)
            except Exception as e:
                # Continue without RAG context if it fails
                pass
        
        # Build the prompt
        primary_keyword = post_idea.primary_keyword or post_idea.title
        prompt = prompt_template.format(
            title=post_idea.title,
            description=post_idea.description or "No description provided.",
            primary_keyword=primary_keyword,
            current_year=datetime.now().year
        )
        
        # Add RAG context if available
        if rag_context:
            prompt = f"{prompt}\n\n### Additional Context from Content Library:\n{rag_context}"
        
        # Generate content
        if provider == 'ollama':
            generated_content = rag_service._call_ollama(prompt, model, max_tokens=8000)
        elif provider == 'openai':
            max_tokens = 16000 if any(keyword in model.lower() for keyword in ['gpt-4o', 'gpt-4-turbo', 'gpt-4']) else 8000
            generated_content = rag_service._call_openai(prompt, model, max_tokens=max_tokens)
        elif provider == 'gemini':
            max_tokens = 16000 if 'gemini-3-pro' in model.lower() else 8000
            generated_content = rag_service._call_gemini(prompt, model, max_tokens=max_tokens)
        
        # Post-process content
        import re
        blog_content = generated_content.strip()
        
        # Remove any text before the first <h1> tag
        h1_match = re.search(r'<h1>', blog_content, re.IGNORECASE)
        if h1_match:
            blog_content = blog_content[h1_match.start():]
        
        # Remove JSON-LD schema blocks
        json_pattern = r'```(?:json)?\s*\{.*?\}\s*```'
        blog_content = re.sub(json_pattern, '', blog_content, flags=re.DOTALL | re.IGNORECASE)
        
        json_block_pattern = r'\s*\{[^{}]*"@context"[^{}]*"@type"[^{}]*\}'
        blog_content = re.sub(json_block_pattern, '', blog_content, flags=re.DOTALL | re.IGNORECASE)
        
        # Remove common intro phrases
        intro_phrases = [
            r'^Here is a comprehensive.*?optimized for.*?\n+',
            r'^This is a comprehensive.*?guide.*?\n+',
            r'^Below is.*?\n+',
        ]
        for pattern in intro_phrases:
            blog_content = re.sub(pattern, '', blog_content, flags=re.IGNORECASE | re.MULTILINE)
        
        # Remove FAQ sections if they were generated (they should be in metadata, not content)
        # Remove FAQ sections with various heading formats
        faq_patterns = [
            r'<h[2-6][^>]*>.*?FAQ.*?</h[2-6]>.*?(?=<h[2-6]|</body>|$)',
            r'<h[2-6][^>]*>.*?Frequently Asked.*?</h[2-6]>.*?(?=<h[2-6]|</body>|$)',
            r'<h[2-6][^>]*>.*?People Also Ask.*?</h[2-6]>.*?(?=<h[2-6]|</body>|$)',
        ]
        for pattern in faq_patterns:
            blog_content = re.sub(pattern, '', blog_content, flags=re.IGNORECASE | re.DOTALL)
        
        blog_content = blog_content.strip()
        
        # Create the blog post
        blog_post = BlogPost.objects.create(
            title=post_idea.title,
            content=blog_content,
            post_idea=post_idea
        )
        
        # Parse and create image records from content
        _parse_and_create_blog_post_images(blog_post)
        
        # Step 2: Generate metadata
        # Strip HTML tags from content for metadata generation
        text_content = re.sub(r'<[^>]+>', ' ', blog_post.content)
        text_content = ' '.join(text_content.split())
        
        # Build metadata prompt with current year
        current_year = str(datetime.now().year)
        metadata_prompt = metadata_prompt_template.replace('{current_year}', current_year).replace('[PASTE YOUR GENERATED HTML CONTENT HERE]', text_content)
        
        # Generate metadata
        if metadata_provider == 'ollama':
            generated_metadata = rag_service._call_ollama(metadata_prompt, metadata_model, max_tokens=2000)
        elif metadata_provider == 'openai':
            generated_metadata = rag_service._call_openai(metadata_prompt, metadata_model, max_tokens=2000)
        elif metadata_provider == 'gemini':
            generated_metadata = rag_service._call_gemini(metadata_prompt, metadata_model, max_tokens=4000)
        
        # Parse metadata
        # Extract meta title - multiple patterns; truncate if over 60 chars; fallback to post title
        meta_title = None
        patterns = [
            r'\*\*Meta Title:\*\*\s*(.+?)(?=\n\*\*|\n\n|\n|$)',
            r'Meta [Tt]itle\s*:?\s*(.+?)(?=\n\*\*|\n\n|\n|$)',
            r'Title [Tt]ag\s*:?\s*(.+?)(?=\n\*\*|\n\n|\n|$)',
            r'Meta Title:\s*(.+?)(?:\n|$)',
            r'Title:\s*(.+?)(?=\n\*\*|\n\n|\n|$)',
        ]
        for pattern in patterns:
            match = re.search(pattern, generated_metadata, re.IGNORECASE)
            if match:
                meta_title = re.sub(r'\*\*|\*|\[|\]|`', '', match.group(1).strip())
                if meta_title:
                    blog_post.meta_title = meta_title[:60] if len(meta_title) > 60 else meta_title
                    break
        if not (blog_post.meta_title and blog_post.meta_title.strip()):
            blog_post.meta_title = (blog_post.title or '')[:60]

        # Extract meta description - multiple patterns; truncate if over 160 chars; fallback to content
        meta_description = None
        patterns = [
            r'\*\*Meta Description:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
            r'Meta [Dd]escription\s*:?\s*(.+?)(?=\n\*\*|\n\n|$)',
            r'Meta Description:\s*(.+?)(?=\n\*\*|\n\n|$)',
            r'Description:\s*(.+?)(?=\n\*\*|\n\n|$)',
        ]
        for pattern in patterns:
            match = re.search(pattern, generated_metadata, re.IGNORECASE | re.DOTALL)
            if match:
                meta_description = re.sub(r'\*\*|\*|\[|\]|`', '', match.group(1).strip())
                meta_description = ' '.join(meta_description.split())
                if meta_description:
                    blog_post.meta_description = meta_description[:160] if len(meta_description) > 160 else meta_description
                    break
        if not (blog_post.meta_description and blog_post.meta_description.strip()):
            fallback_desc = (text_content or '').strip()[:160]
            blog_post.meta_description = fallback_desc or (blog_post.title or '')[:160]

        # Extract slug
        slug = None
        patterns = [
            r'\*\*URL Slug:\*\*\s*(.+?)(?:\n|$)',
            r'URL Slug:\s*(.+?)(?:\n|$)',
            r'Slug:\s*(.+?)(?:\n|$)',
        ]
        for pattern in patterns:
            match = re.search(pattern, generated_metadata, re.IGNORECASE)
            if match:
                slug = re.sub(r'\*\*|\*|\[|\]|`', '', match.group(1).strip())
                slug = slugify(slug)
                if slug and len(slug) <= 255:
                    if not BlogPost.objects.filter(slug=slug).exclude(pk=blog_post.pk).exists():
                        blog_post.slug = slug
                    break
        
        # Extract tags
        tags_text = None
        patterns = [
            r'\*\*Tags:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
            r'Tags:\s*(.+?)(?=\n\*\*|\n\n|$)',
        ]
        for pattern in patterns:
            match = re.search(pattern, generated_metadata, re.IGNORECASE | re.DOTALL)
            if match:
                tags_text = match.group(1).strip()
                break
        
        if tags_text:
            tags_text = re.sub(r'\*\*|\*|\[|\]|`', '', tags_text)
            tag_names = [tag.strip() for tag in tags_text.split(',') if tag.strip()]
            
            tags_to_add = []
            for tag_name in tag_names[:10]:
                if tag_name:
                    tag_slug = slugify(tag_name)
                    try:
                        tag = Tag.objects.get(slug=tag_slug)
                    except Tag.DoesNotExist:
                        try:
                            tag = Tag.objects.get(name__iexact=tag_name)
                        except Tag.DoesNotExist:
                            tag = Tag.objects.create(name=tag_name, slug=tag_slug)
                    tags_to_add.append(tag)
            
            if tags_to_add:
                blog_post.tags.set(tags_to_add)

        # Step 3: Auto-insert internal links into generated content
        internal_linking = {
            'enabled': bool(enable_internal_links),
            'mode': internal_links_mode,
            'limit': internal_links_limit,
            'suggestions_count': 0,
            'inserted_count': 0,
            'inserted': [],
            'used_ai': False,
            'fallback_to_rule_based': False,
            'ai_failure_reason': None,
            'ai_failure_details': {},
        }
        if enable_internal_links:
            suggestions = _build_internal_link_suggestions(blog_post, limit=internal_links_limit)
            updated_content = blog_post.content
            applied_links = []

            if internal_links_mode == 'ai':
                try:
                    updated_content, applied_links, used_ai, ai_failure_reason, ai_failure_details = _apply_internal_links_with_ai(
                        blog_post.content,
                        suggestions,
                        rag_service,
                        metadata_provider,
                        metadata_model,
                        max_links=internal_links_limit
                    )
                    internal_linking['used_ai'] = bool(used_ai)
                    internal_linking['ai_failure_reason'] = ai_failure_reason
                    internal_linking['ai_failure_details'] = ai_failure_details or {}
                except Exception as e:
                    import traceback
                    updated_content = blog_post.content
                    applied_links = []
                    internal_linking['ai_failure_reason'] = 'provider_error'
                    internal_linking['ai_failure_details'] = {
                        'provider': metadata_provider,
                        'model': metadata_model,
                        'error_message': str(e),
                        'traceback_preview': traceback.format_exc()[:1200],
                    }

            if not applied_links:
                updated_content, applied_links = _apply_internal_links_to_html(
                    blog_post.content,
                    suggestions,
                    max_links=internal_links_limit
                )
                if internal_links_mode == 'ai':
                    internal_linking['fallback_to_rule_based'] = True

            blog_post.content = updated_content
            internal_linking['suggestions_count'] = len(suggestions)
            internal_linking['inserted_count'] = len(applied_links)
            internal_linking['inserted'] = applied_links
        
        # Extract featured image alt text
        featured_image_desc = None
        patterns = [
            r'\*\*Featured Image Alt Text:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
            r'Featured Image Alt Text:\s*(.+?)(?=\n\*\*|\n\n|$)',
            r'\*\*Featured Image Description:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
            r'Featured Image Description:\s*(.+?)(?=\n\*\*|\n\n|$)',
        ]
        for pattern in patterns:
            match = re.search(pattern, generated_metadata, re.IGNORECASE | re.DOTALL)
            if match:
                featured_image_desc = re.sub(r'\*\*|\*|\[|\]|`', '', match.group(1).strip())
                featured_image_desc = ' '.join(featured_image_desc.split())
                if featured_image_desc:
                    if len(featured_image_desc) > 200:
                        featured_image_desc = featured_image_desc[:197] + '...'
                    blog_post.featured_image_description = featured_image_desc
                    break
        
        # Extract FAQ Section Title - try multiple patterns
        faq_title = None
        patterns = [
            r'\*\*FAQ Section Title:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
            r'FAQ Section Title:\s*(.+?)(?=\n\*\*|\n\n|$)',
            r'\*\*FAQ Title:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
            r'FAQ Title:\s*(.+?)(?=\n\*\*|\n\n|$)',
        ]
        for pattern in patterns:
            faq_title_match = re.search(pattern, generated_metadata, re.IGNORECASE | re.DOTALL)
            if faq_title_match:
                faq_title = faq_title_match.group(1).strip()
                # Remove markdown formatting if present
                faq_title = re.sub(r'\*\*|\*|\[|\]|`', '', faq_title).strip()
                # Remove newlines and extra spaces
                faq_title = ' '.join(faq_title.split())
                if faq_title and len(faq_title) <= 200:
                    blog_post.faq_title = faq_title
                    break
        
        # Extract FAQ - try multiple patterns
        faq_data = None
        # Try to find FAQ section - look for FAQ: followed by JSON array (may span multiple lines)
        patterns = [
            r'\*\*FAQ:\*\*\s*(\[.*?\])\s*(?=\n\*\*|\n\n|$)',
            r'FAQ:\s*(\[.*?\])\s*(?=\n\*\*|\n\n|$)',
            r'\*\*FAQ\*\*\s*(\[.*?\])\s*(?=\n\*\*|\n\n|$)',
            r'\*\*FAQ:\*\*\s*(\[[\s\S]*?\])',  # More flexible for multi-line
            r'FAQ:\s*(\[[\s\S]*?\])',  # More flexible for multi-line
        ]
        for pattern in patterns:
            faq_match = re.search(pattern, generated_metadata, re.IGNORECASE | re.DOTALL)
            if faq_match:
                faq_text = faq_match.group(1).strip()
                try:
                    # Try to parse as JSON
                    faq_data = json.loads(faq_text)
                    # Validate it's a list with proper structure
                    if isinstance(faq_data, list) and len(faq_data) > 0:
                        # Ensure each item has question and answer
                        valid_faq = []
                        for item in faq_data[:4]:  # Limit to 4 items
                            if isinstance(item, dict) and 'question' in item and 'answer' in item:
                                valid_faq.append({
                                    'question': str(item['question']).strip(),
                                    'answer': str(item['answer']).strip()
                                })
                        # Accept any number of valid FAQ items (1-4) instead of requiring exactly 4
                        if len(valid_faq) > 0:
                            blog_post.faq = valid_faq
                            break
                except (json.JSONDecodeError, ValueError, KeyError) as e:
                    # If JSON parsing fails, try next pattern
                    continue
        
        # If FAQ wasn't found with patterns, try to find JSON array anywhere after "FAQ:"
        if not blog_post.faq:
            # Find the position of "FAQ:" in the text
            faq_label_match = re.search(r'FAQ:', generated_metadata, re.IGNORECASE)
            if faq_label_match:
                # Get text after FAQ:
                text_after_faq = generated_metadata[faq_label_match.end():]
                # Try to find a JSON array - look for opening bracket and try to match closing bracket
                bracket_start = text_after_faq.find('[')
                if bracket_start != -1:
                    # Find matching closing bracket by counting brackets
                    bracket_count = 0
                    bracket_end = -1
                    for i in range(bracket_start, len(text_after_faq)):
                        if text_after_faq[i] == '[':
                            bracket_count += 1
                        elif text_after_faq[i] == ']':
                            bracket_count -= 1
                            if bracket_count == 0:
                                bracket_end = i + 1
                                break
                    
                    if bracket_end > bracket_start:
                        faq_text = text_after_faq[bracket_start:bracket_end].strip()
                        try:
                            faq_data = json.loads(faq_text)
                            if isinstance(faq_data, list) and len(faq_data) > 0:
                                valid_faq = []
                                for item in faq_data[:4]:
                                    if isinstance(item, dict) and 'question' in item and 'answer' in item:
                                        valid_faq.append({
                                            'question': str(item['question']).strip(),
                                            'answer': str(item['answer']).strip()
                                        })
                                if len(valid_faq) > 0:
                                    blog_post.faq = valid_faq
                        except (json.JSONDecodeError, ValueError, KeyError):
                            pass
        
        # Save blog post with metadata
        blog_post.save()
        
        # Log activities
        log_activity(
            'blog_post_created',
            f'Blog post "{blog_post.title}" was generated from post idea "{post_idea.title}"',
            user=None,  # API call, no user
            metadata={
                'blog_post_id': blog_post.id,
                'post_idea_id': post_idea.id,
                'provider': provider,
                'model': model
            }
        )
        
        log_activity(
            'blog_post_updated',
            f'Metadata generated for blog post "{blog_post.title}"',
            user=None,  # API call, no user
            metadata={
                'blog_post_id': blog_post.id,
                'provider': metadata_provider,
                'model': metadata_model
            }
        )
        
        # Get tag names for response
        tag_names = [tag.name for tag in blog_post.tags.all()]
        
        return JsonResponse({
            'success': True,
            'blog_post': {
                'id': blog_post.id,
                'title': blog_post.title,
                'slug': blog_post.slug,
                'meta_title': blog_post.meta_title,
                'meta_description': blog_post.meta_description,
                'published': blog_post.published,
                'created_at': blog_post.created_at.isoformat() if blog_post.created_at else None,
                'post_idea_id': post_idea.id,
                'tags': tag_names,
                'featured_image_description': blog_post.featured_image_description,
            },
            'generation_info': {
                'content_provider': provider,
                'content_model': model,
                'metadata_provider': metadata_provider,
                'metadata_model': metadata_model,
                'used_rag': use_rag,
                'internal_linking': internal_linking,
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON in request body'
        }, status=400)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


