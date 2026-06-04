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
import random
import requests
from ..models import PostIdea, Content, Tag, BlogPost
from ..utils import log_activity, is_idea_too_similar_with_embeddings
from ..rag_service import RAGService
from ..embedding_service import EmbeddingService
from ..llm_models import list_models_for_provider, get_default_model_for_provider
from pgvector.django import CosineDistance

@login_required
def post_idea_list(request):
    """Display list of all post ideas"""
    post_ideas = PostIdea.objects.all().prefetch_related('blog_posts')
    
    # Get total count before filtering
    total_count = post_ideas.count()
    
    # Apply search filter
    search_query = request.GET.get('search', '').strip()
    if search_query:
        post_ideas = post_ideas.filter(
            Q(title__icontains=search_query) | 
            Q(description__icontains=search_query)
        )
    
    # Apply sorting (default: newest first)
    sort_by = request.GET.get('sort', 'newest').strip()
    if sort_by == 'oldest':
        post_ideas = post_ideas.order_by('created_at')
    elif sort_by == 'title_asc':
        post_ideas = post_ideas.order_by('title')
    elif sort_by == 'title_desc':
        post_ideas = post_ideas.order_by('-title')
    else:
        # Default: newest first
        post_ideas = post_ideas.order_by('-created_at')
    
    # Get filtered count
    filtered_count = post_ideas.count()
    
    context = {
        'post_ideas': post_ideas,
        'search_query': search_query,
        'sort_by': sort_by,
        'total_count': total_count,
        'filtered_count': filtered_count,
    }
    
    return render(request, 'sources/post_idea_list.html', context)


@login_required
def post_idea_generate(request):
    """Generate post ideas using Ollama, OpenAI, or Gemini"""
    # Get available tags and content for selection
    tags = Tag.objects.all().order_by('name')
    # Don't load contents by default - they'll be loaded via AJAX when searching
    contents = []
    
    if request.method == 'POST':
        num_ideas = int(request.POST.get('num_ideas', 5))
        selected_tag_ids = request.POST.getlist('tags')
        selected_content_ids = request.POST.getlist('contents')
        provider = request.POST.get('provider', 'ollama').strip().lower()
        selected_model = request.POST.get('model', '').strip()
        
        # Get selected tags and contents
        selected_tags = Tag.objects.filter(pk__in=selected_tag_ids) if selected_tag_ids else []
        selected_contents = Content.objects.filter(pk__in=selected_content_ids) if selected_content_ids else []
        
        # Validate provider
        if provider not in ['ollama', 'openai', 'gemini']:
            messages.error(request, 'Invalid provider selected.')
            return redirect('sources:post_idea_list')
        
        # Get model - use selected model or fall back to defaults
        if not selected_model:
            selected_model = get_default_model_for_provider(provider)
        
        # Build context for prompt
        context_parts = []
        
        if selected_tags:
            tag_names = [tag.name for tag in selected_tags]
            context_parts.append(f"Tags/Categories: {', '.join(tag_names)}")
        
        if selected_contents:
            content_summaries = []
            for content in selected_contents[:5]:  # Limit to 5 contents to avoid too long prompts
                summary = f"- {content.title}"
                if content.content:
                    # Truncate content preview
                    content_preview = content.content[:300] if len(content.content) > 300 else content.content
                    summary += f"\n  Preview: {content_preview}..."
                content_summaries.append(summary)
            context_parts.append(f"Related Content:\n" + "\n".join(content_summaries))
        
        # Use helper function to generate ideas
        success, created_count, created_ideas, error_message, skipped_similar = _generate_post_ideas(
            num_ideas=num_ideas,
            provider=provider,
            model=selected_model,
            selected_tags=selected_tags,
            selected_contents=selected_contents,
            user=request.user
        )
        
        if success:
            if created_count > 0:
                message = f'Successfully generated {created_count} post idea(s) using {provider.upper()}!'
                if skipped_similar > 0:
                    message += f' ({skipped_similar} similar idea(s) were skipped to avoid duplicates)'
                messages.success(request, message)
            else:
                if skipped_similar > 0:
                    messages.warning(
                        request, 
                        f'All {skipped_similar} generated idea(s) were too similar to existing ideas and were skipped. '
                        f'Try generating ideas with different tags/content, or lower the similarity threshold.'
                    )
                else:
                    messages.warning(request, 'No valid ideas were generated. Please try again.')
            return redirect('sources:post_idea_list')
        else:
            messages.error(request, error_message)
    
    # Get default model for the form
    default_model = ''
    try:
        app_settings = Settings.get_settings()
        default_model = app_settings.default_tagging_model
    except Exception:
        default_model = 'gpt-oss:20b-cloud'
    
    # Check which providers are available
    has_openai_key = bool(getattr(settings, 'OPENAI_API_KEY', None))
    has_gemini_key = bool(getattr(settings, 'GEMINI_API_KEY', None))
    
    context = {
        'tags': tags,
        'contents': contents,
        'default_model': default_model,
        'has_openai_key': has_openai_key,
        'has_gemini_key': has_gemini_key,
    }
    return render(request, 'sources/post_idea_generate.html', context)


@login_required
def post_idea_search_content_api(request):
    """API endpoint to search content for post idea generation"""
    search_query = request.GET.get('q', '').strip()
    
    if not search_query:
        return JsonResponse({'contents': []})
    
    # Search in all contents that have content text
    contents = Content.objects.filter(
        has_content=True
    ).select_related('source').filter(
        Q(title__icontains=search_query) | 
        Q(content__icontains=search_query) |
        Q(source__name__icontains=search_query)
    ).order_by('-created_at')[:50]  # Limit to 50 results for performance
    
    results = []
    for content in contents:
        results.append({
            'id': content.id,
            'title': content.title,
            'content_type': content.get_content_type_display(),
            'source_name': content.source.name if content.source else None,
            'preview': content.content[:200] + '...' if content.content and len(content.content) > 200 else (content.content or '')
        })
    
    return JsonResponse({'contents': results})


@login_required
def post_idea_models_api(request):
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
            
            models = []
            if 'models' in data:
                for model_info in data['models']:
                    model_name = model_info.get('name', '')
                    if model_name:
                        models.append(model_name)
            
            # Sort models
            chinese_models = [m for m in models if any(keyword in m.lower() for keyword in ['qwen', 'chinese', 'zh', 'cn'])]
            other_models = [m for m in models if m not in chinese_models]
            
            def sort_key(model_name):
                name_lower = model_name.lower()
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
        models, error = list_models_for_provider(provider)
        if error:
            status = 400 if 'not set' in error else 502
            return JsonResponse({'error': error}, status=status)
        return JsonResponse({'models': models})
    
    else:
        return JsonResponse({'error': 'Invalid provider'}, status=400)


@login_required
def post_idea_add(request):
    """Add a new post idea"""
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        primary_keyword = request.POST.get('primary_keyword', '').strip() or None
        
        if title:
            post_idea = PostIdea.objects.create(
                title=title,
                description=description,
                primary_keyword=primary_keyword
            )
            # Log activity
            log_activity(
                'post_idea_created',
                f'Post idea "{post_idea.title}" was created',
                user=request.user,
                metadata={'post_idea_id': post_idea.id, 'title': post_idea.title}
            )
            messages.success(request, f'Post idea "{post_idea.title}" added successfully')
            return redirect('sources:post_idea_list')
        else:
            messages.error(request, 'Title is required')
    
    return render(request, 'sources/post_idea_form.html', {'action': 'Add'})


@login_required
def post_idea_edit(request, pk):
    """Edit an existing post idea"""
    try:
        post_idea = PostIdea.objects.get(pk=pk)
    except PostIdea.DoesNotExist:
        messages.error(request, 'Post idea not found')
        return redirect('sources:post_idea_list')
    
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        primary_keyword = request.POST.get('primary_keyword', '').strip() or None
        
        if title:
            old_title = post_idea.title
            post_idea.title = title
            post_idea.description = description
            post_idea.primary_keyword = primary_keyword
            post_idea.save()
            # Log activity
            log_activity(
                'post_idea_updated',
                f'Post idea "{post_idea.title}" was updated',
                user=request.user,
                metadata={'post_idea_id': post_idea.id, 'old_title': old_title, 'new_title': post_idea.title}
            )
            messages.success(request, f'Post idea "{post_idea.title}" updated successfully')
            return redirect('sources:post_idea_list')
        else:
            messages.error(request, 'Title is required')
    
    context = {
        'post_idea': post_idea,
        'action': 'Edit'
    }
    return render(request, 'sources/post_idea_form.html', context)


@login_required
def post_idea_delete(request, pk):
    """Delete a post idea"""
    try:
        post_idea = PostIdea.objects.get(pk=pk)
    except PostIdea.DoesNotExist:
        messages.error(request, 'Post idea not found')
        return redirect('sources:post_idea_list')
    
    if request.method == 'POST':
        title = post_idea.title
        post_idea_id = post_idea.id
        post_idea.delete()
        # Log activity
        log_activity(
            'post_idea_deleted',
            f'Post idea "{title}" was deleted',
            user=request.user,
            metadata={'post_idea_id': post_idea_id, 'title': title}
        )
        messages.success(request, f'Post idea "{title}" deleted successfully')
        return redirect('sources:post_idea_list')
    
    context = {
        'post_idea': post_idea
    }
    return render(request, 'sources/post_idea_confirm_delete.html', context)


def _generate_post_ideas(num_ideas, provider, model, selected_tags=None, selected_contents=None, user=None, max_retries=5, similarity_threshold=0.70):
    """
    Helper function to generate post ideas using the specified provider and model.
    Automatically retries generation until the requested number of valid (non-duplicate) ideas are found.
    Returns tuple: (success: bool, created_count: int, created_ideas: list, error_message: str, skipped_similar: int)
    
    Args:
        num_ideas: Number of ideas to generate
        provider: AI provider ('ollama', 'openai', or 'gemini')
        model: Model name to use
        selected_tags: Optional list of tags to use as context
        selected_contents: Optional list of content items to use as context
        user: Optional user object for logging
        max_retries: Maximum number of retry attempts (default: 5)
        similarity_threshold: Similarity threshold for duplicate detection (0.0-1.0, default: 0.70)
            Higher threshold = more strict (only flag near-duplicates)
            0.92 = very strict (only near-duplicates), 0.9 = strict, 0.85 = moderate, 0.7 = lenient
    """
    selected_tags = selected_tags or []
    selected_contents = selected_contents or []
    
    # Initialize embedding service for similarity checking
    embedding_service = None
    try:
        embedding_service = EmbeddingService()
    except (ValueError, ImportError):
        # Embedding service not available, skip similarity checking
        pass
    
    # Get existing ideas with embeddings for comparison (load once, update as we create)
    existing_ideas_with_embeddings = list(
        PostIdea.objects.filter(title_embedding__isnull=False)
    ) if embedding_service else []
    
    # Build context for prompt
    context_parts = []
    
    if selected_tags:
        tag_names = [tag.name for tag in selected_tags]
        context_parts.append(f"Tags/Categories: {', '.join(tag_names)}")
    
    if selected_contents:
        content_summaries = []
        for content in selected_contents[:5]:  # Limit to 5 contents to avoid too long prompts
            summary = f"- {content.title}"
            if content.content:
                # Truncate content preview
                content_preview = content.content[:300] if len(content.content) > 300 else content.content
                summary += f"\n  Preview: {content_preview}..."
            content_summaries.append(summary)
        context_parts.append(f"Related Content:\n" + "\n".join(content_summaries))
    
    context_text = "\n\n".join(context_parts) if context_parts else "General blog post ideas about China, Chinese culture, travel, history, and related topics."
    
    # Track totals across all retries
    total_created_count = 0
    total_created_ideas = []
    total_skipped_similar = 0
    total_skipped_titles = []
    total_attempts = 0
    ideas_still_needed = num_ideas
    
    # Get sample of existing idea titles to avoid (for diversity)
    existing_titles_sample = list(PostIdea.objects.values_list('title', flat=True)[:20])
    existing_titles_text = ""
    if existing_titles_sample:
        existing_titles_text = f"\n\n### AVOID THESE TOPICS (already covered):\n" + "\n".join([f"- {title}" for title in existing_titles_sample[:15]])
    
    # Get random tags for diversity (if available) - prepare list once
    all_tags = list(Tag.objects.all())
    
    # Call API to generate ideas
    try:
        import requests
        import json
    except ImportError:
        return False, 0, [], 'requests library required. Install with: pip install requests', 0
    
    # Retry loop: continue generating until we have enough valid ideas
    while ideas_still_needed > 0 and total_attempts < max_retries:
        total_attempts += 1
        # Increase batch size on retries to get more variety
        batch_size = max(ideas_still_needed, 5 + (total_attempts - 1) * 2)  # 5, 7, 9, 11, 13...
        
        # Increase creativity on retries
        creativity_boost = ""
        if total_attempts > 1:
            creativity_boost = f"""
### CREATIVITY REQUIREMENT (Attempt #{total_attempts}):
- **BE MORE CREATIVE AND DIVERSE** - Previous attempts generated ideas too similar to existing ones.
- Explore **less common destinations** (beyond Beijing, Shanghai, Xi'an, Chengdu, Yunnan).
- Consider **unique angles**: specific neighborhoods, festivals, activities, transportation modes, accommodation types, travel styles (budget, luxury, solo, family, etc.).
- Think about **niche topics**: photography spots, hiking trails, local markets, traditional crafts, specific dishes, regional dialects, local customs, etc.
- Avoid repeating the same format (e.g., don't keep generating "X-Day Itinerary" or "Best Time to Visit X").
"""
        
        # Get random tags for diversity on retries
        random_tags_text = ""
        if total_attempts > 1 and len(all_tags) >= 3:
            random_tags = random.sample(all_tags, min(3, len(all_tags)))
            random_tag_names = [tag.name for tag in random_tags]
            random_tags_text = f"\n\n### EXPLORE THESE TOPICS (for diversity):\n" + ", ".join(random_tag_names)
        
        # Build prompt with current iteration values
        prompt = f"""
        Generate {batch_size} high-quality blog post ideas for a China travel blog.

Your ideas must be directly useful for people planning a trip to China.  
Avoid abstract cultural topics unless they clearly help a traveler understand a place, activity, or tradition they can experience on a trip.

Use the following context (optional reference material):
{context_text}{existing_titles_text}{random_tags_text}{creativity_boost}

### Requirements
- Each idea must clearly answer a real search intent a traveler might have.
- Ideas should be practical, specific, and actionable: itineraries, travel guides, food recommendations, destination highlights, logistics, tips, or seasonal advice.
- Avoid purely cultural or historical analysis unless it connects directly to a travel experience.
- **IMPORTANT: Ensure each idea is DISTINCT and covers a different topic or angle. Avoid generating multiple variations of the same idea (e.g., don't generate "How to Book Trains" and "Train Booking Guide" - they're too similar).**
- **CRITICAL: If this is a retry attempt, you MUST generate ideas that are significantly different from common topics. Explore unique angles, less-visited destinations, or specific niches.**
- Each idea must contain:
  1. A compelling and SEO-friendly title (50–80 characters)
  2. A brief description (1–2 sentences) explaining what the post covers and why it helps a traveler.
- Cover a diverse range of regions, themes, and traveler needs.

### Tone Guidance
Prioritize topics that answer searches like:
- "Best things to do in ___"
- "Where to eat in ___"
- "Travel guide"
- "Hidden gems in ___"
- "Is ___ worth visiting?"
- "How to get from ___ to ___"
- "Best time to visit ___"
- "What to eat in ___"
- "7-day itinerary for ___"

### Important
Every idea must be explicitly linked to travel, trip planning, or on-the-ground experience in China.

### Output Format
Respond in JSON only:

{{
  "ideas": [
    {{
      "title": "Post title here",
      "description": "Brief summary explaining the travel value",
      "primary_keyword": "Main SEO keyword for this post (e.g., 'Chengdu travel guide', 'China visa requirements')"
    }}
  ]
}}

Generate exactly {batch_size} ideas.
Response:
"""
        
        response_text = None
        api_error = None
        
        try:
            if provider == 'ollama':
                # Call Ollama
                ollama_url = getattr(settings, 'OLLAMA_URL', 'http://localhost:11434')
                url = f"{ollama_url}/api/generate"
                
                # Increase temperature on retries for more creativity
                base_temp = 0.8
                retry_temp = min(1.2, base_temp + (total_attempts - 1) * 0.1)  # 0.8, 0.9, 1.0, 1.1, 1.2
                
                payload = {
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": retry_temp,
                        "top_p": 0.9,
                    }
                }
                
                response = requests.post(url, json=payload, timeout=120)
                response.raise_for_status()
                result = response.json()
                response_text = result.get('response', '').strip()
                
            elif provider == 'openai':
                # Call OpenAI
                api_key = getattr(settings, 'OPENAI_API_KEY', None)
                if not api_key:
                    api_error = 'OPENAI_API_KEY is not set in settings. Please configure it to use OpenAI.'
                    if total_attempts >= max_retries:
                        return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                    continue  # Continue while loop
            
                try:
                    from openai import OpenAI
                except ImportError:
                    api_error = 'openai library required. Install with: pip install openai'
                    if total_attempts >= max_retries:
                        return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                    continue  # Continue while loop
                
                client = OpenAI(api_key=api_key)
                
                # Check model type for parameter compatibility
                is_gpt5 = 'gpt-5' in model.lower()
                is_newer_model = any(keyword in model.lower() for keyword in ['gpt-4o', 'gpt-5', 'o1', 'o3'])
                
                # Build request parameters
                request_params = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant that generates blog post ideas for a China travel blog. Always respond with valid JSON only."},
                        {"role": "user", "content": prompt}
                    ],
                }
                
                # GPT-5 only supports default temperature (1), so don't set it
                if not is_gpt5:
                    # Increase temperature on retries for more creativity
                    base_temp = 0.8
                    retry_temp = min(1.2, base_temp + (total_attempts - 1) * 0.1)  # 0.8, 0.9, 1.0, 1.1, 1.2
                    request_params["temperature"] = retry_temp
                
                # Use appropriate parameter based on model
                if is_newer_model:
                    request_params["max_completion_tokens"] = 2000
                else:
                    request_params["max_tokens"] = 2000
                
                response = client.chat.completions.create(**request_params)
                response_text = response.choices[0].message.content.strip()
                
            elif provider == 'gemini':
                # Call Gemini
                api_key = getattr(settings, 'GEMINI_API_KEY', None)
                if not api_key:
                    api_error = 'GEMINI_API_KEY is not set in settings. Please configure it to use Gemini.'
                    if total_attempts >= max_retries:
                        return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                    continue  # Continue while loop
                
                try:
                    import google.generativeai as genai
                except ImportError:
                    api_error = 'google-generativeai library required. Install with: pip install google-generativeai'
                    if total_attempts >= max_retries:
                        return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                    continue  # Continue while loop
                
                genai.configure(api_key=api_key)
                
                # Configure safety settings to be more permissive for content generation
                # This helps avoid false positives when generating blog post ideas
                # Note: gemini-3-pro-preview may have stricter filters, so we use BLOCK_ONLY_HIGH for all
                try:
                    from google.generativeai.types import HarmCategory, HarmBlockThreshold
                    safety_settings = {
                        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
                        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_ONLY_HIGH,
                        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
                        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
                    }
                except (ImportError, AttributeError):
                    # Fallback to string-based settings if enum import fails
                    safety_settings = {
                        "HARM_CATEGORY_HARASSMENT": "BLOCK_ONLY_HIGH",
                        "HARM_CATEGORY_HATE_SPEECH": "BLOCK_ONLY_HIGH",
                        "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_ONLY_HIGH",
                        "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_ONLY_HIGH",
                    }
                
                genai_model = genai.GenerativeModel(model, safety_settings=safety_settings)
                # Increase temperature on retries for more creativity
                base_temp = 0.8
                retry_temp = min(1.2, base_temp + (total_attempts - 1) * 0.1)  # 0.8, 0.9, 1.0, 1.1, 1.2
                
                # Calculate appropriate token limit based on batch size
                # Each idea needs ~200-300 tokens (title + description + keyword), so we need headroom
                # Use at least 4000 tokens, or scale with batch size
                max_tokens = max(4000, batch_size * 500)
                # Cap at 8192 (common limit for many Gemini models)
                max_tokens = min(max_tokens, 8192)
                
                response = genai_model.generate_content(
                    prompt,
                    generation_config={
                        "temperature": retry_temp,
                        "max_output_tokens": max_tokens,
                    }
                )
                
                # Check for blocked responses or empty content BEFORE accessing response.text
                # Accessing response.text when finish_reason indicates a block will throw an exception
                if response.candidates and len(response.candidates) > 0:
                    candidate = response.candidates[0]
                    finish_reason = candidate.finish_reason
                    
                    # Handle different finish reasons
                    # finish_reason can be an integer (1=STOP, 2=MAX_TOKENS, 3=SAFETY, etc.) or string
                    finish_reason_str = str(finish_reason).upper() if finish_reason else ""
                    finish_reason_int = int(finish_reason) if isinstance(finish_reason, (int, str)) and str(finish_reason).isdigit() else None
                    
                    # Check for SAFETY blocks (finish_reason 3 or "SAFETY")
                    # Note: Sometimes SAFETY may be reported as finish_reason 2 when there are no parts
                    if finish_reason_int == 3 or "SAFETY" in finish_reason_str:
                        reason_text = "SAFETY (blocked by content safety filters)"
                        # Try to get detailed safety ratings if available
                        safety_info = ""
                        blocked_categories = []
                        if hasattr(candidate, 'safety_ratings') and candidate.safety_ratings:
                            for rating in candidate.safety_ratings:
                                # Check if this rating caused the block
                                # HIGH or MEDIUM probability ratings are likely the cause
                                prob_name = rating.probability.name if hasattr(rating.probability, 'name') else str(rating.probability)
                                cat_name = rating.category.name if hasattr(rating.category, 'name') else str(rating.category)
                                if prob_name not in ['NEGLIGIBLE', 'LOW']:
                                    blocked_categories.append(f"{cat_name}: {prob_name}")
                            if blocked_categories:
                                safety_info = f" Blocked categories: {', '.join(blocked_categories)}."
                        
                        # Also check prompt_feedback for additional info
                        if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                            if hasattr(response.prompt_feedback, 'safety_ratings') and response.prompt_feedback.safety_ratings:
                                prompt_blocked = []
                                for rating in response.prompt_feedback.safety_ratings:
                                    prob_name = rating.probability.name if hasattr(rating.probability, 'name') else str(rating.probability)
                                    cat_name = rating.category.name if hasattr(rating.category, 'name') else str(rating.category)
                                    if prob_name not in ['NEGLIGIBLE', 'LOW']:
                                        prompt_blocked.append(f"{cat_name}: {prob_name}")
                                if prompt_blocked:
                                    safety_info += f" Prompt blocked categories: {', '.join(prompt_blocked)}."
                        
                        api_error = f'Gemini API response was blocked ({reason_text}).{safety_info} This may be a false positive. Try: 1) Using a different model (e.g., gemini-1.5-pro), 2) Simplifying the prompt, or 3) Using a different provider.'
                        if total_attempts >= max_retries:
                            return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                        continue  # Continue while loop
                    
                    # Check for MAX_TOKENS (finish_reason 2 or "MAX_TOKENS")
                    # Note: finish_reason 2 can also indicate SAFETY when there are no parts
                    # We'll try to access response.text below and handle the exception if it fails
                    if finish_reason_int == 2 or "MAX_TOKENS" in finish_reason_str:
                        # Try to access response.text - if it fails, it's likely a safety block
                        try:
                            if hasattr(response, 'text') and response.text:
                                # It's actually MAX_TOKENS with partial content
                                api_error = f'Gemini API response hit the token limit (MAX_TOKENS). The max_output_tokens ({max_tokens}) may be too low for the requested batch size. Try requesting fewer ideas at once or using a different provider.'
                                if total_attempts >= max_retries:
                                    return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                                continue  # Continue while loop
                        except (ValueError, AttributeError) as e:
                            # finish_reason 2 but can't access text - likely a safety block
                            reason_text = "SAFETY (blocked by content safety filters)"
                            safety_info = ""
                            blocked_categories = []
                            if hasattr(candidate, 'safety_ratings') and candidate.safety_ratings:
                                for rating in candidate.safety_ratings:
                                    prob_name = rating.probability.name if hasattr(rating.probability, 'name') else str(rating.probability)
                                    cat_name = rating.category.name if hasattr(rating.category, 'name') else str(rating.category)
                                    if prob_name not in ['NEGLIGIBLE', 'LOW']:
                                        blocked_categories.append(f"{cat_name}: {prob_name}")
                                if blocked_categories:
                                    safety_info = f" Blocked categories: {', '.join(blocked_categories)}."
                            
                            # Check prompt_feedback
                            if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                                if hasattr(response.prompt_feedback, 'safety_ratings') and response.prompt_feedback.safety_ratings:
                                    prompt_blocked = []
                                    for rating in response.prompt_feedback.safety_ratings:
                                        prob_name = rating.probability.name if hasattr(rating.probability, 'name') else str(rating.probability)
                                        cat_name = rating.category.name if hasattr(rating.category, 'name') else str(rating.category)
                                        if prob_name not in ['NEGLIGIBLE', 'LOW']:
                                            prompt_blocked.append(f"{cat_name}: {prob_name}")
                                    if prompt_blocked:
                                        safety_info += f" Prompt blocked categories: {', '.join(prompt_blocked)}."
                            
                            api_error = f'Gemini API response was blocked ({reason_text}).{safety_info} This may be a false positive. Try: 1) Using a different model (e.g., gemini-1.5-pro), 2) Simplifying the prompt, or 3) Using a different provider.'
                            if total_attempts >= max_retries:
                                return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                            continue  # Continue while loop
                    
                    # Check for RECITATION (finish_reason 4 or "RECITATION")
                    if finish_reason_int == 4 or "RECITATION" in finish_reason_str:
                        api_error = 'Gemini API response was blocked (RECITATION - blocked due to potential recitation). Try adjusting the prompt or using a different provider.'
                        if total_attempts >= max_retries:
                            return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                        continue  # Continue while loop
                    
                    # Check for OTHER reasons
                    if finish_reason_int and finish_reason_int > 4:
                        api_error = f'Gemini API response was blocked (finish_reason: {finish_reason}). Try adjusting the prompt or using a different provider.'
                        if total_attempts >= max_retries:
                            return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                        continue  # Continue while loop
                
                # Now safely try to access response.text
                try:
                    if not response or not hasattr(response, 'text') or not response.text:
                        api_error = f'Gemini API returned empty or invalid response'
                        if total_attempts >= max_retries:
                            return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                        continue  # Continue while loop
                    response_text = response.text.strip()
                except ValueError as e:
                    # This exception occurs when response.text is accessed but there's no valid Part
                    # Usually happens when finish_reason indicates a block
                    error_str = str(e)
                    if "valid `Part`" in error_str or "finish_reason" in error_str:
                        # Try to get safety information from the response
                        safety_info = ""
                        if hasattr(response, 'candidates') and response.candidates and len(response.candidates) > 0:
                            candidate = response.candidates[0]
                            blocked_categories = []
                            if hasattr(candidate, 'safety_ratings') and candidate.safety_ratings:
                                for rating in candidate.safety_ratings:
                                    prob_name = rating.probability.name if hasattr(rating.probability, 'name') else str(rating.probability)
                                    cat_name = rating.category.name if hasattr(rating.category, 'name') else str(rating.category)
                                    if prob_name not in ['NEGLIGIBLE', 'LOW']:
                                        blocked_categories.append(f"{cat_name}: {prob_name}")
                                if blocked_categories:
                                    safety_info = f" Blocked categories: {', '.join(blocked_categories)}."
                            
                            # Check prompt_feedback
                            if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                                if hasattr(response.prompt_feedback, 'safety_ratings') and response.prompt_feedback.safety_ratings:
                                    prompt_blocked = []
                                    for rating in response.prompt_feedback.safety_ratings:
                                        prob_name = rating.probability.name if hasattr(rating.probability, 'name') else str(rating.probability)
                                        cat_name = rating.category.name if hasattr(rating.category, 'name') else str(rating.category)
                                        if prob_name not in ['NEGLIGIBLE', 'LOW']:
                                            prompt_blocked.append(f"{cat_name}: {prob_name}")
                                    if prompt_blocked:
                                        safety_info += f" Prompt blocked categories: {', '.join(prompt_blocked)}."
                        
                        api_error = f'Gemini API response was blocked or filtered.{safety_info} This may be a false positive. Try: 1) Using a different model (e.g., gemini-1.5-pro), 2) Simplifying the prompt, or 3) Using a different provider. Error: {error_str}'
                    else:
                        api_error = f'Gemini API error: {error_str}'
                    if total_attempts >= max_retries:
                        return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                    continue  # Continue while loop
            else:
                api_error = f'Invalid provider: {provider}'
                if total_attempts >= max_retries:
                    return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                continue  # Continue while loop
            
            # Parse JSON response
        
        except requests.exceptions.ConnectionError as e:
            if provider == 'ollama':
                ollama_url = getattr(settings, 'OLLAMA_URL', 'http://localhost:11434')
                api_error = f'Could not connect to Ollama at {ollama_url}. Make sure Ollama is running.'
            else:
                api_error = f'Connection error: {str(e)}'
            if total_attempts >= max_retries:
                return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
            continue  # Continue while loop
        except Exception as e:
            error_str = str(e)
            # Check for API key errors
            if 'api_key' in error_str.lower() or 'authentication' in error_str.lower() or 'unauthorized' in error_str.lower():
                if provider == 'openai':
                    api_error = f'OpenAI API key error: {error_str}. Please check your OPENAI_API_KEY setting.'
                elif provider == 'gemini':
                    api_error = f'Gemini API key error: {error_str}. Please check your GEMINI_API_KEY setting.'
                else:
                    api_error = f'Authentication error: {error_str}'
            else:
                api_error = f'Error generating ideas with {provider.upper()}: {error_str}'
            if total_attempts >= max_retries:
                return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
            continue  # Continue while loop
        
        if response_text:
            # Try to extract JSON from response
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}')
            
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx + 1]
                try:
                    parsed = json.loads(json_str)
                    ideas = parsed.get('ideas', [])
                    
                    # Process ideas: check similarity and create valid ones
                    batch_created_count = 0
                    batch_created_ideas = []
                    batch_skipped_similar = 0
                    batch_skipped_titles = []
                    
                    for idea_data in ideas:
                        title = idea_data.get('title', '').strip()
                        description = idea_data.get('description', '').strip()
                        primary_keyword = idea_data.get('primary_keyword', '').strip() or None  # Use None instead of empty string
                        
                        if not title:
                            continue
                        
                        # Check for similarity using embeddings if available
                        too_similar = False
                        if embedding_service and existing_ideas_with_embeddings:
                            # Enable debug logging on retries to see similarity scores
                            debug_mode = total_attempts > 1
                            too_similar = is_idea_too_similar_with_embeddings(
                                title, 
                                existing_ideas_with_embeddings,
                                embedding_service,
                                similarity_threshold,
                                debug=debug_mode
                            )
                        
                        if too_similar:
                            batch_skipped_similar += 1
                            batch_skipped_titles.append(title)
                            if total_attempts == 1:
                                print(f"Skipping similar idea: {title}")
                            # Debug output is already printed by the function
                            continue
                        
                        # Generate embedding for the new idea
                        new_embedding = None
                        if embedding_service:
                            try:
                                new_embedding = embedding_service.generate_embedding(title)
                            except Exception as e:
                                print(f"Warning: Could not generate embedding for '{title}': {str(e)}")
                        
                        # Create the post idea
                        post_idea = PostIdea.objects.create(
                            title=title,
                            description=description,
                            primary_keyword=primary_keyword,
                            title_embedding=new_embedding
                        )
                        batch_created_count += 1
                        batch_created_ideas.append({'id': post_idea.id, 'title': post_idea.title})
                        
                        # Add to existing list for checking against in the same batch
                        if new_embedding:
                            existing_ideas_with_embeddings.append(post_idea)
                        
                        # Stop if we have enough ideas
                        if batch_created_count >= ideas_still_needed:
                            break
                        
                    # Update totals
                    total_created_count += batch_created_count
                    total_created_ideas.extend(batch_created_ideas)
                    total_skipped_similar += batch_skipped_similar
                    total_skipped_titles.extend(batch_skipped_titles)
                    ideas_still_needed -= batch_created_count
                    
                    # If we got some valid ideas but not enough, continue the loop
                    if ideas_still_needed > 0:
                        print(f"Generated {batch_created_count} valid ideas, {ideas_still_needed} still needed. Retrying...")
                        # Break out of try to continue while loop
                        break
                    else:
                        # We have enough ideas, break out of retry loop
                        break
                    
                except json.JSONDecodeError:
                    api_error = f'Failed to parse {provider.upper()} response as JSON. Response: {response_text[:200]}'
                    if total_attempts >= max_retries:
                        return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                    continue  # Continue while loop
            else:
                api_error = f'Invalid response format from {provider.upper()}. Response: {response_text[:200]}'
                if total_attempts >= max_retries:
                    return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                continue  # Continue while loop
        else:
            api_error = f'No response received from {provider.upper()}.'
            if total_attempts >= max_retries:
                return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
            continue  # Continue while loop
    
    # After retry loop completes
    if total_created_count > 0 or total_skipped_similar > 0:
        # Log activity
        message = f'{total_created_count} post idea(s) were generated using AI'
        if total_skipped_similar > 0:
            message += f' ({total_skipped_similar} similar ideas skipped)'
        if total_attempts > 1:
            message += f' (after {total_attempts} generation attempts)'
        
        log_activity(
            'post_ideas_generated',
            message,
            user=user,
            metadata={
                'count': total_created_count,
                'skipped_similar': total_skipped_similar,
                'num_requested': num_ideas,
                'total_attempts': total_attempts,
                'provider': provider,
                'model': model,
                'similarity_check': embedding_service is not None,
                'similarity_threshold': similarity_threshold if embedding_service else None,
                'tags_selected': [tag.id for tag in selected_tags] if selected_tags else [],
                'tags_names': [tag.name for tag in selected_tags] if selected_tags else [],
                'contents_selected': [content.id for content in selected_contents] if selected_contents else [],
                'created_ideas': total_created_ideas,
                'skipped_titles': total_skipped_titles if total_skipped_titles else [],
                'api_generated': user is None
            }
        )
    
    # Return success if we got at least some ideas, or if we exhausted retries
    if total_created_count > 0:
        return True, total_created_count, total_created_ideas, None, total_skipped_similar
    elif total_attempts >= max_retries:
        # All retries exhausted, return with error
        error_msg = api_error if 'api_error' in locals() and api_error else f'Could not generate valid ideas after {max_retries} attempts. {total_skipped_similar} ideas were skipped as duplicates.'
        return False, 0, [], error_msg, total_skipped_similar
    else:
        # Should not reach here, but handle it
        return False, 0, [], 'Unexpected error during idea generation', total_skipped_similar


