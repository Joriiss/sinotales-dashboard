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
from ..models import Source, Content, Tag
from ..forms import ContentForm
from ..utils import log_activity
from ..content_processing_service import ContentProcessingService

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
    
    # Optimize: Only load sources and tags once, they're used for filters
    # These could be cached, but for now just ensure they're efficient
    sources = Source.objects.all().only('id', 'name', 'source_type')
    tags = Tag.objects.all().only('id', 'name').order_by('name')
    
    context = {
        'page_obj': page_obj,
        'contents': page_obj,
        'sources': sources,
        'tags': tags,
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
            
            # Update last_collected timestamp on source
            from django.utils import timezone
            content.source.last_collected = timezone.now()
            content.source.save(update_fields=['last_collected'])
            
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
                # Use proxy support for content extraction (includes curl fallback and UTF-8 encoding)
                processing_service = ContentProcessingService(use_proxy=True)
                # Force re-extraction even if content already exists
                extracted = processing_service.extract_content(content, force=True)
                if extracted:
                    # Refresh content from DB to get updated content
                    content.refresh_from_db()
                    content_length = len(content.content) if content.content else 0
                    messages.success(request, f'Content fetched successfully from {content.link} ({content_length:,} characters)')
                    # Log successful content fetch
                    log_activity(
                        'content_fetched',
                        f'Content fetched from "{content.link}" for "{content.title}"',
                        user=request.user,
                        content=content,
                        source=content.source
                    )
                else:
                    # Get more detailed error message if available
                    error_msg = 'Could not extract content. The page might not be accessible, blocked by Cloudflare, or the content structure is not recognized.'
                    messages.warning(request, f'Could not extract content from {content.link}. {error_msg}')
                    # Log failed content fetch
                    log_activity(
                        'content_fetched',
                        f'Failed to fetch content from "{content.link}" for "{content.title}"',
                        user=request.user,
                        content=content,
                        source=content.source,
                        metadata={'success': False, 'reason': 'Could not extract content'}
                    )
            except Exception as e:
                messages.error(request, f'Error fetching content: {str(e)}')
                # Log error
                log_activity(
                    'content_fetched',
                    f'Error fetching content from "{content.link}" for "{content.title}": {str(e)}',
                    user=request.user,
                    content=content,
                    source=content.source,
                    metadata={'success': False, 'error': str(e)}
                )
        else:
            messages.warning(request, 'Content can only be fetched for blog posts with a valid link.')
        # Redirect back to edit page to show updated content
        return redirect('sources:content_edit', pk=content.pk)
    
    # Handle get transcript action
    if request.method == 'POST' and 'get_transcript' in request.POST:
        if content.content_type == 'video' and (content.external_id or content.link):
            try:
                # Use proxy support like the API endpoint does (important for VPS/cloud IPs)
                processing_service = ContentProcessingService(use_proxy=True)
                # Force re-extraction even if content already exists
                # Pass user for activity logging (service method handles logging)
                extracted, language_code = processing_service.extract_transcript(content, force=True, user=request.user)
                if extracted:
                    # Refresh content from DB to get updated content
                    content.refresh_from_db()
                    
                    # Check if transcript is not in English and translate if needed
                    is_english = False
                    if language_code:
                        # Normalize language code for checking (handle variants like 'en', 'en-US', 'en-GB', etc.)
                        lang_base = language_code.split('-')[0].lower()
                        is_english = lang_base == 'en'
                    
                    if language_code and not is_english and language_code.lower() != 'auto':
                        # Transcript is not in English - translate it
                        print(f"  [TRANSLATE] Transcript is in {language_code}, translating to English...", flush=True)
                        translated = processing_service.translate_to_english(content, source_language=language_code)
                        if translated:
                            content.refresh_from_db()
                            messages.success(request, f'Transcript fetched and translated to English for video "{content.title}" (original: {language_code})')
                        else:
                            messages.warning(request, f'Transcript fetched but translation to English failed for video "{content.title}"')
                    elif is_english:
                        messages.success(request, f'Transcript fetched successfully for video "{content.title}" (already in English)')
                    else:
                        # Language is unknown or auto - no translation needed
                        messages.success(request, f'Transcript fetched successfully for video "{content.title}"')
                else:
                    messages.warning(request, f'Could not extract transcript. The video might not have transcripts available, or they may be disabled.')
            except Exception as e:
                messages.error(request, f'Error fetching transcript: {str(e)}')
                # Service method already logs errors, no need to log again here
        else:
            messages.warning(request, 'Transcript can only be fetched for videos with a valid video ID or link.')
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
    
    # Handle regular form save (when Save Content button is clicked)
    # Check if this is a regular form submission (not one of the special action buttons)
    if request.method == 'POST':
        is_special_action = any(key in request.POST for key in ['fetch_content', 'get_transcript', 'add_tags', 'generate_embeddings'])
        
        if not is_special_action:
            # This is a regular form save
            print(f"\n{'='*60}", flush=True)
            print(f"Processing regular form save for content {content.id}", flush=True)
            print(f"POST keys: {list(request.POST.keys())}", flush=True)
            print(f"{'='*60}\n", flush=True)
            
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
                # Form has errors - display them
                print(f"Form validation failed. Errors: {form.errors}", flush=True)
                error_messages = []
                for field, errors in form.errors.items():
                    for error in errors:
                        error_messages.append(f"{field}: {error}")
                if error_messages:
                    messages.error(request, f'Please correct the following errors: {"; ".join(error_messages)}')
        else:
            # This was a special action, form will be created below
            form = ContentForm(instance=content)
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
