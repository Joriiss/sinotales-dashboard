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
from ..models import Source, Tag, ActivityLog, Settings
from ..forms import SettingsForm
from ..utils import log_activity
from ..rag_service import RAGService

@login_required
def agent_view(request):
    """Agent chat interface page"""
    # Get sources and tags for filters
    sources = Source.objects.all().order_by('name')
    tags = Tag.objects.all().order_by('name')
    
    context = {
        'sources': sources,
        'tags': tags,
    }
    return render(request, 'sources/agent.html', context)



@login_required
def agent_chat_api(request):
    """API endpoint for chat messages"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    try:
        data = json.loads(request.body)
        question = data.get('question', '').strip()
        provider = data.get('provider', 'ollama').strip().lower()
        model = data.get('model', '').strip()
        num_chunks = int(data.get('num_chunks', 5))
        source_id = data.get('source_id')
        tag_ids = data.get('tag_ids', [])
        content_type = data.get('content_type')
        conversation_history = data.get('conversation_history', [])
        web_search_enabled = data.get('web_search_enabled', False)
        
        # Validate inputs
        if not question:
            return JsonResponse({'error': 'Question is required'}, status=400)
        
        if provider not in ['ollama', 'openai', 'gemini']:
            return JsonResponse({'error': 'Invalid provider. Must be one of: ollama, openai, gemini'}, status=400)
        
        if not model:
            return JsonResponse({'error': 'Model is required'}, status=400)
        
        if num_chunks < 1 or num_chunks > 20:
            num_chunks = 5
        
        # Convert source_id to int if provided
        if source_id:
            try:
                source_id = int(source_id)
            except (ValueError, TypeError):
                source_id = None
        
        # Convert tag_ids to list of ints
        if tag_ids:
            try:
                tag_ids = [int(tid) for tid in tag_ids if tid]
            except (ValueError, TypeError):
                tag_ids = []
        
        # Initialize RAG service
        try:
            rag_service = RAGService()
        except ValueError as e:
            # Handle missing OpenAI API key
            if "OPENAI_API_KEY" in str(e):
                return JsonResponse({
                    'error': str(e),
                    'success': False,
                    'error_type': 'missing_openai_key'
                }, status=400)
            raise
        
        # Generate response
        try:
            answer, sources = rag_service.generate_response(
                question=question,
                provider=provider,
                model=model,
                num_chunks=num_chunks,
                source_id=source_id,
                tag_ids=tag_ids if tag_ids else None,
                content_type=content_type,
                conversation_history=conversation_history,
                web_search_enabled=web_search_enabled
            )
            
            return JsonResponse({
                'answer': answer,
                'sources': sources,
                'success': True
            })
        except Exception as e:
            return JsonResponse({
                'error': str(e),
                'success': False
            }, status=500)
    
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# Logs View

@login_required
def logs_view(request):
    """Display activity logs"""
    logs = ActivityLog.objects.select_related('source', 'content').all()
    
    # Filtering
    activity_type_filter = request.GET.get('activity_type', '').strip()
    if activity_type_filter:
        logs = logs.filter(activity_type=activity_type_filter)
    
    # Pagination
    paginator = Paginator(logs, 50)  # Show 50 logs per page
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    # Get activity type choices for filter
    activity_types = ActivityLog.ACTIVITY_TYPE_CHOICES
    
    context = {
        'logs': page_obj,
        'activity_types': activity_types,
        'activity_type_filter': activity_type_filter,
    }
    return render(request, 'sources/logs.html', context)



@login_required
def settings_view(request):
    """View and edit application settings"""
    settings = Settings.get_settings()
    
    if request.method == 'POST':
        form = SettingsForm(request.POST, instance=settings)
        if form.is_valid():
            form.save()
            log_activity(
                'settings_updated',
                'Application settings were updated',
                user=request.user,
                metadata={
                    'tagging_provider': form.cleaned_data.get('default_tagging_provider'),
                    'tagging_model': form.cleaned_data.get('default_tagging_model'),
                }
            )
            messages.success(request, 'Settings updated successfully!')
            return redirect('sources:settings')
    else:
        form = SettingsForm(instance=settings)
    
    context = {
        'form': form,
        'settings': settings,
    }
    return render(request, 'sources/settings.html', context)



