from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.views.decorators.http import require_http_methods
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.db.models.functions import Length
from django.http import JsonResponse
from django.conf import settings
from django.utils.html import json_script
import json
from .models import Source, Content, Tag, ContentChunk, ActivityLog, Settings
from .forms import SourceForm, ContentForm, SettingsForm
from .rag_service import RAGService
from .utils import log_activity
from .content_processing_service import ContentProcessingService


class CustomLoginView(LoginView):
    """Custom login view with redirect to sources list"""
    template_name = 'registration/login.html'
    redirect_authenticated_user = True


class CustomLogoutView(LogoutView):
    """Custom logout view"""
    next_page = 'login'


@login_required
def dashboard(request):
    """Dashboard homepage with statistics"""
    # Basic counts
    total_sources = Source.objects.count()
    total_contents = Content.objects.count()
    active_sources = Source.objects.filter(is_active=True).count()
    
    # Content breakdown by type
    content_by_type = Content.objects.values('content_type').annotate(
        count=Count('id')
    ).order_by('-count')
    
    # Source breakdown by type
    sources_by_type = Source.objects.values('source_type').annotate(
        count=Count('id')
    ).order_by('-count')
    
    # Content with text
    contents_with_text = Content.objects.filter(has_content=True).count()
    contents_processed = Content.objects.filter(processed=True).count()
    
    # Calculate total words and data size
    # Use database aggregation for better performance
    total_chars_result = Content.objects.filter(has_content=True).aggregate(
        total_length=Sum(Length('content'))
    )
    total_chars = total_chars_result['total_length'] or 0
    
    # Estimate: ~5 chars per word on average
    total_words = total_chars // 5 if total_chars else 0
    # Estimate: UTF-8 encoding, average 2 bytes per character
    total_mb = (total_chars * 2) / (1024 * 1024) if total_chars else 0
    
    # Language breakdown
    sources_by_language = Source.objects.values('language').annotate(
        count=Count('id')
    ).order_by('-count')
    
    # Recent activity
    recent_contents = Content.objects.select_related('source').prefetch_related('tags').order_by('-created_at')[:10]
    recent_sources = Source.objects.order_by('-created_at')[:5]
    
    # Content by source (top sources)
    top_sources = Source.objects.annotate(
        content_count=Count('contents')
    ).filter(content_count__gt=0).order_by('-content_count')[:10]
    
    # Tags statistics
    total_tags = Tag.objects.count()
    contents_with_tags = Content.objects.filter(tags__isnull=False).distinct().count()
    
    # Chunks and embeddings statistics
    total_chunks = ContentChunk.objects.count()
    chunks_with_embeddings = ContentChunk.objects.filter(embedding__isnull=False).count()
    contents_with_embeddings = Content.objects.filter(
        chunks__embedding__isnull=False
    ).distinct().count()
    
    # Calculate embedding percentage
    embedding_percentage = (
        (chunks_with_embeddings / total_chunks * 100) 
        if total_chunks > 0 else 0
    )
    
    # Top tags
    top_tags = Tag.objects.annotate(
        content_count=Count('contents')
    ).filter(content_count__gt=0).order_by('-content_count')[:10]
    
    context = {
        'total_sources': total_sources,
        'total_contents': total_contents,
        'active_sources': active_sources,
        'contents_with_text': contents_with_text,
        'contents_processed': contents_processed,
        'content_by_type': content_by_type,
        'sources_by_type': sources_by_type,
        'sources_by_language': sources_by_language,
        'total_words': total_words,
        'total_mb': total_mb,
        'recent_contents': recent_contents,
        'recent_sources': recent_sources,
        'top_sources': top_sources,
        'total_tags': total_tags,
        'contents_with_tags': contents_with_tags,
        'total_chunks': total_chunks,
        'chunks_with_embeddings': chunks_with_embeddings,
        'contents_with_embeddings': contents_with_embeddings,
        'embedding_percentage': embedding_percentage,
        'top_tags': top_tags,
    }
    return render(request, 'sources/dashboard.html', context)


@login_required
def source_list(request):
    """Display list of all sources"""
    # Annotate sources with content counts
    sources = Source.objects.annotate(
        total_contents=Count('contents'),
        contents_with_text=Count('contents', filter=Q(contents__has_content=True)),
        processed_contents=Count('contents', filter=Q(contents__processed=True)),
    ).all()
    
    # Apply filters
    source_type_filter = request.GET.get('source_type', '').strip()
    language_filter = request.GET.get('language', '').strip()
    status_filter = request.GET.get('status', '').strip()
    search_query = request.GET.get('search', '').strip()
    
    if source_type_filter:
        sources = sources.filter(source_type=source_type_filter)
    
    if language_filter:
        sources = sources.filter(language=language_filter)
    
    if status_filter:
        if status_filter == 'active':
            sources = sources.filter(is_active=True)
        elif status_filter == 'inactive':
            sources = sources.filter(is_active=False)
    
    if search_query:
        sources = sources.filter(name__icontains=search_query)
    
    # Get filter choices - only show types and languages that exist in the database
    existing_source_types = Source.objects.values_list('source_type', flat=True).distinct().order_by('source_type')
    existing_languages = Source.objects.values_list('language', flat=True).distinct().order_by('language')
    
    # Map to display labels
    source_types = [
        (st, dict(Source.SOURCE_TYPE_CHOICES)[st]) 
        for st in existing_source_types 
        if st in dict(Source.SOURCE_TYPE_CHOICES)
    ]
    languages = [
        (lang, dict(Source.LANGUAGE_CHOICES)[lang]) 
        for lang in existing_languages 
        if lang in dict(Source.LANGUAGE_CHOICES)
    ]
    
    context = {
        'sources': sources,
        'source_types': source_types,
        'languages': languages,
        'source_type_filter': source_type_filter,
        'language_filter': language_filter,
        'status_filter': status_filter,
        'search_query': search_query,
    }
    return render(request, 'sources/source_list.html', context)


@login_required
def source_add(request):
    """Add a new source"""
    if request.method == 'POST':
        form = SourceForm(request.POST)
        if form.is_valid():
            source = form.save()
            log_activity(
                'source_created',
                f'Source "{source.name}" ({source.get_source_type_display()}) was created',
                user=request.user,
                source=source
            )
            messages.success(request, f'Source "{source.name}" added successfully!')
            return redirect('sources:source_list')
    else:
        form = SourceForm()
    
    context = {
        'form': form,
        'action': 'Add',
    }
    return render(request, 'sources/source_form.html', context)


@login_required
def source_edit(request, pk):
    """Edit an existing source"""
    source = get_object_or_404(Source, pk=pk)
    
    # Handle get_videos action for YouTube sources
    if request.method == 'POST' and 'get_videos' in request.POST:
        if source.source_type != 'youtube':
            messages.error(request, 'Get Videos is only available for YouTube sources.')
            return redirect('sources:source_edit', pk=source.pk)
        
        if not source.channel_id:
            messages.error(request, 'Channel ID is required to get videos.')
            return redirect('sources:source_edit', pk=source.pk)
        
        try:
            from .youtube_service import get_channel_videos
            from .video_filter_service import is_video_relevant
            from django.db import transaction
            
            print(f"\n{'='*60}")
            print(f"Fetching videos from YouTube channel: {source.name}")
            print(f"Channel ID: {source.channel_id}")
            print(f"Include Shorts: {source.include_shorts}")
            print(f"Filter Videos: {source.filter_videos}")
            print(f"{'='*60}\n")
            
            # Fetch videos from YouTube (fetch details if filtering is enabled)
            fetch_details = source.filter_videos
            print("Step 1: Fetching videos from YouTube API...")
            all_videos = get_channel_videos(
                channel_id=source.channel_id,
                include_shorts=source.include_shorts,
                fetch_details=fetch_details
            )
            
            if not all_videos:
                messages.info(request, f'No videos found for channel "{source.name}".')
                return redirect('sources:source_edit', pk=source.pk)
            
            total_found = len(all_videos)
            print(f"✓ Found {total_found} video(s) from YouTube\n")
            
            # Step 2: Check which videos already exist (BEFORE filtering)
            print("Step 2: Checking for existing videos in database...")
            existing_video_ids = set(
                Content.objects.filter(source=source)
                .values_list('external_id', flat=True)
            )
            
            # Filter out videos that already exist
            new_videos = []
            already_exist_count = 0
            for video in all_videos:
                if video['video_id'] in existing_video_ids:
                    already_exist_count += 1
                else:
                    new_videos.append(video)
            
            print(f"✓ {already_exist_count} video(s) already exist, {len(new_videos)} new video(s) to process\n")
            
            if not new_videos:
                messages.info(
                    request,
                    f'All {total_found} video(s) from "{source.name}" already exist in the database.'
                )
                return redirect('sources:source_edit', pk=source.pk)
            
            # Step 3: Filter videos if filter_videos is enabled (only filter NEW videos)
            filtered_count = 0
            videos_to_create = new_videos
            if source.filter_videos:
                print(f"Step 3: Filtering {len(new_videos)} new video(s) using AI...")
                filtered_videos = []
                for i, video in enumerate(new_videos, 1):
                    title_preview = video['title'][:50] + "..." if len(video['title']) > 50 else video['title']
                    print(f"  [{i}/{len(new_videos)}] Checking: {title_preview}")
                    
                    try:
                        is_relevant = is_video_relevant(
                            title=video['title'],
                            description=video.get('description', ''),
                            tags=video.get('tags', [])
                        )
                        if is_relevant:
                            print(f"    ✓ Relevant - will be added")
                            filtered_videos.append(video)
                        else:
                            print(f"    ✗ Not relevant - filtered out")
                            filtered_count += 1
                    except Exception as e:
                        # If filtering fails, include the video (be permissive)
                        print(f"    ⚠ Error filtering (including video): {str(e)}")
                        filtered_videos.append(video)
                
                videos_to_create = filtered_videos
                print(f"\n✓ Filtering complete: {len(filtered_videos)} relevant, {filtered_count} filtered out\n")
            
            if not videos_to_create:
                messages.info(
                    request, 
                    f'No relevant new videos found for channel "{source.name}" after filtering. '
                    f'{filtered_count} video(s) were filtered out, {already_exist_count} already exist.'
                )
                return redirect('sources:source_edit', pk=source.pk)
            
            # Step 4: Create Content entries for each video
            print(f"Step 4: Creating {len(videos_to_create)} content entry/entries...")
            created_count = 0
            
            with transaction.atomic():
                for i, video in enumerate(videos_to_create, 1):
                    title_preview = video['title'][:50] + "..." if len(video['title']) > 50 else video['title']
                    print(f"  [{i}/{len(videos_to_create)}] Creating: {title_preview}")
                    
                    # Create content entry
                    Content.objects.create(
                        source=source,
                        external_id=video['video_id'],
                        title=video['title'],
                        link=video['link'],
                        content_type='video',
                        date=video['upload_date'],
                        content='',  # Empty - will be filled later when processing
                        processed=False,
                    )
                    created_count += 1
            
            print(f"\n✓ Successfully created {created_count} content entry/entries")
            print(f"{'='*60}\n")
            
            # Log the activity
            metadata = {
                'videos_fetched': created_count,
                'videos_skipped': already_exist_count,
                'total_found': total_found
            }
            if source.filter_videos:
                metadata['videos_filtered'] = filtered_count
            
            log_activity(
                'content_created',
                f'Fetched {created_count} videos from YouTube channel "{source.name}"',
                user=request.user,
                source=source,
                metadata=metadata
            )
            
            if created_count > 0:
                message = f'Successfully fetched {created_count} video(s) from "{source.name}". '
                if already_exist_count > 0:
                    message += f'{already_exist_count} video(s) already exist. '
                if source.filter_videos and filtered_count > 0:
                    message += f'{filtered_count} video(s) were filtered out (not relevant to China).'
                messages.success(request, message)
            else:
                messages.info(
                    request,
                    f'No new videos to add from "{source.name}".'
                )
            
        except ImportError as e:
            messages.error(request, f'YouTube API library not available: {str(e)}')
        except ValueError as e:
            messages.error(request, f'Error: {str(e)}')
        except Exception as e:
            messages.error(request, f'Error fetching videos: {str(e)}')
        
        return redirect('sources:source_edit', pk=source.pk)
    
    if request.method == 'POST':
        form = SourceForm(request.POST, instance=source)
        if form.is_valid():
            source = form.save()
            log_activity(
                'source_updated',
                f'Source "{source.name}" ({source.get_source_type_display()}) was updated',
                user=request.user,
                source=source
            )
            messages.success(request, f'Source "{source.name}" updated successfully!')
            return redirect('sources:source_list')
    else:
        form = SourceForm(instance=source)
    
    context = {
        'form': form,
        'source': source,
        'action': 'Edit',
    }
    return render(request, 'sources/source_form.html', context)


@login_required
@require_http_methods(["POST"])
def source_delete(request, pk):
    """Delete a source"""
    source = get_object_or_404(Source, pk=pk)
    source_name = source.name
    source_type = source.get_source_type_display()
    log_activity(
        'source_deleted',
        f'Source "{source_name}" ({source_type}) was deleted',
        user=request.user,
        source=source
    )
    source.delete()
    messages.success(request, f'Source "{source_name}" deleted successfully!')
    return redirect('sources:source_list')


# Content Views
@login_required
def content_list(request):
    """Display list of all contents"""
    contents = Content.objects.select_related('source').prefetch_related('tags').all()
    
    # Filtering
    source_filter = request.GET.get('source', '').strip()
    content_type_filter = request.GET.get('content_type', '').strip()
    has_content_filter = request.GET.get('has_content', '').strip()
    processed_filter = request.GET.get('processed', '').strip()
    tag_filter = request.GET.get('tag', '').strip()
    search_query = request.GET.get('search', '').strip()
    
    if source_filter:
        contents = contents.filter(source_id=source_filter)
    if content_type_filter:
        contents = contents.filter(content_type=content_type_filter)
    if has_content_filter:
        contents = contents.filter(has_content=(has_content_filter == 'true'))
    if processed_filter:
        contents = contents.filter(processed=(processed_filter == 'true'))
    if tag_filter:
        contents = contents.filter(tags__id=tag_filter).distinct()
    if search_query:
        contents = contents.filter(
            title__icontains=search_query
        ) | contents.filter(
            external_id__icontains=search_query
        )
    
    # Pagination
    paginator = Paginator(contents, 25)  # Show 25 contents per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'contents': page_obj,
        'sources': Source.objects.all(),
        'tags': Tag.objects.all().order_by('name'),
        'source_filter': source_filter,
        'content_type_filter': content_type_filter,
        'has_content_filter': has_content_filter,
        'processed_filter': processed_filter,
        'tag_filter': tag_filter,
        'search_query': search_query,
    }
    return render(request, 'sources/content_list.html', context)


@login_required
def content_add(request):
    """Add a new content"""
    if request.method == 'POST':
        form = ContentForm(request.POST)
        if form.is_valid():
            content = form.save()
            
            # Log content creation BEFORE processing
            log_activity(
                'content_created',
                f'Content "{content.title}" ({content.get_content_type_display()}) was created',
                user=request.user,
                content=content,
                source=content.source
            )
            
            # Process content: extract, translate, tag, embed
            try:
                processing_service = ContentProcessingService()
                processing_results = processing_service.process_content(
                    content,
                    extract=True,
                    translate=True,
                    tag=True,
                    embed=True
                )
                
                # Refresh content to get latest state (especially has_content)
                content.refresh_from_db()
                
                # Log processing results
                processing_summary = []
                if processing_results.get('extracted'):
                    processing_summary.append('extracted')
                if processing_results.get('translated'):
                    processing_summary.append('translated')
                if processing_results.get('tagged'):
                    processing_summary.append('tagged')
                if processing_results.get('embedded'):
                    processing_summary.append('embedded')
                
                if processing_summary:
                    messages.success(
                        request, 
                        f'Content "{content.title}" added and processed: {", ".join(processing_summary)}'
                    )
                else:
                    messages.success(request, f'Content "{content.title}" added successfully!')
            except Exception as e:
                # Log error but don't fail the request
                import traceback
                error_trace = traceback.format_exc()
                print(f"Error in content processing: {str(e)}")
                print(error_trace)
                messages.warning(
                    request, 
                    f'Content "{content.title}" added, but processing encountered an error: {str(e)}'
                )
            
            return redirect('sources:content_list')
    else:
        form = ContentForm()
    
    # Get all sources grouped by type for JavaScript filtering
    sources_by_type = {}
    for source in Source.objects.all().order_by('name'):
        source_type = source.source_type
        if source_type not in sources_by_type:
            sources_by_type[source_type] = []
        sources_by_type[source_type].append({
            'id': source.id,
            'name': source.name,
        })
    
    context = {
        'form': form,
        'action': 'Add',
        'sources_by_type_json': json.dumps(sources_by_type),
    }
    return render(request, 'sources/content_form.html', context)


@login_required
def content_edit(request, pk):
    """Edit an existing content"""
    content = get_object_or_404(Content, pk=pk)
    
    # Handle fetch content action
    if request.method == 'POST' and 'fetch_content' in request.POST:
        if content.content_type == 'blog_post' and content.link:
            try:
                processing_service = ContentProcessingService()
                # Force re-extraction even if content already exists
                extracted = processing_service.extract_content(content, force=True)
                if extracted:
                    messages.success(request, f'Content fetched successfully from {content.link}')
                    # Refresh content from DB to get updated content
                    content.refresh_from_db()
                else:
                    messages.warning(request, f'Could not extract content from {content.link}. The page might not be accessible or the content structure is not recognized.')
            except Exception as e:
                messages.error(request, f'Error fetching content: {str(e)}')
        else:
            messages.warning(request, 'Content can only be fetched for blog posts with a valid link.')
        # Redirect back to edit page to show updated content
        return redirect('sources:content_edit', pk=content.pk)
    
    # Handle add tags action
    if request.method == 'POST' and 'add_tags' in request.POST:
        if not content.content or not content.content.strip():
            messages.warning(request, 'Content must have text before tags can be added.')
        else:
            try:
                processing_service = ContentProcessingService()
                # Force re-tagging even if tags already exist
                content.tags.clear()  # Clear existing tags to force re-tagging
                tagged = processing_service.add_tags(content)
                if tagged:
                    messages.success(request, f'Tags added successfully to "{content.title}"')
                    content.refresh_from_db()
                else:
                    messages.warning(request, f'Could not add tags. Check console for details.')
            except Exception as e:
                import traceback
                error_trace = traceback.format_exc()
                print(f"Error adding tags: {str(e)}")
                print(error_trace)
                messages.error(request, f'Error adding tags: {str(e)}')
        # Redirect back to edit page
        return redirect('sources:content_edit', pk=content.pk)
    
    # Handle generate embeddings action
    if request.method == 'POST' and 'generate_embeddings' in request.POST:
        if not content.content or not content.content.strip():
            messages.warning(request, 'Content must have text before embeddings can be generated.')
        elif not content.tags.exists():
            messages.warning(request, 'Content must have tags before embeddings can be generated.')
        else:
            try:
                processing_service = ContentProcessingService()
                # Force re-embedding even if embeddings already exist
                content.chunks.all().delete()  # Clear existing chunks to force re-embedding
                embedded = processing_service.generate_embeddings(content)
                if embedded:
                    messages.success(request, f'Embeddings generated successfully for "{content.title}"')
                    content.refresh_from_db()
                else:
                    messages.warning(request, f'Could not generate embeddings. Check console for details.')
            except Exception as e:
                import traceback
                error_trace = traceback.format_exc()
                print(f"Error generating embeddings: {str(e)}")
                print(error_trace)
                messages.error(request, f'Error generating embeddings: {str(e)}')
        # Redirect back to edit page
        return redirect('sources:content_edit', pk=content.pk)
    
    if request.method == 'POST':
        form = ContentForm(request.POST, instance=content)
        if form.is_valid():
            content = form.save()
            log_activity(
                'content_updated',
                f'Content "{content.title}" ({content.get_content_type_display()}) was updated',
                user=request.user,
                content=content,
                source=content.source
            )
            messages.success(request, f'Content "{content.title}" updated successfully!')
            return redirect('sources:content_list')
    else:
        form = ContentForm(instance=content)
    
    # Get all sources grouped by type for JavaScript filtering
    sources_by_type = {}
    for source in Source.objects.all().order_by('name'):
        source_type = source.source_type
        if source_type not in sources_by_type:
            sources_by_type[source_type] = []
        sources_by_type[source_type].append({
            'id': source.id,
            'name': source.name,
        })
    
    context = {
        'form': form,
        'content': content,
        'action': 'Edit',
        'sources_by_type_json': json.dumps(sources_by_type),
    }
    return render(request, 'sources/content_form.html', context)


@login_required
def content_detail(request, pk):
    """View content details"""
    content = get_object_or_404(Content.objects.select_related('source').prefetch_related('tags'), pk=pk)
    context = {
        'content': content,
    }
    return render(request, 'sources/content_detail.html', context)


@login_required
@require_http_methods(["POST"])
def content_delete(request, pk):
    """Delete a content"""
    content = get_object_or_404(Content, pk=pk)
    content_title = content.title
    content_type = content.get_content_type_display()
    source = content.source
    log_activity(
        'content_deleted',
        f'Content "{content_title}" ({content_type}) was deleted',
        user=request.user,
        content=content,
        source=source
    )
    content.delete()
    messages.success(request, f'Content "{content_title}" deleted successfully!')
    return redirect('sources:content_list')


# Agent Views
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
def agent_models_api(request):
    """API endpoint to fetch available Ollama models"""
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


@login_required
def agent_chat_api(request):
    """API endpoint for chat messages"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    try:
        data = json.loads(request.body)
        question = data.get('question', '').strip()
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
                    'video_filter_model': form.cleaned_data.get('default_video_filter_model'),
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
