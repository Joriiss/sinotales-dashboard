from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
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
from .models import Source, Content, Tag, ContentChunk, ActivityLog, Settings, PostIdea, ScheduledPostIdeaGeneration, BlogPost, BlogPostImage
import random
from .forms import SourceForm, ContentForm, SettingsForm
from .rag_service import RAGService
from .utils import log_activity, is_idea_too_similar_with_embeddings
from .content_processing_service import ContentProcessingService
from .embedding_service import EmbeddingService
from pgvector.django import CosineDistance
from datetime import datetime


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
    # Cache dashboard statistics for 10 minutes to improve performance
    cache_key = 'dashboard_stats'
    cached_stats = cache.get(cache_key)
    
    if cached_stats is None:
        # Basic counts - combine where possible
        total_sources = Source.objects.count()
        total_contents = Content.objects.count()
        active_sources = Source.objects.filter(is_active=True).count()
    
        # Content breakdown by type
        content_by_type = list(Content.objects.values('content_type').annotate(
        count=Count('id')
        ).order_by('-count'))
    
        # Source breakdown by type
        sources_by_type = list(Source.objects.values('source_type').annotate(
        count=Count('id')
        ).order_by('-count'))
    
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
        sources_by_language = list(Source.objects.values('language').annotate(
        count=Count('id')
        ).order_by('-count'))
    
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
        top_tags = list(Tag.objects.annotate(
        content_count=Count('contents')
        ).filter(content_count__gt=0).order_by('-content_count')[:10])
    
        # Cache the expensive aggregations
        cached_stats = {
        'total_sources': total_sources,
        'total_contents': total_contents,
        'active_sources': active_sources,
        'content_by_type': content_by_type,
        'sources_by_type': sources_by_type,
            'contents_with_text': contents_with_text,
            'contents_processed': contents_processed,
        'total_words': total_words,
        'total_mb': total_mb,
            'sources_by_language': sources_by_language,
        'total_tags': total_tags,
        'contents_with_tags': contents_with_tags,
        'total_chunks': total_chunks,
        'chunks_with_embeddings': chunks_with_embeddings,
        'contents_with_embeddings': contents_with_embeddings,
        'embedding_percentage': embedding_percentage,
        'top_tags': top_tags,
        }
        cache.set(cache_key, cached_stats, 600)  # Cache for 10 minutes
    
    # Recent activity - don't cache, should be fresh
    recent_contents = Content.objects.select_related('source').prefetch_related('tags').order_by('-created_at')[:10]
    recent_sources = Source.objects.order_by('-created_at')[:5]
    
    # Content by source (top sources) - don't cache, should be fresh
    top_sources = Source.objects.annotate(
        content_count=Count('contents')
    ).filter(content_count__gt=0).order_by('-content_count')[:10]
    
    context = {
        **cached_stats,  # Unpack cached statistics
        'recent_contents': recent_contents,
        'recent_sources': recent_sources,
        'top_sources': top_sources,
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
    
    # Apply sorting (default: alphabetical by name)
    sort_by = request.GET.get('sort', 'name_asc').strip()
    if sort_by == 'name_asc':
        sources = sources.order_by('name')
    elif sort_by == 'name_desc':
        sources = sources.order_by('-name')
    elif sort_by == 'type':
        sources = sources.order_by('source_type', 'name')
    elif sort_by == 'language':
        sources = sources.order_by('language', 'name')
    elif sort_by == 'last_collected_desc':
        sources = sources.order_by('-last_collected', 'name')
    elif sort_by == 'last_collected_asc':
        sources = sources.order_by('last_collected', 'name')
    elif sort_by == 'contents_desc':
        sources = sources.order_by('-total_contents', 'name')
    elif sort_by == 'contents_asc':
        sources = sources.order_by('total_contents', 'name')
    else:
        # Default: alphabetical by name
        sources = sources.order_by('name')
    
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
        'sort_by': sort_by,
    }
    return render(request, 'sources/source_list.html', context)


@login_required
def source_add(request):
    """Add a new source"""
    if request.method == 'POST':
        form = SourceForm(request.POST, request.FILES)
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
        
        # Check if test mode is enabled (limit to 5 videos) - capture before background thread
        test_mode = request.POST.get('test_mode') == '1'
        
        # Run video import in background thread to avoid timeout
        import threading
        from django.db import connection
        
        def import_videos_background():
            """Import videos in background thread"""
            # Close the database connection from the main thread
            connection.close()
            
            try:
                from .youtube_service import get_channel_videos
                from django.db import transaction
                from django.utils import timezone
                
                # Get fresh source object in this thread
                source_pk = source.pk
                source_refresh = Source.objects.get(pk=source_pk)
                
                # Fetch videos from YouTube (without filtering first - we'll filter only new videos)
                print(f"\n{'='*60}", flush=True)
                print(f"Get Videos (Background): {source_refresh.name} (Channel: {source_refresh.channel_id})", flush=True)
                print(f"Filter China: {source_refresh.filter_videos}", flush=True)
                if test_mode:
                    print(f"TEST MODE: Enabled (5 videos only)", flush=True)
                print(f"{'='*60}\n", flush=True)
                
                # Get all videos without filtering (filtering will happen per-video for new ones only)
                videos = get_channel_videos(
                    channel_id=source_refresh.channel_id,
                    include_shorts=source_refresh.include_shorts,
                    filter_china=False  # Don't filter here - we'll filter only new videos
                )
                
                if not videos:
                    print(f"  [BACKGROUND] No videos found for channel", flush=True)
                    return
                
                # Limit to 5 videos if test mode is enabled
                if test_mode:
                    videos = videos[:5]
                    print(f"  [BACKGROUND] TEST MODE: Limiting to 5 videos", flush=True)
                
                print(f"  [BACKGROUND] Found {len(videos)} videos from YouTube, checking which are new...", flush=True)
                
                # Get existing video IDs to skip filtering for videos already in DB
                existing_video_ids = set(
                    Content.objects.filter(source=source_refresh)
                    .values_list('external_id', flat=True)
                )
                print(f"  [BACKGROUND] {len(existing_video_ids)} videos already exist in database", flush=True)
                
                # Import filtering function
                from .youtube_service import is_video_relevant_to_china
                
                # Create Content entries for each video
                created_count = 0
                skipped_count = 0
                filtered_count = 0
                created_content_data = []  # Store (content_id, transcript_text) tuples for processing
                
                with transaction.atomic():
                    for i, video in enumerate(videos, 1):
                        video_id = video['video_id']
                        
                        # Check if content already exists (by external_id)
                        if video_id in existing_video_ids:
                            skipped_count += 1
                            if i % 10 == 0:
                                print(f"  [BACKGROUND] Processed {i}/{len(videos)} videos (created: {created_count}, skipped: {skipped_count}, filtered: {filtered_count})...", flush=True)
                            continue
                        
                        # Filter China-related videos if enabled (only for new videos)
                        transcript_text = None
                        if source_refresh.filter_videos:
                            is_relevant, transcript_text = is_video_relevant_to_china(
                                video['title'], 
                                video.get('description', ''), 
                                video.get('tags', []), 
                                video_id
                            )
                            if not is_relevant:
                                filtered_count += 1
                                print(f"  [BACKGROUND] Filtered out (not China-related): {video['title'][:60]}...", flush=True)
                                if i % 10 == 0:
                                    print(f"  [BACKGROUND] Processed {i}/{len(videos)} videos (created: {created_count}, skipped: {skipped_count}, filtered: {filtered_count})...", flush=True)
                                continue
                        
                        # Create content entry
                        content = Content.objects.create(
                            source=source_refresh,
                            external_id=video_id,
                            title=video['title'],
                            link=video['link'],
                            content_type='video',
                            date=video['upload_date'],
                            content='',  # Empty - will be filled later when processing
                            processed=False,
                        )
                        created_count += 1
                        created_content_data.append((content.id, transcript_text))
                        
                        if i % 10 == 0:
                            print(f"  [BACKGROUND] Processed {i}/{len(videos)} videos (created: {created_count}, skipped: {skipped_count}, filtered: {filtered_count})...", flush=True)
                
                # Update last_collected timestamp if any videos were found (even if all were skipped)
                if videos:
                    source_refresh.last_collected = timezone.now()
                    source_refresh.save(update_fields=['last_collected'])
                
                # Extract transcripts, tag, and embed newly created videos
                if created_content_data:
                    print(f"\n  [BACKGROUND] Processing {len(created_content_data)} videos (transcript, tag, embed)...", flush=True)
                    from .content_processing_service import ContentProcessingService
                    
                    # Create processing service with proxy support
                    processing_service = ContentProcessingService(use_proxy=True)
                    
                    transcripts_extracted = 0
                    transcripts_failed = 0
                    tagged_count = 0
                    embedded_count = 0
                    
                    for idx, (content_id, transcript_text) in enumerate(created_content_data, 1):
                        try:
                            # Get fresh content object
                            content = Content.objects.get(pk=content_id)
                            
                            # Extract transcript (using pre-fetched if available)
                            extracted, _ = processing_service.extract_transcript(content, force=False, user=None, transcript_text=transcript_text)
                            if extracted:
                                transcripts_extracted += 1
                                
                                # Refresh to get the saved transcript
                                content.refresh_from_db()
                                
                                # Tag and embed if we have content
                                if content.content and content.content.strip():
                                    # Tag the content
                                    if processing_service.add_tags(content):
                                        tagged_count += 1
                                        content.refresh_from_db()
                                    
                                    # Embed the content (only if it has tags)
                                    if content.tags.exists():
                                        if processing_service.generate_embeddings(content):
                                            embedded_count += 1
                                            content.processed = True
                                            content.save(update_fields=['processed'])
                            else:
                                transcripts_failed += 1
                            
                            if idx % 10 == 0:
                                print(f"  [BACKGROUND] Processed {idx}/{len(created_content_data)} videos (transcripts: {transcripts_extracted}, tagged: {tagged_count}, embedded: {embedded_count}, failed: {transcripts_failed})...", flush=True)
                        except Exception as e:
                            transcripts_failed += 1
                            print(f"  [BACKGROUND] Error processing content {content_id}: {str(e)}", flush=True)
                    
                    print(f"  [BACKGROUND] Processing completed: {transcripts_extracted} transcripts, {tagged_count} tagged, {embedded_count} embedded, {transcripts_failed} failed", flush=True)
                
                # Log the activity
                log_activity(
                    'content_created',
                    f'Fetched {created_count} videos from YouTube channel "{source_refresh.name}"',
                    user=None,  # Background task, no user context
                    source=source_refresh,
                    metadata={
                        'videos_fetched': created_count,
                        'videos_skipped': skipped_count,
                        'videos_filtered': filtered_count,
                        'total_found': len(videos)
                    }
                )
                
                print(f"\n{'='*60}", flush=True)
                print(f"  [BACKGROUND] ✓ Import completed: {created_count} created, {skipped_count} skipped, {filtered_count} filtered out", flush=True)
                print(f"{'='*60}\n", flush=True)
                
            except ImportError as e:
                print(f"  [BACKGROUND] ERROR: YouTube API library not available: {str(e)}", flush=True)
            except ValueError as e:
                print(f"  [BACKGROUND] ERROR: {str(e)}", flush=True)
            except Exception as e:
                import traceback
                print(f"  [BACKGROUND] ERROR: {str(e)}", flush=True)
                print(f"  [BACKGROUND] Traceback: {traceback.format_exc()}", flush=True)
            finally:
                # Ensure database connection is closed
                connection.close()
        
        # Start background thread
        thread = threading.Thread(target=import_videos_background, daemon=True)
        thread.start()
        
        messages.info(
                    request,
            f'Video import started in the background for "{source.name}". '
            f'This may take several minutes. Check the logs for progress.'
        )
        
        return redirect('sources:source_edit', pk=source.pk)
    
    # Handle import_ebook action for ebook sources
    if request.method == 'POST' and 'import_ebook' in request.POST:
        if source.source_type != 'ebook':
            messages.error(request, 'Import Ebook is only available for ebook sources.')
            return redirect('sources:source_edit', pk=source.pk)
        
        if not source.ebook_file:
            messages.error(request, 'Ebook file is required to import ebook.')
            return redirect('sources:source_edit', pk=source.pk)
        
        if not source.publication_date:
            messages.error(request, 'Publication date is required to import ebook.')
            return redirect('sources:source_edit', pk=source.pk)
        
        # Run ebook import in background thread to avoid timeout
        import threading
        from django.db import connection
        
        def import_ebook_background():
            """Import ebook chunks in background thread"""
            # Close the database connection from the main thread
            connection.close()
            
            try:
                from django.db import transaction
                from django.utils import timezone
                import re
                
                # Get fresh source object in this thread
                source_pk = source.pk
                source_refresh = Source.objects.get(pk=source_pk)
                
                print(f"\n{'='*60}", flush=True)
                print(f"Import Ebook (Background): {source_refresh.name}", flush=True)
                print(f"Publication Date: {source_refresh.publication_date}", flush=True)
                print(f"Language: {source_refresh.language}", flush=True)
                print(f"{'='*60}\n", flush=True)
                
                # Helper function to find sentence boundary (from split_ebooks.py)
                def find_sentence_boundary(text, start_pos, target_length, max_search=500):
                    target_end = start_pos + target_length
                    sentence_pattern = r'[.!?][\s\n]|\.$'
                    search_start = max(start_pos, target_end - max_search)
                    search_end = min(len(text), target_end + max_search)
                    search_text = text[search_start:search_end]
                    matches = list(re.finditer(sentence_pattern, search_text))
                    
                    if matches:
                        best_match = None
                        best_distance = float('inf')
                        for match in matches:
                            match_pos = search_start + match.end()
                            distance = abs(match_pos - target_end)
                            if distance < best_distance and match_pos > start_pos:
                                best_distance = distance
                                best_match = match_pos
                        if best_match:
                            return best_match
                    
                    # Fallback to word boundary
                    return find_word_boundary(text, start_pos, target_length, max_search)
                
                def find_word_boundary(text, start_pos, target_length, max_search=200):
                    target_end = start_pos + target_length
                    search_start = max(start_pos, target_end - max_search)
                    search_end = min(len(text), target_end + max_search)
                    search_text = text[search_start:search_end]
                    word_pattern = r'\s+'
                    matches = list(re.finditer(word_pattern, search_text))
                    
                    if matches:
                        best_match = None
                        best_distance = float('inf')
                        for match in matches:
                            match_pos = search_start + match.start()
                            distance = abs(match_pos - target_end)
                            if distance < best_distance and match_pos > start_pos:
                                best_distance = distance
                                best_match = match_pos
                        if best_match:
                            return best_match
                    
                    return target_end
                
                def split_text_into_chunks(text, chunk_size=5000):
                    """Split text into chunks of approximately chunk_size characters"""
                    chunks = []
                    current_pos = 0
                    text_length = len(text)
                    
                    while current_pos < text_length:
                        remaining = text_length - current_pos
                        if remaining <= chunk_size:
                            chunks.append(text[current_pos:])
                            break
                        
                        split_pos = find_sentence_boundary(text, current_pos, chunk_size)
                        chunk = text[current_pos:split_pos].strip()
                        
                        if chunk:
                            chunks.append(chunk)
                        
                        current_pos = split_pos
                        while current_pos < text_length and text[current_pos].isspace():
                            current_pos += 1
                    
                    return chunks
                
                # Read ebook file
                print(f"  [BACKGROUND] Reading ebook file...", flush=True)
                try:
                    ebook_file = source_refresh.ebook_file
                    if not ebook_file:
                        print(f"  [BACKGROUND] ✗ No ebook file found", flush=True)
                        return
                    
                    # Read file as binary and decode with UTF-8 (handles encoding issues)
                    ebook_file.open('rb')
                    try:
                        raw_bytes = ebook_file.read()
                        # Decode with UTF-8, replacing invalid characters if any
                        text = raw_bytes.decode('utf-8', errors='replace')
                    finally:
                        ebook_file.close()
                    
                    if not text or not text.strip():
                        print(f"  [BACKGROUND] ✗ Ebook file is empty", flush=True)
                        return
                    
                    print(f"  [BACKGROUND] ✓ Read {len(text):,} characters from ebook", flush=True)
                except Exception as e:
                    print(f"  [BACKGROUND] ✗ Error reading ebook file: {str(e)}", flush=True)
                    import traceback
                    print(f"  [BACKGROUND] Traceback: {traceback.format_exc()}", flush=True)
                    return
                
                # Split into chunks
                print(f"  [BACKGROUND] Splitting ebook into ~5000 character chunks...", flush=True)
                chunks = split_text_into_chunks(text, chunk_size=5000)
                print(f"  [BACKGROUND] ✓ Created {len(chunks)} chunks", flush=True)
                
                if not chunks:
                    print(f"  [BACKGROUND] ✗ No chunks created", flush=True)
                    return
                
                # Create Content objects for each chunk
                print(f"  [BACKGROUND] Creating content entries...", flush=True)
                created_count = 0
                skipped_count = 0
                created_content_ids = []
                
                with transaction.atomic():
                    for i, chunk in enumerate(chunks, 1):
                        # Generate title for chunk
                        chunk_title = f"{source_refresh.name} - Part {i}/{len(chunks)}"
                        
                        # Generate external_id (slugify the source name and add part number)
                        from django.utils.text import slugify
                        source_slug = slugify(source_refresh.name)
                        if not source_slug:
                            source_slug = f"ebook-{source_refresh.pk}"
                        external_id = f"{source_slug}-part-{i:03d}"
                        
                        # Ensure uniqueness by checking if it exists
                        base_external_id = external_id
                        counter = 0
                        while Content.objects.filter(
                            source=source_refresh,
                            external_id=external_id
                        ).exists():
                            counter += 1
                            external_id = f"{base_external_id}-{counter}"
                        
                        # Check if content already exists (with the final external_id)
                        existing = Content.objects.filter(
                            source=source_refresh,
                            external_id=external_id
                        ).first()
                        
                        if existing:
                            skipped_count += 1
                            if i <= 3:
                                print(f"  [BACKGROUND] Skipped chunk {i} (already exists): {chunk_title}", flush=True)
                            continue
                        
                        # Create content entry
                        content = Content.objects.create(
                            source=source_refresh,
                            external_id=external_id,
                            title=chunk_title,
                            link=None,  # Ebooks don't have links
                            content_type='ebook',
                            date=source_refresh.publication_date,
                            content=chunk,
                            processed=False,
                            has_content=True,
                        )
                        created_count += 1
                        created_content_ids.append(content.id)
                        
                        if i % 10 == 0:
                            print(f"  [BACKGROUND] Created {i}/{len(chunks)} chunks...", flush=True)
                
                # Update last_collected timestamp
                if chunks:
                    source_refresh.last_collected = timezone.now()
                    source_refresh.save(update_fields=['last_collected'])
                
                print(f"  [BACKGROUND] ✓ Created {created_count} content entries, skipped {skipped_count}", flush=True)
                
                # Process each created chunk: translate, tag, embed
                translated_count = 0
                translated_failed = 0
                tagged_count = 0
                tagged_failed = 0
                embedded_count = 0
                embedded_failed = 0
                processed_count = 0
                
                if created_content_ids:
                    print(f"\n  [BACKGROUND] Processing {len(created_content_ids)} chunks (translate, tag, embed)...", flush=True)
                    from .content_processing_service import ContentProcessingService
                    
                    processing_service = ContentProcessingService()
                    
                    for idx, content_id in enumerate(created_content_ids, 1):
                        try:
                            # Get fresh content object
                            content = Content.objects.get(pk=content_id)
                            
                            print(f"  [BACKGROUND] [{idx}/{len(created_content_ids)}] Processing: {content.title[:60]}...", flush=True)
                            
                            # Step 1: Translate (if source language is not English)
                            if content.source.language != 'en' and content.content and content.content.strip():
                                if processing_service.translate_content(content):
                                    translated_count += 1
                                    content.refresh_from_db()
                                else:
                                    translated_failed += 1
                            
                            # Step 2: Tag (only if we have content)
                            if content.content and content.content.strip():
                                if processing_service.add_tags(content):
                                    tagged_count += 1
                                    content.refresh_from_db()
                                else:
                                    tagged_failed += 1
                                
                                # Step 3: Embed (only if it has tags)
                                if content.tags.exists():
                                    if processing_service.generate_embeddings(content):
                                        embedded_count += 1
                                        content.processed = True
                                        content.save(update_fields=['processed'])
                                        processed_count += 1
                                    else:
                                        embedded_failed += 1
                                else:
                                    print(f"    [BACKGROUND] Skipping embedding (no tags)", flush=True)
                            
                        except Exception as e:
                            print(f"    [BACKGROUND] ✗ Error processing chunk {idx}: {str(e)}", flush=True)
                            import traceback
                            print(f"    [BACKGROUND] Traceback: {traceback.format_exc()[:300]}", flush=True)
                    
                    print(f"\n  [BACKGROUND] Processing completed:", flush=True)
                    print(f"    - Translated: {translated_count} succeeded, {translated_failed} failed", flush=True)
                    print(f"    - Tagged: {tagged_count} succeeded, {tagged_failed} failed", flush=True)
                    print(f"    - Embedded: {embedded_count} succeeded, {embedded_failed} failed", flush=True)
                    print(f"    - Fully processed: {processed_count}", flush=True)
                
                # Log the activity
                log_activity(
                    'content_created',
                    f'Imported ebook "{source_refresh.name}" - created {created_count} chunks',
                    user=None,
                    source=source_refresh,
                    metadata={
                        'chunks_created': created_count,
                        'chunks_skipped': skipped_count,
                        'total_chunks': len(chunks),
                        'translated': translated_count,
                        'tagged': tagged_count,
                        'embedded': embedded_count,
                        'fully_processed': processed_count
                    }
                )
                
                print(f"\n{'='*60}", flush=True)
                print(f"  [BACKGROUND] ✓ Import completed: {created_count} created, {skipped_count} skipped", flush=True)
                print(f"  [BACKGROUND] ✓ Processing completed: {processed_count} fully processed", flush=True)
                print(f"{'='*60}\n", flush=True)
                
            except Exception as e:
                import traceback
                print(f"  [BACKGROUND] ERROR: {str(e)}", flush=True)
                print(f"  [BACKGROUND] Traceback: {traceback.format_exc()}", flush=True)
            finally:
                # Ensure database connection is closed
                connection.close()
        
        # Start background thread
        thread = threading.Thread(target=import_ebook_background, daemon=True)
        thread.start()
        
        messages.info(
            request,
            f'Ebook import started in the background for "{source.name}". '
            f'This may take several minutes. Check the logs for progress.'
        )
        
        return redirect('sources:source_edit', pk=source.pk)
    
    # Handle get_posts action for blog sources
    if request.method == 'POST' and 'get_posts' in request.POST:
        if source.source_type != 'blog':
            messages.error(request, 'Get Posts is only available for blog sources.')
            return redirect('sources:source_edit', pk=source.pk)
        
        if not source.sitemap:
            messages.error(request, 'Sitemap URL is required to get posts.')
            return redirect('sources:source_edit', pk=source.pk)
        
        # Check for test mode
        test_mode = request.POST.get('test_mode') == '1'
        
        # Run post import in background thread to avoid timeout
        import threading
        from django.db import connection
        from urllib.parse import urlparse
        
        def has_language_code(url):
            """
            Check if a URL contains a language code in the path (e.g., /es/, /pt/, /ja/).
            Returns True if the URL contains a language code, False otherwise.
            English URLs typically don't have a language code.
            """
            parsed = urlparse(url)
            path = parsed.path.lower()
            
            # Common language codes (ISO 639-1 two-letter codes and variants)
            # These appear in URLs like /es/page, /pt/page, /ja/page, etc.
            language_codes = [
                '/es/', '/pt/', '/ja/', '/ko/', '/de/', '/fr/', '/it/', '/ru/', '/zh/', '/zh_cn/', '/zh_tw/',
                '/ar/', '/hi/', '/nl/', '/sv/', '/pl/', '/tr/', '/vi/', '/th/', '/id/',
                '/cs/', '/hu/', '/ro/', '/fi/', '/da/', '/no/', '/he/', '/uk/', '/el/',
                '/bg/', '/hr/', '/sk/', '/sl/', '/et/', '/lv/', '/lt/', '/mt/', '/ga/',
                '/cy/', '/is/', '/mk/', '/sq/', '/sr/', '/bs/', '/ca/', '/eu/', '/gl/',
                '/bn/', '/ur/', '/mr/', '/te/', '/ta/', '/jv/', '/gu/', '/ms/', '/ml/', '/kn/', '/pa/', '/ne/',
                # Also check for language codes at the start of path (without leading slash)
                'es/', 'pt/', 'ja/', 'ko/', 'de/', 'fr/', 'it/', 'ru/', 'zh/', 'zh_cn/', 'zh_tw/',
                'ar/', 'hi/', 'nl/', 'sv/', 'pl/', 'tr/', 'vi/', 'th/', 'id/',
                'bn/', 'ur/', 'mr/', 'te/', 'ta/', 'jv/', 'gu/', 'ms/', 'ml/', 'kn/', 'pa/', 'ne/',
            ]
            
            # Check if any language code appears in the path
            for code in language_codes:
                if code in path:
                    return True
            
            return False

        def is_china_related(url):
            """
            Check if a URL is related to China based on keywords in the URL.
            Excludes Chinatowns and Chinese-related content in other countries.
            Based on the logic from get_posts_list.py
            
            Args:
                url: URL to check
                
            Returns:
                True if URL contains China-related keywords, False otherwise
            """
            url_lower = url.lower()
            
            # Exclude URLs that are clearly about other countries
            # Check for country codes in the path (common in Lonely Planet URLs)
            exclude_countries = [
                '/usa/', '/united-states/', '/america/', '/american/',
                '/australia/', '/canada/', '/uk/', '/united-kingdom/', '/britain/',
                '/france/', '/germany/', '/italy/', '/spain/', '/japan/', '/korea/',
                '/thailand/', '/vietnam/', '/singapore/', '/malaysia/', '/indonesia/',
                '/philippines/', '/india/', '/brazil/', '/mexico/', '/argentina/',
                '/new-zealand/', '/south-africa/', '/egypt/', '/turkey/', '/greece/'
            ]
            
            # If URL contains a non-China country code, it's likely not about China
            for country in exclude_countries:
                if country in url_lower:
                    # Exception: if it also explicitly mentions China in the path, it might be relevant
                    if '/china/' not in url_lower:
                        return False
            
            # Strong indicators that it's about China (these take priority)
            strong_indicators = [
                '/china/',  # Explicit China path (e.g., /china/beijing/)
                '/taiwan/', '/taipei/',  # Taiwan is part of China context
                '/hong-kong/', '/hongkong/', '/macau/', '/macao/',  # Special regions
            ]
            
            for indicator in strong_indicators:
                if indicator in url_lower:
                    return True
            
            # China-related keywords (but be careful with "chinatown" and "chinese" in other contexts)
            china_keywords = [
                # Major cities (only if not in excluded countries)
                'beijing', 'peking', 'shanghai', 'guangzhou', 'canton', 'shenzhen', 
                'chengdu', 'xian', 'xi\'an', 'hangzhou', 'nanjing', 'wuhan', 
                'chongqing', 'tianjin', 'suzhou', 'dalian', 'qingdao', 'xiamen',
                'foshan', 'dongguan', 'zhengzhou', 'changsha', 'kunming', 'fuzhou',
                'wuxi', 'hefei', 'nanning', 'shijiazhuang', 'haerbin', 'harbin',
                'jinan', 'taiyuan', 'changchun', 'nanchang', 'guiyang', 'lanzhou',
                # Provinces and regions
                'guangdong', 'jiangsu', 'shandong', 'zhejiang', 'henan', 'sichuan',
                'hubei', 'hunan', 'anhui', 'hebei', 'jiangxi', 'shanxi', 'liaoning',
                'fujian', 'yunnan', 'guangxi', 'heilongjiang', 'jilin', 'shaanxi',
                'guizhou', 'xinjiang', 'tibet', 'qinghai', 'gansu', 'inner-mongolia',
                'ningxia',
                # Regions and areas
                'yangtze', 'yellow-river', 'pearl-river', 'tibetan',
                'manchuria', 'dongbei', 'northeast-china',
                # Other related terms
                'great-wall', 'terracotta', 'forbidden-city', 'panda', 'silk-road'
            ]
            
            # Check for China keywords, but exclude "chinatown" and "chinese" unless in China context
            for keyword in china_keywords:
                if keyword in url_lower:
                    return True
            
            # Check for "china" or "chinese" but only if not in excluded country context
            # and not just "chinatown" in other countries
            if 'china' in url_lower or 'chinese' in url_lower:
                # If it's just "chinatown" without other strong China indicators, be cautious
                if 'chinatown' in url_lower:
                    # Only accept if there are other strong China indicators
                    if any(indicator in url_lower for indicator in strong_indicators):
                        return True
                    # Or if it's in a China city/province context
                    if any(city in url_lower for city in ['beijing', 'shanghai', 'guangzhou', 'chengdu', 'xian']):
                        return True
                    return False
                # For "china" or "chinese" (not chinatown), check if it's in excluded country
                # If we got here, we already checked exclude_countries above
                return True
            
            return False
        
        def import_posts_background():
            """Import blog posts in background thread"""
            # Close the database connection from the main thread
            connection.close()
            
            try:
                from django.db import transaction, IntegrityError
                from django.utils import timezone
                import requests
                import xml.etree.ElementTree as ET
                import time
                import subprocess
                import os
                from pathlib import Path
                
                # Try to import dateutil for date parsing
                try:
                    from dateutil import parser as date_parser
                except ImportError:
                    date_parser = None
                
                # Get fresh source object in this thread
                source_pk = source.pk
                source_refresh = Source.objects.get(pk=source_pk)
                
                print(f"\n{'='*60}", flush=True)
                print(f"Get Posts (Background): {source_refresh.name}", flush=True)
                print(f"Sitemap: {source_refresh.sitemap}", flush=True)
                print(f"Filter China: {source_refresh.filter_china}", flush=True)
                if test_mode:
                    print(f"TEST MODE: Enabled (10 posts only)", flush=True)
                print(f"{'='*60}\n", flush=True)
                
                # Helper function to load proxy configuration
                def load_proxy_config():
                    """Load Webshare proxy configuration and fetch proxy list"""
                    # Try to get from environment variables first
                    api_token = os.environ.get('WEBSHARE_API_TOKEN', '').strip()
                    proxy_username = os.environ.get('WEBSHARE_PROXY_USERNAME', '').strip()
                    proxy_password = os.environ.get('WEBSHARE_PROXY_PASSWORD', '').strip()
                    
                    # If not in environment, try to load from .env file
                    if not api_token and (not proxy_username or not proxy_password):
                        base_dir = Path(settings.BASE_DIR)
                        env_file = base_dir / '.env'
                        
                        if env_file.exists():
                            try:
                                with open(env_file, 'r', encoding='utf-8') as f:
                                    for line in f:
                                        line = line.strip()
                                        if not line or line.startswith('#'):
                                            continue
                                        
                                        if '=' in line:
                                            key, value = line.split('=', 1)
                                            key = key.strip()
                                            value = value.strip()
                                            
                                            # Remove quotes if present
                                            if value.startswith('"') and value.endswith('"'):
                                                value = value[1:-1]
                                            elif value.startswith("'") and value.endswith("'"):
                                                value = value[1:-1]
                                            
                                            if key == 'WEBSHARE_API_TOKEN':
                                                api_token = value
                                            elif key == 'WEBSHARE_PROXY_USERNAME':
                                                proxy_username = value
                                            elif key == 'WEBSHARE_PROXY_PASSWORD':
                                                proxy_password = value
                            except Exception as e:
                                print(f"  [BACKGROUND] ⚠️  Could not read .env file: {str(e)}", flush=True)
                    
                    # Use API token if available, otherwise use username/password
                    if not api_token and (not proxy_username or not proxy_password):
                        print(f"  [BACKGROUND] ⚠️  Webshare credentials not found, proceeding without proxy", flush=True)
                        return None
                    
                    # Use API token if available, otherwise username will be used as token
                    token_to_use = api_token if api_token else proxy_username
                    
                    if not token_to_use or not token_to_use.strip():
                        print(f"  [BACKGROUND] ⚠️  Token is empty, proceeding without proxy", flush=True)
                        return None
                    
                    # Fetch proxy list from Webshare API
                    try:
                        print(f"  [BACKGROUND] Loading proxy configuration...", flush=True)
                        api_url = 'https://proxy.webshare.io/api/v2/proxy/list/'
                        
                        headers = {
                            'Authorization': f'Token {token_to_use}'
                        }
                        
                        # Disable SSL warnings
                        import urllib3
                        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                        
                        # Try backbone mode first, then fallback to other modes
                        modes_to_try = ['backbone', None, 'backconnect', 'datacenter', 'direct']
                        response = None
                        
                        for mode in modes_to_try:
                            params = {
                                'page': 1,
                                'page_size': 25,  # Fetch multiple proxies for rotation
                            }
                            if mode:
                                params['mode'] = mode
                            
                            try:
                                test_response = requests.get(api_url, headers=headers, params=params, timeout=10, verify=False)
                            except requests.exceptions.SSLError:
                                test_response = requests.get(api_url, headers=headers, params=params, timeout=10, verify=False)
                            
                            if test_response.status_code == 200:
                                response = test_response
                                break
                            elif test_response.status_code == 400:
                                continue
                            else:
                                continue
                        
                        # If all modes failed and we have username/password, try basic auth as fallback
                        if (not response or (hasattr(response, 'status_code') and response.status_code != 200)) and not api_token and proxy_username and proxy_password:
                            params = {'page': 1, 'page_size': 25}
                            try:
                                auth = (proxy_username, proxy_password)
                                response = requests.get(api_url, auth=auth, params=params, timeout=10, verify=False)
                            except:
                                pass
                        
                        if response and hasattr(response, 'status_code') and response.status_code == 200:
                            data = response.json()
                            results = data.get('results', [])
                            
                            if results:
                                import random
                                # Select a random proxy from the list for better distribution
                                proxy = random.choice(results)
                                proxy_address = proxy.get('proxy_address')
                                port = proxy.get('port')
                                username = proxy.get('username')
                                password = proxy.get('password')
                                
                                # For backbone proxies, proxy_address can be null, use p.webshare.io as default
                                if not proxy_address:
                                    proxy_address = 'p.webshare.io'
                                
                                if proxy_address and port and username and password:
                                    proxy_url = f'http://{username}:{password}@{proxy_address}:{port}'
                                    proxies = {
                                        'http': proxy_url,
                                        'https': proxy_url
                                    }
                                    print(f"  [BACKGROUND] ✅ Loaded proxy: {proxy_address}:{port} (selected from {len(results)} available)", flush=True)
                                    return proxies
                    except Exception as e:
                        print(f"  [BACKGROUND] ⚠️  Error loading proxy: {str(e)}, proceeding without proxy", flush=True)
                    
                    return None
                
                # Load proxy configuration
                proxies = load_proxy_config()
                
                # Helper function to fetch sitemap with multiple approaches
                def fetch_sitemap(sitemap_url, base_url=None, proxies=None):
                    """Fetch sitemap using multiple approaches"""
                    if not base_url:
                        parsed = urlparse(sitemap_url)
                        base_url = f"{parsed.scheme}://{parsed.netloc}"
                    
                    response = None
                    
                    # Approach 1: Use curl first (most reliable)
                    try:
                        print(f"    [BACKGROUND] Trying Approach 1: Using curl...", flush=True)
                        # Use much longer timeout for very large sitemaps (especially when using proxy)
                        # For 15,000+ entries, allow up to 5 minutes (300 seconds)
                        curl_cmd = ['curl', '-s', '-L', '--max-time', '300', '--connect-timeout', '30']
                        
                        # Add proxy if available
                        if proxies and proxies.get('http'):
                            proxy_url = proxies['http']
                            curl_cmd.extend(['--proxy', proxy_url])
                            # Skip SSL verification for proxy connections (common with proxies)
                            curl_cmd.extend(['-k', '--proxy-insecure'])
                        
                        curl_cmd.append(sitemap_url)
                        
                        print(f"    [BACKGROUND] (This may take several minutes for large sitemaps...)", flush=True)
                        result = subprocess.run(
                            curl_cmd,
                            capture_output=True,
                            text=True,
                            encoding='utf-8',
                            errors='replace',  # Replace invalid UTF-8 sequences instead of failing
                            timeout=320  # 300s curl timeout + 20s buffer
                        )
                        print(f"    [BACKGROUND] Curl return code: {result.returncode}", flush=True)
                        if result.returncode != 0:
                            if result.stderr:
                                print(f"    [BACKGROUND] Curl stderr: {result.stderr[:300]}", flush=True)
                        if result.returncode == 0 and result.stdout:
                            # Validate it's XML, not HTML
                            content_str = result.stdout[:200].lower()
                            if '<html' in content_str or 'cloudflare' in content_str or 'challenge' in content_str:
                                print(f"    [BACKGROUND] ✗ Curl returned HTML/Cloudflare challenge, trying next approach...", flush=True)
                            elif '<urlset' in content_str or '<sitemapindex' in content_str or '<?xml' in content_str:
                                print(f"    [BACKGROUND] ✓ Success with curl (got {len(result.stdout)} bytes, valid XML)", flush=True)
                                class MockResponse:
                                    def __init__(self, content, status_code=200):
                                        self.content = content.encode('utf-8') if isinstance(content, str) else content
                                        self.text = content if isinstance(content, str) else content.decode('utf-8', errors='ignore')
                                        self.status_code = status_code
                                        self.headers = {'Content-Type': 'application/xml'}
                                return MockResponse(result.stdout, 200)
                            else:
                                print(f"    [BACKGROUND] ✗ Curl content doesn't appear to be XML, trying next approach...", flush=True)
                        else:
                            if result.stderr:
                                print(f"    [BACKGROUND] ✗ Curl failed: {result.stderr[:200]}", flush=True)
                            else:
                                print(f"    [BACKGROUND] ✗ Curl failed: return code {result.returncode}, no output", flush=True)
                    except FileNotFoundError:
                        print(f"    [BACKGROUND] ✗ Curl not found in PATH, trying next approach...", flush=True)
                    except subprocess.TimeoutExpired:
                        print(f"    [BACKGROUND] ✗ Curl timeout, trying next approach...", flush=True)
                    except Exception as e:
                        print(f"    [BACKGROUND] ✗ Curl failed: {type(e).__name__}: {str(e)}, trying next approach...", flush=True)
                    
                    # Approach 2: Full headers with session
                    if not response:
                        try:
                            print(f"    [BACKGROUND] Trying Approach 2: Full headers with session...", flush=True)
                            headers = {
                                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                                'Accept-Language': 'en-US,en;q=0.9',
                                'Referer': f"{base_url}/",
                            }
                            session = requests.Session()
                            session.headers.update(headers)
                            session.get(base_url, timeout=10, allow_redirects=True, proxies=proxies)
                            time.sleep(1.0)
                            response = session.get(sitemap_url, timeout=30, allow_redirects=True, proxies=proxies)
                            print(f"    [BACKGROUND] Approach 2 response: HTTP {response.status_code}", flush=True)
                            if response.status_code in (200, 202):
                                # Check if content is actually present and is XML (not HTML/Cloudflare challenge)
                                if hasattr(response, 'content') and response.content and len(response.content) > 0:
                                    content_str = response.content.decode('utf-8', errors='ignore')[:200].lower()
                                    # Check if it's HTML/Cloudflare challenge instead of XML
                                    if '<html' in content_str or 'cloudflare' in content_str or 'challenge' in content_str:
                                        print(f"    [BACKGROUND] ✗ Approach 2 returned HTML/Cloudflare challenge, trying next approach...", flush=True)
                                    elif '<urlset' in content_str or '<sitemapindex' in content_str or '<?xml' in content_str:
                                        print(f"    [BACKGROUND] ✓ Success with Approach 2 (content: {len(response.content)} bytes, valid XML)", flush=True)
                                        return response
                                    else:
                                        print(f"    [BACKGROUND] ✗ Approach 2 content doesn't appear to be XML, trying next approach...", flush=True)
                                else:
                                    print(f"    [BACKGROUND] ✗ Approach 2 returned {response.status_code} but content is empty, trying next approach...", flush=True)
                            else:
                                print(f"    [BACKGROUND] ✗ Approach 2 failed: HTTP {response.status_code}", flush=True)
                        except Exception as e:
                            print(f"    [BACKGROUND] ✗ Approach 2 failed: {type(e).__name__}: {str(e)}", flush=True)
                    
                    # Approach 3: Minimal headers
                    if not response or (hasattr(response, 'status_code') and response.status_code not in (200, 202)):
                        try:
                            print(f"    [BACKGROUND] Trying Approach 3: Minimal headers...", flush=True)
                            minimal_headers = {
                                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                                'Accept': 'application/xml, text/xml, */*',
                            }
                            response = requests.get(sitemap_url, headers=minimal_headers, timeout=30, allow_redirects=True, proxies=proxies)
                            print(f"    [BACKGROUND] Approach 3 response: HTTP {response.status_code}", flush=True)
                            if response.status_code in (200, 202):
                                # Check if content is actually present and is XML (not HTML/Cloudflare challenge)
                                if hasattr(response, 'content') and response.content and len(response.content) > 0:
                                    content_str = response.content.decode('utf-8', errors='ignore')[:200].lower()
                                    # Check if it's HTML/Cloudflare challenge instead of XML
                                    if '<html' in content_str or 'cloudflare' in content_str or 'challenge' in content_str:
                                        print(f"    [BACKGROUND] ✗ Approach 3 returned HTML/Cloudflare challenge", flush=True)
                                    elif '<urlset' in content_str or '<sitemapindex' in content_str or '<?xml' in content_str:
                                        print(f"    [BACKGROUND] ✓ Success with Approach 3 (content: {len(response.content)} bytes, valid XML)", flush=True)
                                        return response
                                    else:
                                        print(f"    [BACKGROUND] ✗ Approach 3 content doesn't appear to be XML", flush=True)
                                else:
                                    print(f"    [BACKGROUND] ✗ Approach 3 returned {response.status_code} but content is empty", flush=True)
                            else:
                                print(f"    [BACKGROUND] ✗ Approach 3 failed: HTTP {response.status_code}", flush=True)
                        except Exception as e:
                            print(f"    [BACKGROUND] ✗ Approach 3 failed: {type(e).__name__}: {str(e)}", flush=True)
                    
                    return response
                
                # Helper function to check if sitemap is for posts
                def is_post_sitemap(sitemap_url):
                    sitemap_lower = sitemap_url.lower()
                    return ('post-sitemap' in sitemap_lower or 'article' in sitemap_lower)
                
                # Helper function to parse sitemap recursively
                def parse_sitemap(sitemap_url, base_url=None, proxies=None):
                    """Parse sitemap and return list of posts"""
                    posts = []
                    
                    if not base_url:
                        parsed = urlparse(sitemap_url)
                        base_url = f"{parsed.scheme}://{parsed.netloc}"
                    
                    print(f"  [BACKGROUND] Fetching sitemap: {sitemap_url}", flush=True)
                    response = fetch_sitemap(sitemap_url, base_url, proxies=proxies)
                    
                    if not response or response.status_code not in (200, 202):
                        status_code = response.status_code if (response and hasattr(response, 'status_code')) else 'No response'
                        print(f"  [BACKGROUND] ✗ Failed to fetch sitemap: {sitemap_url} (HTTP {status_code})", flush=True)
                        return posts
                    
                    # Validate response content
                    if not hasattr(response, 'content') or not response.content:
                        print(f"  [BACKGROUND] ✗ Sitemap response has no content", flush=True)
                        return posts
                    
                    # Debug: Check content length and preview
                    content_length = len(response.content) if hasattr(response, 'content') else 0
                    print(f"  [BACKGROUND] Response content length: {content_length} bytes", flush=True)
                    
                    if content_length == 0:
                        print(f"  [BACKGROUND] ✗ Sitemap response has empty content", flush=True)
                        return posts
                    
                    # Debug: Show first 500 chars of content
                    content_preview = response.content[:500].decode('utf-8', errors='ignore') if isinstance(response.content, bytes) else str(response.content)[:500]
                    print(f"  [BACKGROUND] Content preview (first 500 chars): {content_preview[:200]}...", flush=True)
                    
                    # Parse XML using ElementTree
                    try:
                        # Get content as bytes or string
                        if isinstance(response.content, bytes):
                            xml_content = response.content
                        else:
                            xml_content = response.content.encode('utf-8')
                        
                        root = ET.fromstring(xml_content)
                    except Exception as e:
                        print(f"  [BACKGROUND] ✗ Failed to parse XML: {type(e).__name__}: {str(e)}", flush=True)
                        return posts
                    
                    # Define namespace for sitemap XML
                    # Sitemaps use: http://www.sitemaps.org/schemas/sitemap/0.9
                    # ElementTree includes full namespace URI in tag names when default namespace is used
                    sitemap_ns = 'http://www.sitemaps.org/schemas/sitemap/0.9'
                    namespace = {'sitemap': sitemap_ns}
                    
                    # Helper function to find elements with or without namespace
                    def find_with_ns(elem, tag_name):
                        """Find element with or without namespace"""
                        # Try with namespace prefix
                        result = elem.find(f'.//sitemap:{tag_name}', namespace)
                        if result is not None:
                            return result
                        # Try without namespace (full URI)
                        result = elem.find(f'.//{{{sitemap_ns}}}{tag_name}')
                        if result is not None:
                            return result
                        # Try without any namespace
                        return elem.find(f'.//{tag_name}')
                    
                    def findall_with_ns(elem, tag_name):
                        """Find all elements with or without namespace"""
                        results = elem.findall(f'.//sitemap:{tag_name}', namespace)
                        if results:
                            return results
                        results = elem.findall(f'.//{{{sitemap_ns}}}{tag_name}')
                        if results:
                            return results
                        return elem.findall(f'.//{tag_name}')
                    
                    # Check if it's a sitemap index
                    sitemapindex = find_with_ns(root, 'sitemapindex')
                    has_sitemap_elements = len(findall_with_ns(root, 'sitemap')) > 0
                    
                    if sitemapindex is not None or root.tag.endswith('sitemapindex') or has_sitemap_elements:
                        print(f"  [BACKGROUND] Found sitemap index, filtering for post sitemaps...", flush=True)
                        sitemap_urls = []
                        
                        # Find all sitemap elements (try with and without namespace)
                        sitemap_elements = findall_with_ns(root, 'sitemap')
                        if not sitemap_elements:
                            sitemap_elements = [elem for elem in root.iter() if elem.tag.endswith('sitemap')]
                        
                        for sitemap_elem in sitemap_elements:
                            # Find loc element (try with and without namespace)
                            loc = find_with_ns(sitemap_elem, 'loc')
                            if loc is not None and loc.text:
                                sitemap_urls.append(loc.text.strip())
                        
                        print(f"  [BACKGROUND] Found {len(sitemap_urls)} total sitemap(s) in index", flush=True)
                        if sitemap_urls:
                            print(f"  [BACKGROUND] Sitemap URLs found: {sitemap_urls[:3]}..." if len(sitemap_urls) > 3 else f"  [BACKGROUND] Sitemap URLs found: {sitemap_urls}", flush=True)
                        
                        post_sitemaps = [url for url in sitemap_urls if is_post_sitemap(url)]
                        print(f"  [BACKGROUND] Filtered to {len(post_sitemaps)} post-related sitemap(s)", flush=True)
                        
                        if not post_sitemaps:
                            print(f"  [BACKGROUND] ⚠️  No post-related sitemaps found in index", flush=True)
                            print(f"  [BACKGROUND] All sitemap URLs: {sitemap_urls}", flush=True)
                            return posts
                        
                        for i, post_sitemap_url in enumerate(post_sitemaps, 1):
                            print(f"  [BACKGROUND] Parsing post sitemap {i}/{len(post_sitemaps)}: {post_sitemap_url}", flush=True)
                            nested_posts = parse_sitemap(post_sitemap_url, base_url, proxies=proxies)
                            posts.extend(nested_posts)
                            print(f"  [BACKGROUND] Extracted {len(nested_posts)} posts from this sitemap", flush=True)
                            if i < len(post_sitemaps):
                                time.sleep(0.5)
                    else:
                        # Regular sitemap with URLs
                        print(f"  [BACKGROUND] Parsing regular sitemap (not an index)...", flush=True)
                        
                        # Find all url elements (try with and without namespace)
                        url_elements = findall_with_ns(root, 'url')
                        if not url_elements:
                            url_elements = [elem for elem in root.iter() if elem.tag.endswith('url')]
                        
                        print(f"  [BACKGROUND] Found {len(url_elements)} <url> tags", flush=True)
                        
                        filtered_language_count = 0
                        for url_elem in url_elements:
                            # Find loc element (try with and without namespace)
                            loc = find_with_ns(url_elem, 'loc')
                            if loc is None or not loc.text:
                                continue
                            
                            url = loc.text.strip()
                            
                            # Filter out non-English versions (URLs with language codes)
                            if has_language_code(url):
                                filtered_language_count += 1
                                continue
                            
                            # Extract lastmod (date) - try with and without namespace
                            lastmod = find_with_ns(url_elem, 'lastmod')
                            date_str = ''
                            if lastmod is not None and lastmod.text:
                                date_str = lastmod.text.strip()
                            
                            # Debug: log first few dates to verify extraction
                            if len(posts) < 3:
                                print(f"    [BACKGROUND] Sample post {len(posts)+1}: URL={url[:60]}..., date_str='{date_str}'", flush=True)
                            
                            # Generate title from URL if not in sitemap
                            # Extract the slug from URL and convert to title
                            parsed = urlparse(url)
                            path = parsed.path.strip('/')
                            # Get the last part of the path (slug)
                            slug = path.split('/')[-1] if path else ''
                            # Convert slug to title (replace hyphens with spaces, title case)
                            if slug:
                                title = slug.replace('-', ' ').replace('_', ' ').title()
                            else:
                                title = url
                            
                            posts.append({
                                'url': url,
                                'title': title,
                                'date': date_str
                            })
                        
                        if filtered_language_count > 0:
                            print(f"  [BACKGROUND] ⏭️  Filtered out {filtered_language_count} non-English URLs (language codes)", flush=True)
                        print(f"  [BACKGROUND] Extracted {len(posts)} posts from sitemap", flush=True)
                    
                    return posts
                
                # Parse the sitemap
                posts = parse_sitemap(source_refresh.sitemap, proxies=proxies)
                
                if not posts:
                    print(f"  [BACKGROUND] No posts found in sitemap", flush=True)
                    return
                
                # Limit to 10 posts if test mode is enabled
                if test_mode:
                    posts = posts[:10]
                    print(f"  [BACKGROUND] TEST MODE: Limiting to 10 posts", flush=True)
                
                print(f"  [BACKGROUND] Found {len(posts)} posts in sitemap, checking which are new...", flush=True)
                
                # Get both external_id and link to check for existing posts
                # (some posts might have URL in external_id, others in link)
                existing_external_ids_raw = set(
                    Content.objects.filter(source=source_refresh)
                    .exclude(external_id__isnull=True)
                    .exclude(external_id='')
                    .values_list('external_id', flat=True)
                )
                existing_links_raw = set(
                    Content.objects.filter(source=source_refresh)
                    .exclude(link__isnull=True)
                    .exclude(link='')
                    .values_list('link', flat=True)
                )
                # Combine both sets (URLs might be in either field)
                existing_urls_raw = existing_external_ids_raw | existing_links_raw
                # Count actual Content objects, not unique URLs
                existing_count = Content.objects.filter(source=source_refresh).count()
                # Normalize existing URLs (remove trailing slashes for comparison)
                existing_urls_normalized = {url.rstrip('/') for url in existing_urls_raw if url}
                print(f"  [BACKGROUND] {existing_count} posts already exist in database", flush=True)
                print(f"  [BACKGROUND] (checked both external_id and link fields for {len(existing_urls_normalized)} unique URLs)", flush=True)
                
                # Filter out existing posts first (before applying content filters)
                if existing_urls_normalized:
                    posts_before_existing = len(posts)
                    posts = [post for post in posts if post['url'].rstrip('/') not in existing_urls_normalized]
                    filtered_existing = posts_before_existing - len(posts)
                    if filtered_existing > 0:
                        print(f"  [BACKGROUND] Filtered out {filtered_existing} existing posts", flush=True)
                
                # Step 1: If filter_china is enabled, apply keyword-based URL filter first
                filter_china = source_refresh.filter_china
                filtered_china_keyword = 0
                if filter_china:
                    posts_before_china_keyword = len(posts)
                    filtered_posts_china = []
                    for post in posts:
                        post_url = post.get('url', '')
                        post_title = post.get('title', '')
                        # Check URL first (most reliable), then title
                        if is_china_related(post_url) or (post_title and is_china_related(post_title)):
                            filtered_posts_china.append(post)
                    posts = filtered_posts_china
                    filtered_china_keyword = posts_before_china_keyword - len(posts)
                    if filtered_china_keyword > 0:
                        print(f"  [BACKGROUND] Filtered out {filtered_china_keyword} non-China-related posts (keyword-based URL filter)", flush=True)
                
                # Create Content entries for each post
                created_count = 0
                skipped_count = 0
                created_content_ids = []  # Store content IDs for processing
                
                # Helper functions for keyword-based filtering (fallback)
                def is_job_posting_keyword(post):
                    """Check if a post is a job posting based on keywords"""
                    title = post.get('title', '').lower()
                    tags = post.get('tags', '').lower() if post.get('tags') else ''
                    combined = f"{title} {tags}"
                    
                    job_keywords = [
                        'career', 'careers', 'job', 'jobs', 'hiring', 'position', 'vacancy',
                        'vacancies', 'recruit', 'recruitment', 'intern', 'internship', 'internships',
                        'manager', 'director', 'coordinator', 'specialist', 'associate', 'executive',
                        'applicant', 'application', 'apply now', 'join our team', 'we are hiring',
                        'open position', 'full-time', 'part-time', 'remote position'
                    ]
                    
                    for keyword in job_keywords:
                        if keyword in combined:
                            return True
                    return False
                
                def is_non_travel_content_keyword(post):
                    """Check if a post is non-travel content based on keywords"""
                    title = post.get('title', '').lower()
                    link = post.get('url', post.get('link', '')).lower()
                    tags = post.get('tags', '').lower() if post.get('tags') else ''
                    combined = f"{title} {link} {tags}"
                    
                    legal_url_patterns = [
                        '/aboutus/', '/about-us/', '/terms', '/disclaimer', '/privacy',
                        '/contact', '/partner/', '/partners/', '/affiliate', '/sitemap',
                        '/legal/', '/policy/', '/policies/'
                    ]
                    
                    for pattern in legal_url_patterns:
                        if pattern in link:
                            return True
                    
                    legal_keywords = [
                        'terms and conditions', 'privacy policy', 'disclaimer',
                        'contact us', 'about us', 'sitemap'
                    ]
                    
                    for keyword in legal_keywords:
                        if keyword in title:
                            return True
                    
                    return False
                
                def filter_post_with_ollama(post, model, check_china=False):
                    """Use Ollama to filter a post based on multiple criteria"""
                    import json
                    title = post.get('title', '')
                    url = post.get('url', post.get('link', ''))
                    tags = post.get('tags', '')
                    
                    tags_str = tags if isinstance(tags, str) else ', '.join(tags) if tags else 'None'
                    
                    prompt = f"""Analyze the following blog post and determine:
1. Is this a job posting or job advertisement?
2. Is this non-travel content (legal pages, business pages, company announcements, etc.)?
{f"3. Is this content related to China, Chinese culture, Chinese geography, or Chinese topics?" if check_china else ""}

Title: {title}
URL: {url}
Tags: {tags_str}

Instructions:
- A job posting includes: job openings, hiring announcements, career opportunities, internships, positions available, etc.
- Non-travel content includes: legal pages (terms, privacy, disclaimer), business pages (about us, contact, partners), company announcements (awards, partnerships) that are NOT about travel destinations or experiences
- Keep travel-related company news (e.g., "New tour in Yunnan", "Award for best China travel guide")
- {"China-related content includes: Chinese cities, provinces, culture, history, geography, food, traditions, travel in China, etc. Exclude Chinatowns in other countries unless they're specifically about China." if check_china else ""}

Respond in the following JSON format:
{{
    "is_job_posting": true or false,
    "is_non_travel": true or false,
    {"\"is_china_related\": true or false," if check_china else ""}
    "reasoning": "Brief explanation for each determination (2-3 sentences)"
}}

Response:"""
                    
                    ollama_url = getattr(settings, 'OLLAMA_URL', 'http://localhost:11434')
                    url_api = f"{ollama_url}/api/generate"
                    
                    payload = {
                        "model": model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.3,
                            "top_p": 0.9,
                        }
                    }
                    
                    try:
                        response = requests.post(url_api, json=payload, timeout=30)
                        response.raise_for_status()
                        result = response.json()
                        response_text = result.get('response', '').strip()
                        
                        # Parse JSON response
                        start_idx = response_text.find('{')
                        end_idx = response_text.rfind('}')
                        
                        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                            json_str = response_text[start_idx:end_idx + 1]
                            try:
                                parsed = json.loads(json_str)
                                is_job = bool(parsed.get('is_job_posting', False))
                                is_non_travel = bool(parsed.get('is_non_travel', False))
                                is_china = bool(parsed.get('is_china_related', True)) if check_china else True
                                reasoning = parsed.get('reasoning', 'No reasoning provided')
                                return is_job, is_non_travel, is_china, reasoning
                            except json.JSONDecodeError:
                                pass
                        
                        # Fallback: try to parse the whole response as JSON
                        try:
                            parsed = json.loads(response_text)
                            is_job = bool(parsed.get('is_job_posting', False))
                            is_non_travel = bool(parsed.get('is_non_travel', False))
                            is_china = bool(parsed.get('is_china_related', True)) if check_china else True
                            reasoning = parsed.get('reasoning', 'No reasoning provided')
                            return is_job, is_non_travel, is_china, reasoning
                        except json.JSONDecodeError:
                            pass
                    except Exception as e:
                        raise Exception(f"Ollama API error: {str(e)}")
                    
                    return False, False, True if not check_china else False, "Failed to parse Ollama response"
                
                # Step 2: Apply Ollama filtering (always, if available) for job postings, non-travel content, and China relevance
                original_count_after_china = len(posts)
                filtered_posts = []
                filtered_jobs = 0
                filtered_non_travel = 0
                filtered_china_ollama = 0
                ollama_used = False
                ollama_errors = 0
                
                # Try to use Ollama filtering
                try:
                    from sources.models import Settings
                    app_settings = Settings.get_settings()
                    ollama_model = app_settings.default_filtering_model
                    if ollama_model:
                        print(f"  [BACKGROUND] Using Ollama filtering with model: {ollama_model}", flush=True)
                        ollama_used = True
                        total_posts = len(posts)
                        
                        for i, post in enumerate(posts, 1):
                            post_title = post.get('title', '')[:60]  # Truncate for display
                            print(f"  [BACKGROUND] [{i}/{total_posts}] Analyzing: {post_title}...", end='', flush=True)
                            
                            try:
                                # Always check China relevance with Ollama if filter_china is enabled
                                # (even though we already did keyword filtering, Ollama can be more nuanced)
                                is_job, is_non_travel, is_china_related_ollama, reasoning = filter_post_with_ollama(
                                    post, ollama_model, check_china=filter_china
                                )
                                
                                # Skip job postings
                                if is_job:
                                    filtered_jobs += 1
                                    print(f" ❌ FILTERED (Job Posting)", flush=True)
                                    continue
                                
                                # Skip non-travel content
                                if is_non_travel:
                                    filtered_non_travel += 1
                                    print(f" ❌ FILTERED (Non-Travel Content)", flush=True)
                                    continue
                                
                                # Apply China filter if enabled (Ollama can refine the keyword-based filter)
                                if filter_china and not is_china_related_ollama:
                                    filtered_china_ollama += 1
                                    print(f" ❌ FILTERED (Not China-Related)", flush=True)
                                    continue
                                
                                # Post passed all filters
                                filtered_posts.append(post)
                                print(f" ✓ PASSED", flush=True)
                                
                            except Exception as e:
                                ollama_errors += 1
                                print(f" ⚠️  ERROR: {str(e)[:50]}", flush=True)
                                # Fall back to keyword-based filtering for this post
                                if is_job_posting_keyword(post):
                                    filtered_jobs += 1
                                    continue
                                if is_non_travel_content_keyword(post):
                                    filtered_non_travel += 1
                                    continue
                                # If filter_china is enabled, we already filtered by keyword, so keep the post
                                # (unless Ollama explicitly says it's not China-related, but we can't know that here)
                                filtered_posts.append(post)
                        
                        if ollama_errors > 0:
                            print(f"  [BACKGROUND] ⚠️  {ollama_errors} posts processed with keyword-based fallback due to Ollama errors", flush=True)
                except Exception as e:
                    print(f"  [BACKGROUND] ⚠️  Ollama filtering not available: {str(e)}, using keyword-based filtering", flush=True)
                    ollama_used = False
                
                # Fallback to keyword-based filtering if Ollama not used
                if not ollama_used:
                    print(f"  [BACKGROUND] Using keyword-based filtering for job postings and non-travel content", flush=True)
                    for post in posts:
                        # Skip job postings
                        if is_job_posting_keyword(post):
                            filtered_jobs += 1
                            continue
                        
                        # Skip non-travel content
                        if is_non_travel_content_keyword(post):
                            filtered_non_travel += 1
                            continue
                        
                        # China filter already applied above with keyword-based URL filter
                        # No need to check again here
                        
                        filtered_posts.append(post)
                
                posts = filtered_posts
                
                # Log filtering results
                if filtered_jobs > 0:
                    print(f"  [BACKGROUND] Filtered out {filtered_jobs} job postings (Ollama)", flush=True)
                if filtered_non_travel > 0:
                    print(f"  [BACKGROUND] Filtered out {filtered_non_travel} non-travel content posts (Ollama)", flush=True)
                if filter_china and filtered_china_ollama > 0:
                    print(f"  [BACKGROUND] Filtered out {filtered_china_ollama} non-China-related posts (Ollama refinement)", flush=True)
                
                filtered_total = original_count_after_china - len(posts)
                if filtered_total > 0:
                    print(f"  [BACKGROUND] Total filtered by Ollama: {filtered_total} posts, {len(posts)} posts remaining", flush=True)
                
                with transaction.atomic():
                    for i, post in enumerate(posts, 1):
                        post_url = post['url']
                        
                        # Note: Existing posts are already filtered out above, but we still check here
                        # as a safety measure (in case of race conditions or missed duplicates)
                        post_url_normalized = post_url.rstrip('/')
                        if existing_urls_normalized and post_url_normalized in existing_urls_normalized:
                            skipped_count += 1
                            if i % 50 == 0:
                                print(f"  [BACKGROUND] Processed {i}/{len(posts)} posts (created: {created_count}, skipped: {skipped_count})...", flush=True)
                            continue
                        
                        # Parse date from ISO format (e.g., "2025-11-13T09:45:26+00:00")
                        post_date = None
                        date_str = post.get('date', '').strip() if post.get('date') else ''
                        
                        if date_str:
                            # Debug: log first few date parsing attempts
                            if created_count < 3:
                                print(f"    [BACKGROUND] Parsing date for post {created_count+1}: '{date_str}'", flush=True)
                            
                            if date_parser:
                                try:
                                    post_date = date_parser.parse(date_str)
                                    if created_count < 3:
                                        print(f"    [BACKGROUND] ✓ Parsed date with dateutil: {post_date}", flush=True)
                                except Exception as e:
                                    print(f"    [BACKGROUND] ✗ Failed to parse date '{date_str}' with dateutil: {str(e)}", flush=True)
                            else:
                                # Fallback: try to parse ISO format manually
                                try:
                                    from datetime import datetime
                                    # Handle ISO format with timezone (e.g., "2025-11-13T09:45:26+00:00")
                                    if 'T' in date_str:
                                        # Normalize timezone format: replace Z with +00:00, ensure timezone format
                                        normalized = date_str.replace('Z', '+00:00')
                                        # Handle case where timezone might be missing
                                        if '+' not in normalized and normalized.count(':') == 2:
                                            normalized = normalized + '+00:00'
                                        post_date = datetime.fromisoformat(normalized)
                                        if created_count < 3:
                                            print(f"    [BACKGROUND] ✓ Parsed date with fromisoformat: {post_date}", flush=True)
                                    else:
                                        # Try parsing date-only format
                                        post_date = datetime.fromisoformat(date_str)
                                        if created_count < 3:
                                            print(f"    [BACKGROUND] ✓ Parsed date-only format: {post_date}", flush=True)
                                except Exception as e:
                                    print(f"    [BACKGROUND] ✗ Fallback date parsing failed for '{date_str}': {str(e)}", flush=True)
                        else:
                            if created_count < 3:
                                print(f"    [BACKGROUND] ⚠️  No date string found for post, will use current time", flush=True)
                        
                        # If no date, use current time
                        if not post_date:
                            post_date = timezone.now()
                            if created_count < 3:
                                print(f"    [BACKGROUND] Using current time: {post_date}", flush=True)
                        
                        # Create content entry (with error handling for race conditions)
                        try:
                            content = Content.objects.create(
                                source=source_refresh,
                                external_id=post_url,  # Use URL as external_id for blog posts
                                title=post.get('title', post_url),
                                link=post_url,
                                content_type='blog_post',  # Fixed: should be 'blog_post' not 'blog'
                                date=post_date,
                                content='',  # Empty - will be filled later when processing
                                processed=False,
                            )
                            created_count += 1
                            created_content_ids.append(content.id)
                        except Exception as e:
                            # Handle duplicate key errors (race condition or missed duplicate)
                            if isinstance(e, IntegrityError) and 'unique constraint' in str(e).lower():
                                skipped_count += 1
                                if i % 50 == 0 or i <= 3:
                                    print(f"    [BACKGROUND] Skipped duplicate (race condition or missed check): {post_url[:60]}...", flush=True)
                            else:
                                # Re-raise other errors
                                raise
                        
                        if i % 50 == 0:
                            print(f"  [BACKGROUND] Processed {i}/{len(posts)} posts (created: {created_count}, skipped: {skipped_count})...", flush=True)
                
                # Update last_collected timestamp
                if posts:
                    source_refresh.last_collected = timezone.now()
                    source_refresh.save(update_fields=['last_collected'])
                
                # Process each created post: extract content, translate, tag, embed
                extracted_count = 0
                extracted_failed = 0
                translated_count = 0
                translated_failed = 0
                tagged_count = 0
                tagged_failed = 0
                embedded_count = 0
                embedded_failed = 0
                processed_count = 0
                
                if created_content_ids:
                    print(f"\n  [BACKGROUND] Processing {len(created_content_ids)} posts (extract, translate, tag, embed)...", flush=True)
                    from .content_processing_service import ContentProcessingService
                    
                    # Use proxy support for content extraction (same as sitemap fetching)
                    processing_service = ContentProcessingService(use_proxy=True)
                    
                    for idx, content_id in enumerate(created_content_ids, 1):
                        try:
                            # Get fresh content object
                            content = Content.objects.get(pk=content_id)
                            
                            # Step 1: Extract content
                            print(f"  [BACKGROUND] [{idx}/{len(created_content_ids)}] Processing: {content.title[:60]}...", flush=True)
                            extraction_succeeded = False
                            try:
                                if processing_service.extract_content(content, force=False):
                                    extracted_count += 1
                                    content.refresh_from_db()
                                    extraction_succeeded = True
                                else:
                                    extracted_failed += 1
                                    print(f"    [BACKGROUND] ✗ Failed to extract content from: {content.link[:80]}...", flush=True)
                            except Exception as extract_error:
                                extracted_failed += 1
                                print(f"    [BACKGROUND] ✗ Exception extracting content: {str(extract_error)}", flush=True)
                            
                            # Only proceed with translate/tag/embed if extraction succeeded
                            if extraction_succeeded:
                                # Step 2: Translate (if source language is not English)
                                if content.source.language != 'en' and content.content and content.content.strip():
                                    if processing_service.translate_content(content):
                                        translated_count += 1
                                        content.refresh_from_db()
                                    else:
                                        translated_failed += 1
                                
                                # Step 3: Tag (only if we have content)
                                if content.content and content.content.strip():
                                    if processing_service.add_tags(content):
                                        tagged_count += 1
                                        content.refresh_from_db()
                                    else:
                                        tagged_failed += 1
                                    
                                    # Step 4: Embed (only if it has tags)
                                    if content.tags.exists():
                                        if processing_service.generate_embeddings(content):
                                            embedded_count += 1
                                            content.processed = True
                                            content.save(update_fields=['processed'])
                                            processed_count += 1
                                        else:
                                            embedded_failed += 1
                                    else:
                                        print(f"    [BACKGROUND] Skipping embedding (no tags)", flush=True)
                            
                            if idx % 10 == 0:
                                print(f"  [BACKGROUND] Progress: {idx}/{len(created_content_ids)} (extracted: {extracted_count}, translated: {translated_count}, tagged: {tagged_count}, embedded: {embedded_count})...", flush=True)
                        except Exception as e:
                            import traceback
                            print(f"  [BACKGROUND] Error processing content {content_id}: {str(e)}", flush=True)
                            print(f"  [BACKGROUND] Traceback: {traceback.format_exc()}", flush=True)
                    
                    print(f"\n  [BACKGROUND] Processing completed:", flush=True)
                    print(f"    - Content extracted: {extracted_count} succeeded, {extracted_failed} failed", flush=True)
                    print(f"    - Translated: {translated_count} succeeded, {translated_failed} failed", flush=True)
                    print(f"    - Tagged: {tagged_count} succeeded, {tagged_failed} failed", flush=True)
                    print(f"    - Embedded: {embedded_count} succeeded, {embedded_failed} failed", flush=True)
                    print(f"    - Fully processed: {processed_count}", flush=True)
                
                # Log the activity
                log_activity(
                    'content_created',
                    f'Fetched {created_count} blog posts from sitemap "{source_refresh.name}"',
                    user=None,  # Background task, no user context
                    source=source_refresh,
                    metadata={
                        'posts_fetched': created_count,
                        'posts_skipped': skipped_count,
                        'total_found': len(posts),
                        'extracted': extracted_count,
                        'translated': translated_count,
                        'tagged': tagged_count,
                        'embedded': embedded_count,
                        'processed': processed_count,
                    }
                )
                
                print(f"\n{'='*60}", flush=True)
                print(f"  [BACKGROUND] ✓ Import completed: {created_count} created, {skipped_count} skipped", flush=True)
                if created_content_ids:
                    print(f"  [BACKGROUND] ✓ Processing completed: {processed_count} fully processed", flush=True)
                print(f"{'='*60}\n", flush=True)
                
            except Exception as e:
                import traceback
                print(f"  [BACKGROUND] ERROR: {str(e)}", flush=True)
                print(f"  [BACKGROUND] Traceback: {traceback.format_exc()}", flush=True)
            finally:
                # Ensure database connection is closed
                connection.close()
        
        # Start background thread
        thread = threading.Thread(target=import_posts_background, daemon=True)
        thread.start()
        
        messages.info(
            request,
            f'Blog post import started in the background for "{source.name}". '
            f'This may take several minutes. Check the logs for progress.'
        )
        
        return redirect('sources:source_edit', pk=source.pk)
    
    if request.method == 'POST':
        form = SourceForm(request.POST, request.FILES, instance=source)
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
            if provider == 'ollama':
                try:
                    app_settings = Settings.get_settings()
                    selected_model = app_settings.default_tagging_model
                except Exception:
                    selected_model = 'gpt-oss:20b-cloud'
            elif provider == 'openai':
                selected_model = 'gpt-4o-mini'
            elif provider == 'gemini':
                selected_model = 'gemini-1.5-pro'
        
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


@login_required
def blog_post_list(request):
    """Display list of all blog posts"""
    # Optimize queryset: select_related for ForeignKey, prefetch_related for ManyToMany
    # Base queryset with optimizations
    base_queryset = BlogPost.objects.select_related('post_idea').prefetch_related('tags')
    
    # Get total count before filtering
    total_count = base_queryset.count()
    
    # Apply search filter
    search_query = request.GET.get('search', '').strip()
    if search_query:
        # For search, we need to include content field for searching
        blog_posts = base_queryset.filter(
            Q(title__icontains=search_query) | 
            Q(content__icontains=search_query) |
            Q(meta_title__icontains=search_query) |
            Q(meta_description__icontains=search_query)
        )
    else:
        # For non-search queries, defer the large content field
        blog_posts = base_queryset.defer('content')
    
    # Filter by published status if provided
    published_filter = request.GET.get('published', '').strip()
    if published_filter == 'true':
        blog_posts = blog_posts.filter(published=True)
    elif published_filter == 'false':
        blog_posts = blog_posts.filter(published=False)
    
    # Filter by tag if provided
    tag_id = request.GET.get('tag', '').strip()
    if tag_id:
        try:
            tag = Tag.objects.get(pk=tag_id)
            blog_posts = blog_posts.filter(tags=tag).distinct()
        except Tag.DoesNotExist:
            pass
    
    # Filter by post idea if provided
    post_idea_id = request.GET.get('post_idea', '').strip()
    if post_idea_id:
        try:
            post_idea = PostIdea.objects.get(pk=post_idea_id)
            blog_posts = blog_posts.filter(post_idea=post_idea)
        except PostIdea.DoesNotExist:
            pass
    
    # Apply sorting (default: newest first)
    sort_by = request.GET.get('sort', 'newest').strip()
    if sort_by == 'oldest':
        blog_posts = blog_posts.order_by('created_at')
    elif sort_by == 'title_asc':
        blog_posts = blog_posts.order_by('title')
    elif sort_by == 'title_desc':
        blog_posts = blog_posts.order_by('-title')
    elif sort_by == 'updated':
        blog_posts = blog_posts.order_by('-updated_at')
    else:
        # Default: newest first
        blog_posts = blog_posts.order_by('-created_at')
    
    # Pagination
    paginator = Paginator(blog_posts, 25)  # Show 25 blog posts per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Get filtered count from paginator
    filtered_count = paginator.count
    
    # Get all tags for filter dropdown (cached for 1 hour)
    cache_key = 'all_tags_list'
    all_tags = cache.get(cache_key)
    if all_tags is None:
        all_tags = list(Tag.objects.all().order_by('name'))
        cache.set(cache_key, all_tags, 3600)  # Cache for 1 hour
    
    context = {
        'blog_posts': page_obj,
        'page_obj': page_obj,
        'search_query': search_query,
        'sort_by': sort_by,
        'total_count': total_count,
        'filtered_count': filtered_count,
        'all_tags': all_tags,
        'selected_tag_id': tag_id,
        'selected_post_idea_id': post_idea_id,
        'published_filter': published_filter,
    }
    
    return render(request, 'sources/blog_post_list.html', context)


@login_required
def blog_post_detail(request, pk):
    """Display detail view of a blog post"""
    blog_post = get_object_or_404(BlogPost, pk=pk)
    
    context = {
        'blog_post': blog_post,
    }
    
    return render(request, 'sources/blog_post_detail.html', context)


@login_required
def blog_post_edit(request, pk):
    """Edit a blog post"""
    blog_post = get_object_or_404(BlogPost, pk=pk)
    all_tags = Tag.objects.all().order_by('name')
    
    if request.method == 'POST':
        # Update fields
        blog_post.title = request.POST.get('title', blog_post.title)
        blog_post.slug = request.POST.get('slug', blog_post.slug)
        blog_post.content = request.POST.get('content', blog_post.content)
        blog_post.meta_title = request.POST.get('meta_title', '')
        blog_post.meta_description = request.POST.get('meta_description', '')
        blog_post.featured_image_description = request.POST.get('featured_image_description', '')
        blog_post.faq_title = request.POST.get('faq_title', '')
        blog_post.published = request.POST.get('published', 'off') == 'on'
        
        # Handle FAQ
        faq_text = request.POST.get('faq', '').strip()
        if faq_text:
            try:
                faq_data = json.loads(faq_text)
                # Validate it's a list with proper structure
                if isinstance(faq_data, list):
                    # Ensure each item has question and answer
                    valid_faq = []
                    for item in faq_data:
                        if isinstance(item, dict) and 'question' in item and 'answer' in item:
                            valid_faq.append({
                                'question': str(item['question']).strip(),
                                'answer': str(item['answer']).strip()
                            })
                    blog_post.faq = valid_faq
                else:
                    blog_post.faq = []
            except (json.JSONDecodeError, ValueError, KeyError):
                # If JSON parsing fails, keep existing FAQ or set to empty
                if not blog_post.faq:
                    blog_post.faq = []
        else:
            blog_post.faq = []
        
        # Handle featured image upload
        if 'featured_image' in request.FILES:
            blog_post.featured_image = request.FILES['featured_image']
        
        # Handle tags
        selected_tag_ids = request.POST.getlist('tags')
        blog_post.tags.set(selected_tag_ids)
        
        # Save
        try:
            blog_post.save()
            
            # Parse and sync image records from content
            _parse_and_create_blog_post_images(blog_post)
            
            messages.success(request, f'Blog post "{blog_post.title}" updated successfully!')
            
            # Log activity
            log_activity(
                'blog_post_updated',
                f'Blog post "{blog_post.title}" was updated',
                user=request.user,
                metadata={'blog_post_id': blog_post.id}
            )
            
            return redirect('sources:blog_post_detail', pk=blog_post.pk)
        except Exception as e:
            messages.error(request, f'Error updating blog post: {str(e)}')
    
    # Prefetch blog post tags to avoid N+1 queries in template
    blog_post_tags = set(blog_post.tags.all())
    
    # Format FAQ as JSON string for textarea
    faq_json = ''
    if blog_post.faq:
        faq_json = json.dumps(blog_post.faq, indent=2, ensure_ascii=False)
    
    context = {
        'blog_post': blog_post,
        'all_tags': all_tags,
        'blog_post_tags': blog_post_tags,
        'faq_json': faq_json,
    }
    
    return render(request, 'sources/blog_post_edit.html', context)


@login_required
def blog_post_delete(request, pk):
    """Delete a blog post"""
    try:
        blog_post = BlogPost.objects.get(pk=pk)
    except BlogPost.DoesNotExist:
        messages.error(request, 'Blog post not found')
        return redirect('sources:blog_post_list')
    
    if request.method == 'POST':
        title = blog_post.title
        blog_post_id = blog_post.id
        blog_post.delete()
        # Log activity
        log_activity(
            'blog_post_deleted',
            f'Blog post "{title}" was deleted',
            user=request.user,
            metadata={'blog_post_id': blog_post_id, 'title': title}
        )
        messages.success(request, f'Blog post "{title}" deleted successfully')
        return redirect('sources:blog_post_list')
    
    context = {
        'blog_post': blog_post
    }
    return render(request, 'sources/blog_post_confirm_delete.html', context)


@login_required
def blog_post_generate_metadata(request, pk):
    """Generate metadata (slug, meta title, meta description, tags) for a blog post"""
    blog_post = get_object_or_404(BlogPost, pk=pk)
    
    if not blog_post.content:
        messages.error(request, 'Blog post has no content. Please generate content first.')
        return redirect('sources:blog_post_detail', pk=blog_post.pk)
    
    # Load the prompt template
    import os
    from django.conf import settings as django_settings
    
    prompt_file_path = os.path.join(django_settings.BASE_DIR, 'prompt-metadata-generator')
    try:
        with open(prompt_file_path, 'r', encoding='utf-8') as f:
            prompt_template = f.read()
    except FileNotFoundError:
        messages.error(request, 'Prompt template file not found. Please ensure prompt-metadata-generator exists.')
        return redirect('sources:blog_post_detail', pk=blog_post.pk)
    
    if request.method == 'POST':
        provider = request.POST.get('provider', 'gemini').strip().lower()
        model = request.POST.get('model', 'gemini-3-pro-preview').strip()
        
        # Validate provider
        if provider not in ['ollama', 'openai', 'gemini']:
            messages.error(request, 'Invalid provider selected.')
            return redirect('sources:blog_post_detail', pk=blog_post.pk)
        
        # Get default model if not provided
        if not model:
            if provider == 'ollama':
                try:
                    app_settings = Settings.get_settings()
                    model = app_settings.default_tagging_model
                except Exception:
                    model = 'gpt-oss:20b-cloud'
            elif provider == 'openai':
                model = 'gpt-4o-mini'
            elif provider == 'gemini':
                model = 'gemini-3-pro-preview'
        
        try:
            # Strip HTML tags from content before sending to AI (HTML might trigger safety filters)
            import re
            # Remove HTML tags but keep the text content
            text_content = re.sub(r'<[^>]+>', ' ', blog_post.content)
            # Clean up extra whitespace
            text_content = ' '.join(text_content.split())
            
            # Build the prompt with the cleaned text content and current year
            current_year = str(datetime.now().year)
            prompt = prompt_template.replace('{current_year}', current_year).replace('[PASTE YOUR GENERATED HTML CONTENT HERE]', text_content)
            
            # Call the appropriate AI provider
            # Use higher token limits for metadata generation (especially for Gemini which uses "thoughts" tokens)
            rag_service = RAGService()
            if provider == 'ollama':
                generated_metadata = rag_service._call_ollama(prompt, model, max_tokens=2000)
            elif provider == 'openai':
                generated_metadata = rag_service._call_openai(prompt, model, max_tokens=2000)
            elif provider == 'gemini':
                # Gemini models (especially gemini-3-pro) use "thoughts" tokens, so we need more headroom
                # Increase to 4000 to account for thoughts tokens (999 used) + actual response
                generated_metadata = rag_service._call_gemini(prompt, model, max_tokens=4000)
            else:
                messages.error(request, 'Invalid provider.')
                return redirect('sources:blog_post_detail', pk=blog_post.pk)
            
            # Parse the generated metadata
            import re
            
            # Extract meta title - try multiple patterns
            meta_title = None
            patterns = [
                r'\*\*Meta Title:\*\*\s*(.+?)(?:\n|$)',
                r'Meta Title:\s*(.+?)(?:\n|$)',
                r'Title:\s*(.+?)(?:\n|$)',
            ]
            for pattern in patterns:
                meta_title_match = re.search(pattern, generated_metadata, re.IGNORECASE)
                if meta_title_match:
                    meta_title = meta_title_match.group(1).strip()
                    # Remove markdown formatting if present
                    meta_title = re.sub(r'\*\*|\*|\[|\]|`', '', meta_title).strip()
                    if meta_title and len(meta_title) <= 60:
                        blog_post.meta_title = meta_title
                        break
            
            # Extract meta description - try multiple patterns
            meta_description = None
            patterns = [
                r'\*\*Meta Description:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
                r'Meta Description:\s*(.+?)(?=\n\*\*|\n\n|$)',
                r'Description:\s*(.+?)(?=\n\*\*|\n\n|$)',
            ]
            for pattern in patterns:
                meta_desc_match = re.search(pattern, generated_metadata, re.IGNORECASE | re.DOTALL)
                if meta_desc_match:
                    meta_description = meta_desc_match.group(1).strip()
                    # Remove markdown formatting if present
                    meta_description = re.sub(r'\*\*|\*|\[|\]|`', '', meta_description).strip()
                    # Remove newlines and extra spaces
                    meta_description = ' '.join(meta_description.split())
                    if meta_description and len(meta_description) <= 160:
                        blog_post.meta_description = meta_description
                        break
            
            # Extract URL slug - try multiple patterns
            slug = None
            patterns = [
                r'\*\*URL Slug:\*\*\s*(.+?)(?:\n|$)',
                r'URL Slug:\s*(.+?)(?:\n|$)',
                r'Slug:\s*(.+?)(?:\n|$)',
            ]
            for pattern in patterns:
                slug_match = re.search(pattern, generated_metadata, re.IGNORECASE)
                if slug_match:
                    slug = slug_match.group(1).strip()
                    # Remove markdown formatting and ensure it's a valid slug
                    slug = re.sub(r'\*\*|\*|\[|\]|`', '', slug).strip()
                    slug = slugify(slug)
                    if slug and len(slug) <= 255:
                        # Check if slug is unique (excluding current post)
                        if not BlogPost.objects.filter(slug=slug).exclude(pk=blog_post.pk).exists():
                            blog_post.slug = slug
                        else:
                            messages.warning(request, f'Slug "{slug}" already exists. Keeping current slug.')
                        break
            
            # Extract tags - try multiple patterns
            tags_text = None
            patterns = [
                r'\*\*Tags:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
                r'Tags:\s*(.+?)(?=\n\*\*|\n\n|$)',
            ]
            for pattern in patterns:
                tags_match = re.search(pattern, generated_metadata, re.IGNORECASE | re.DOTALL)
                if tags_match:
                    tags_text = tags_match.group(1).strip()
                    break
            
            if tags_text:
                # Remove markdown formatting
                tags_text = re.sub(r'\*\*|\*|\[|\]|`', '', tags_text)
                # Split by comma and clean up
                tag_names = [tag.strip() for tag in tags_text.split(',') if tag.strip()]
                
                # Get or create tags
                tags_to_add = []
                for tag_name in tag_names[:10]:  # Limit to 10 tags
                    if tag_name:  # Ensure tag name is not empty
                        tag_slug = slugify(tag_name)
                        # First try to get by slug (since slug is unique)
                        try:
                            tag = Tag.objects.get(slug=tag_slug)
                        except Tag.DoesNotExist:
                            # If slug doesn't exist, try to get by name (case-insensitive)
                            try:
                                tag = Tag.objects.get(name__iexact=tag_name)
                            except Tag.DoesNotExist:
                                # Create new tag
                                tag = Tag.objects.create(
                                    name=tag_name,
                                    slug=tag_slug
                                )
                        tags_to_add.append(tag)
                
                # Set tags (this replaces existing tags)
                if tags_to_add:
                    blog_post.tags.set(tags_to_add)
            
            # Extract featured image alt text - try multiple patterns
            featured_image_desc = None
            patterns = [
                r'\*\*Featured Image Alt Text:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
                r'Featured Image Alt Text:\s*(.+?)(?=\n\*\*|\n\n|$)',
                r'\*\*Featured Image Description:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',  # Fallback for old format
                r'Featured Image Description:\s*(.+?)(?=\n\*\*|\n\n|$)',  # Fallback for old format
            ]
            for pattern in patterns:
                img_desc_match = re.search(pattern, generated_metadata, re.IGNORECASE | re.DOTALL)
                if img_desc_match:
                    featured_image_desc = img_desc_match.group(1).strip()
                    # Remove markdown formatting
                    featured_image_desc = re.sub(r'\*\*|\*|\[|\]|`', '', featured_image_desc).strip()
                    # Remove newlines and extra spaces
                    featured_image_desc = ' '.join(featured_image_desc.split())
                    # Limit to 200 characters for alt text (reasonable limit)
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
            
            # Save the blog post
            blog_post.save()
            
            # Log activity
            log_activity(
                'blog_post_updated',
                f'Metadata generated for blog post "{blog_post.title}"',
                user=request.user,
                metadata={
                    'blog_post_id': blog_post.id,
                    'provider': provider,
                    'model': model
                }
            )
            
            messages.success(request, 'Metadata generated successfully!')
            return redirect('sources:blog_post_detail', pk=blog_post.pk)
            
        except Exception as e:
            error_msg = str(e)
            messages.error(request, f'Error generating metadata: {error_msg}')
            import traceback
            traceback.print_exc()
    
    # GET request - show the generation form
    context = {
        'blog_post': blog_post,
        'default_provider': 'gemini',
        'default_model': 'gemini-3-pro-preview',
    }
    
    return render(request, 'sources/blog_post_generate_metadata.html', context)


@login_required
def blog_post_generate(request, pk):
    """Generate a blog post from a post idea"""
    post_idea = get_object_or_404(PostIdea, pk=pk)
    
    # Load the prompt template
    import os
    from django.conf import settings as django_settings
    
    prompt_file_path = os.path.join(django_settings.BASE_DIR, 'prompt-post-generation.md')
    try:
        with open(prompt_file_path, 'r', encoding='utf-8') as f:
            prompt_template = f.read()
    except FileNotFoundError:
        messages.error(request, 'Prompt template file not found. Please ensure prompt-post-generation.md exists.')
        return redirect('sources:post_idea_list')
    
    if request.method == 'POST':
        provider = request.POST.get('provider', 'gemini').strip().lower()
        model = request.POST.get('model', 'gemini-3-pro-preview').strip()
        use_rag = request.POST.get('use_rag', 'off') == 'on'
        num_chunks = int(request.POST.get('num_chunks', 5))
        
        # Validate provider
        if provider not in ['ollama', 'openai', 'gemini']:
            messages.error(request, 'Invalid provider selected.')
            return redirect('sources:post_idea_list')
        
        # Get default model if not provided
        if not model:
            if provider == 'ollama':
                try:
                    app_settings = Settings.get_settings()
                    model = app_settings.default_tagging_model
                except Exception:
                    model = 'gpt-oss:20b-cloud'
            elif provider == 'openai':
                model = 'gpt-4o-mini'
            elif provider == 'gemini':
                model = 'gemini-3-pro-preview'
        
        try:
            # Get RAG context if enabled
            rag_context = ""
            if use_rag:
                try:
                    rag_service = RAGService()
                    chunks = rag_service.search_similar_chunks(
                        query_text=post_idea.title,
                        num_chunks=num_chunks
                    )
                    if chunks:
                        rag_context = rag_service._format_context(chunks)
                except Exception as e:
                    messages.warning(request, f'RAG context retrieval failed: {str(e)}. Continuing without context.')
            
            # Build the prompt
            # Include primary keyword if available, otherwise use title as fallback
            primary_keyword = post_idea.primary_keyword or post_idea.title
            prompt = prompt_template.format(
                title=post_idea.title,
                description=post_idea.description or "No description provided.",
                primary_keyword=primary_keyword
            )
            
            # Add RAG context if available
            if rag_context:
                prompt = f"{prompt}\n\n### Additional Context from Content Library:\n{rag_context}"
            
            # Call the appropriate AI provider with higher token limits for blog posts
            rag_service = RAGService()
            # Set higher token limits for blog post generation
            # Blog posts need much more content than regular Q&A responses
            if provider == 'ollama':
                # Ollama: 8000 tokens (or None for unlimited if model supports it)
                generated_content = rag_service._call_ollama(prompt, model, max_tokens=8000)
            elif provider == 'openai':
                # OpenAI: 8000-16000 tokens depending on model capabilities
                # GPT-4o and newer models support up to 16k
                max_tokens = 16000 if any(keyword in model.lower() for keyword in ['gpt-4o', 'gpt-4-turbo', 'gpt-4']) else 8000
                generated_content = rag_service._call_openai(prompt, model, max_tokens=max_tokens)
            elif provider == 'gemini':
                # Gemini: gemini-3-pro-preview supports up to 32k tokens
                # Use 16k for comprehensive blog posts
                max_tokens = 16000 if 'gemini-3-pro' in model.lower() else 8000
                generated_content = rag_service._call_gemini(prompt, model, max_tokens=max_tokens)
            else:
                messages.error(request, 'Invalid provider.')
                return redirect('sources:post_idea_list')
            
            # The prompt generates the blog post content
            # We'll use the post idea title as the blog post title for now
            # Other fields (meta_title, meta_description) will be handled later
            blog_title = post_idea.title
            blog_content = generated_content.strip()
            
            # Post-process to remove any unwanted intro text or JSON
            import re
            # Remove any text before the first <h1> tag
            h1_match = re.search(r'<h1>', blog_content, re.IGNORECASE)
            if h1_match:
                blog_content = blog_content[h1_match.start():]
            
            # Remove any JSON-LD schema blocks at the end
            # Look for ```json or ``` followed by JSON content
            json_pattern = r'```(?:json)?\s*\{.*?\}\s*```'
            blog_content = re.sub(json_pattern, '', blog_content, flags=re.DOTALL | re.IGNORECASE)
            
            # Remove standalone JSON blocks (without code fences)
            json_block_pattern = r'\s*\{[^{}]*"@context"[^{}]*"@type"[^{}]*\}'
            blog_content = re.sub(json_block_pattern, '', blog_content, flags=re.DOTALL | re.IGNORECASE)
            
            # Remove common intro phrases if they appear at the start
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
            
            # Create the blog post with just the content field populated
            blog_post = BlogPost.objects.create(
                title=blog_title,
                content=blog_content,
                post_idea=post_idea
                # meta_title and meta_description will be handled later
            )
            
            # Parse and create image records from content
            _parse_and_create_blog_post_images(blog_post)
            
            # Copy tags from post idea if any (we'll need to add tags to PostIdea or handle differently)
            # For now, we'll leave tags empty
            
            # Log activity
            log_activity(
                'blog_post_created',
                f'Blog post "{blog_title}" was generated from post idea "{post_idea.title}"',
                user=request.user,
                metadata={
                    'blog_post_id': blog_post.id,
                    'post_idea_id': post_idea.id,
                    'provider': provider,
                    'model': model
                }
            )
            
            messages.success(request, f'Blog post "{blog_title}" generated successfully!')
            # Redirect to blog post detail page
            return redirect('sources:blog_post_detail', pk=blog_post.id)
            
        except Exception as e:
            error_msg = str(e)
            messages.error(request, f'Error generating blog post: {error_msg}')
            import traceback
            traceback.print_exc()
    
    # GET request - show the generation form
    # Get available models for the default provider (Gemini)
    context = {
        'post_idea': post_idea,
        'default_provider': 'gemini',
        'default_model': 'gemini-3-pro-preview',
    }
    
    return render(request, 'sources/blog_post_generate.html', context)


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
    
    elif provider == 'openai':
        # OpenAI models
        api_key = getattr(settings, 'OPENAI_API_KEY', None)
        if not api_key:
            return JsonResponse({'error': 'OPENAI_API_KEY is not set in settings'}, status=400)
        
        # Manual list of OpenAI models
        models = [
            'gpt-5.1',
            'gpt-5',
            'gpt-5-mini',
            'gpt-5-nano',
            'gpt-4o',
            'gpt-4o-mini',
            'gpt-4-turbo',
            'gpt-4',
            'gpt-3.5-turbo',
        ]
        return JsonResponse({'models': models})
    
    elif provider == 'gemini':
        # Gemini models
        api_key = getattr(settings, 'GEMINI_API_KEY', None)
        if not api_key:
            return JsonResponse({'error': 'GEMINI_API_KEY is not set in settings'}, status=400)
        
        # Common Gemini models
        models = [
            'gemini-3-pro-preview',
            'gemini-2.5-pro',
            'gemini-2.5-flash',
            'gemini-2.5-flash-lite'
        ]
        return JsonResponse({'models': models})
    
    else:
        return JsonResponse({'error': 'Invalid provider'}, status=400)


@login_required
def post_idea_models_api(request):
    """API endpoint to fetch available models for a given provider"""
    provider = request.GET.get('provider', 'ollama').strip().lower()
    
    if provider == 'ollama':
        # Use existing agent_models_api logic
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
    
    elif provider == 'openai':
        # OpenAI models
        api_key = getattr(settings, 'OPENAI_API_KEY', None)
        if not api_key:
            return JsonResponse({'error': 'OPENAI_API_KEY is not set in settings'}, status=400)
        
        # Manual list of OpenAI models
        models = [
            'gpt-5.1',
            'gpt-5',
            'gpt-5-mini',
            'gpt-5-nano',
            'gpt-4o',
            'gpt-4o-mini',
            'gpt-4-turbo',
            'gpt-4',
            'gpt-3.5-turbo',
        ]
        return JsonResponse({'models': models})
    
    elif provider == 'gemini':
        # Gemini models
        api_key = getattr(settings, 'GEMINI_API_KEY', None)
        if not api_key:
            return JsonResponse({'error': 'GEMINI_API_KEY is not set in settings'}, status=400)
        
        # Common Gemini models
        models = [
            'gemini-3-pro-preview',
            'gemini-2.5-pro',
            'gemini-2.5-flash',
            'gemini-2.5-flash-lite'
        ]
        return JsonResponse({'models': models})
    
    else:
        return JsonResponse({'error': 'Invalid provider'}, status=400)


def _validate_api_token(request):
    """Helper function to validate API token from request"""
    api_token = settings.API_TOKEN
    if not api_token:
        return None, JsonResponse({
            'success': False,
            'error': 'API token not configured'
        }, status=500)
    
    # Get token from Authorization header or query parameter
    provided_token = None
    
    # Check Authorization header: "Token <token>" or "Bearer <token>"
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if auth_header:
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() in ('token', 'bearer'):
            provided_token = parts[1]
    
    # Check query parameter
    if not provided_token:
        provided_token = request.GET.get('token', None)
    
    # Validate token
    if not provided_token or provided_token != api_token:
        return None, JsonResponse({
            'success': False,
            'error': 'Invalid or missing authentication token'
        }, status=401)
    
    return True, None


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
            from .youtube_service import is_video_relevant_to_china_with_details
            
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


def _parse_and_create_blog_post_images(blog_post):
    """
    Parse images from blog post content and create BlogPostImage records.
    Looks for <img> tags with data-filename attribute.
    """
    import re
    content = blog_post.content
    
    # Pattern to match <img> tags with data-filename attribute
    # Matches: <img src="#" alt="..." class="..." data-filename="filename.jpg">
    img_pattern = r'<img[^>]*data-filename=["\']([^"\']+)["\'][^>]*>'
    
    matches = re.finditer(img_pattern, content, re.IGNORECASE)
    
    for match in matches:
        filename = match.group(1)
        img_tag = match.group(0)
        
        # Extract alt text from the img tag
        alt_match = re.search(r'alt=["\']([^"\']*)["\']', img_tag, re.IGNORECASE)
        alt_text = alt_match.group(1) if alt_match else ''
        
        # Create or update BlogPostImage record
        BlogPostImage.objects.get_or_create(
            blog_post=blog_post,
            filename=filename,
            defaults={
                'alt_text': alt_text,
                'is_featured': False
            }
        )
    
    # Also create a record for featured image if it exists
    if blog_post.featured_image:
        BlogPostImage.objects.get_or_create(
            blog_post=blog_post,
            filename='featured_image',
            defaults={
                'alt_text': blog_post.featured_image_description or '',
                'image_file': blog_post.featured_image,
                'is_featured': True
            }
        )


@login_required
def blog_post_images_list(request):
    """Display list of all blog post images with upload status"""
    # Get query parameters
    status_filter = request.GET.get('status', '').strip()
    post_id = request.GET.get('post', '').strip()
    
    # Get all blog posts with their images
    blog_posts = BlogPost.objects.prefetch_related('images').select_related('post_idea').order_by('-created_at')
    
    # Collect all images
    all_images = []
    for post in blog_posts:
        # Get images from content
        content_images = post.images.filter(is_featured=False)
        for img in content_images:
            all_images.append({
                'id': img.id,
                'blog_post': post,
                'filename': img.filename,
                'alt_text': img.alt_text,
                'image_file': img.image_file,
                'is_featured': False,
                'has_file': bool(img.image_file),
            })
        
        # Add featured image if it exists or if there's a description
        if post.featured_image or post.featured_image_description:
            featured_img = post.images.filter(is_featured=True).first()
            all_images.append({
                'id': featured_img.id if featured_img else None,
                'blog_post': post,
                'filename': 'featured_image',
                'alt_text': post.featured_image_description or '',
                'image_file': post.featured_image if post.featured_image else (featured_img.image_file if featured_img else None),
                'is_featured': True,
                'has_file': bool(post.featured_image or (featured_img and featured_img.image_file)),
            })
    
    # If no filters are provided, default to oldest post with missing images
    if not post_id and not status_filter:
        # Get all blog posts ordered by created_at (oldest first)
        all_posts_ordered = BlogPost.objects.prefetch_related('images').select_related('post_idea').order_by('created_at')
        
        # Find the oldest post that has missing images
        for post in all_posts_ordered:
            # Check if this post has any missing images
            post_images = [img for img in all_images if img['blog_post'].id == post.id]
            has_missing = any(not img['has_file'] for img in post_images)
            
            if has_missing:
                post_id = str(post.id)
                all_images = post_images
                break
    
    # Filter by status if provided
    if status_filter == 'missing':
        all_images = [img for img in all_images if not img['has_file']]
    elif status_filter == 'uploaded':
        all_images = [img for img in all_images if img['has_file']]
    
    # Filter by blog post if provided
    if post_id:
        try:
            post = BlogPost.objects.get(pk=post_id)
            all_images = [img for img in all_images if img['blog_post'].id == post.id]
        except BlogPost.DoesNotExist:
            pass
    
    # Pagination
    paginator = Paginator(all_images, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Get all blog posts for filter dropdown
    all_blog_posts = BlogPost.objects.all().order_by('-created_at')
    
    # Statistics
    total_images = len(all_images)
    missing_images = len([img for img in all_images if not img['has_file']])
    uploaded_images = len([img for img in all_images if img['has_file']])
    
    context = {
        'page_obj': page_obj,
        'images': page_obj,
        'all_blog_posts': all_blog_posts,
        'status_filter': status_filter,
        'selected_post_id': post_id,
        'total_images': total_images,
        'missing_images': missing_images,
        'uploaded_images': uploaded_images,
    }
    return render(request, 'sources/blog_post_images_list.html', context)


@login_required
def blog_post_image_upload(request, pk):
    """Upload or update an image for a blog post"""
    blog_post = get_object_or_404(BlogPost, pk=pk)
    
    if request.method == 'POST':
        filename = request.POST.get('filename', '').strip()
        is_featured = request.POST.get('is_featured', 'false') == 'true'
        image_file = request.FILES.get('image_file')
        
        if not filename:
            messages.error(request, 'Filename is required.')
            return redirect('sources:blog_post_images_list')
        
        if not image_file:
            messages.error(request, 'Please select an image file to upload.')
            return redirect('sources:blog_post_images_list')
        
        try:
            if is_featured:
                # Update featured image
                blog_post.featured_image = image_file
                blog_post.save(update_fields=['featured_image'])
                
                # Update or create BlogPostImage record
                blog_post_image, created = BlogPostImage.objects.get_or_create(
                    blog_post=blog_post,
                    filename='featured_image',
                    defaults={'is_featured': True}
                )
                blog_post_image.image_file = image_file
                blog_post_image.is_featured = True
                blog_post_image.save()
                
                messages.success(request, 'Featured image uploaded successfully!')
            else:
                # Update content image
                blog_post_image = get_object_or_404(
                    BlogPostImage,
                    blog_post=blog_post,
                    filename=filename
                )
                blog_post_image.image_file = image_file
                blog_post_image.save()
                
                # Update the content to replace the image src
                import re
                old_pattern = rf'<img([^>]*data-filename=["\']{re.escape(filename)}["\'][^>]*)>'
                new_src = blog_post_image.image_file.url
                
                def replace_img_src(match):
                    img_tag = match.group(0)
                    # Replace src="#" with actual URL
                    if 'src="#"' in img_tag or 'src=\'#\'' in img_tag:
                        img_tag = re.sub(r'src=["\']#["\']', f'src="{new_src}"', img_tag)
                    elif 'src=' not in img_tag:
                        # Add src if it doesn't exist
                        img_tag = img_tag.replace('<img', f'<img src="{new_src}"')
                    else:
                        # Replace existing src
                        img_tag = re.sub(r'src=["\'][^"\']*["\']', f'src="{new_src}"', img_tag)
                    return img_tag
                
                blog_post.content = re.sub(old_pattern, replace_img_src, blog_post.content, flags=re.IGNORECASE)
                blog_post.save(update_fields=['content'])
                
                messages.success(request, f'Image "{filename}" uploaded and content updated successfully!')
            
            # Log activity
            log_activity(
                'blog_post_image_uploaded',
                f'Image "{filename}" uploaded for blog post "{blog_post.title}"',
                user=request.user,
                metadata={
                    'blog_post_id': blog_post.id,
                    'filename': filename,
                    'is_featured': is_featured
                }
            )
            
        except Exception as e:
            messages.error(request, f'Error uploading image: {str(e)}')
    
    return redirect('sources:blog_post_images_list')


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


def _parse_blog_content_sections(content):
    """
    Parse HTML blog post content into sections: intro, summary, main_content, conclusion
    
    Returns a dict with:
    - intro: Content from H1 until "Quick Summary" or "Key Takeaways" heading
    - summary_title: The heading text of the summary section
    - summary_content: The content within the summary section
    - main_content: Content between summary and conclusion
    - conclusion: The last paragraph/section
    """
    import re
    
    if not content:
        return {
            'intro': '',
            'summary_title': '',
            'summary_content': '',
            'main_content': '',
            'conclusion': ''
        }
    
    # Remove H1 if present (we don't need it in sections)
    content = re.sub(r'<h1[^>]*>.*?</h1>', '', content, flags=re.IGNORECASE | re.DOTALL)
    content = content.strip()
    
    # Find the "Quick Summary" or "Key Takeaways" section
    # Look for H2, H3, or div containing H3 with "Quick Summary" or "Key Takeaways"
    # Pattern matches "Quick Summary" even when followed by colon and more text
    
    summary_match = None
    
    # First try to find div containing H3 with "Quick Summary" (most common case)
    # Need to handle nested divs properly by finding the opening div and matching closing div
    h3_pattern = r'<h3[^>]*>([^<]*Quick\s+Summary[^<]*)</h3>'
    h3_match = re.search(h3_pattern, content, re.IGNORECASE | re.DOTALL)
    if h3_match:
        # Find the div that contains this H3
        # Look backwards from H3 to find the opening <div> tag
        h3_start = h3_match.start()
        # Find the last <div> before this H3
        div_start_match = None
        for match in re.finditer(r'<div[^>]*>', content[:h3_start], re.IGNORECASE):
            div_start_match = match
        
        if div_start_match:
            # Find the matching closing </div> after the H3
            # Count div tags to find the matching closing tag
            div_start_pos = div_start_match.start()
            div_count = 1
            pos = div_start_match.end()
            div_end_pos = -1
            
            while pos < len(content) and div_count > 0:
                next_open = content.find('<div', pos)
                next_close = content.find('</div>', pos)
                
                if next_close == -1:
                    break
                
                if next_open != -1 and next_open < next_close:
                    div_count += 1
                    pos = next_open + 4
                else:
                    div_count -= 1
                    if div_count == 0:
                        div_end_pos = next_close + 6  # Position after </div>
                        break
                    pos = next_close + 6
            
            if div_end_pos > 0:
                summary_title_raw = h3_match.group(1).strip()
                # Remove emojis from title
                summary_title_clean = re.sub(r'[\U0001F300-\U0001F9FF]|[\U00002600-\U000027BF]|[\U0001F600-\U0001F64F]|[\U0001F680-\U0001F6FF]|[\U0001F1E0-\U0001F1FF]|[\U00002700-\U000027BF]|[\U0001F900-\U0001F9FF]|[\U0001FA00-\U0001FA6F]|[\U0001FA70-\U0001FAFF]|[\U00002600-\U000026FF]|[\U00002700-\U000027BF]', '', summary_title_raw).strip()
                # Extract inner content: remove outer div wrapper and H3 tag
                # Find the H3 closing tag and extract everything after it until the div closing tag
                h3_end = h3_match.end()  # Position after </h3>
                # Extract content between H3 closing tag and div closing tag
                # div_end_pos is after </div>, so we need to go back 6 chars to get before </div>
                inner_content_start = h3_end
                inner_content_end = div_end_pos - 6  # Position before </div>
                summary_content = content[inner_content_start:inner_content_end].strip()
                # Create a match-like object
                class MatchObj:
                    def __init__(self, start, end, title, content):
                        self._start = start
                        self._end = end
                        self._title = title
                        self._content = content
                    def start(self):
                        return self._start
                    def end(self):
                        return self._end
                    def group(self, n):
                        return self._title if n == 1 else self._content
                summary_match = MatchObj(div_start_pos, div_end_pos, summary_title_clean, summary_content)
    
    # If not found in div, try H2 with "Quick Summary"
    if not summary_match:
        summary_pattern = r'<h2[^>]*>([^<]*Quick\s+Summary[^<]*)</h2>(.*?)(?=<h[2-6]|$)'
        summary_match = re.search(summary_pattern, content, re.IGNORECASE | re.DOTALL)
        
        # If not found, try H3 with "Quick Summary" (standalone, not in div)
        if not summary_match:
            summary_pattern = r'<h3[^>]*>([^<]*Quick\s+Summary[^<]*)</h3>(.*?)(?=<h[2-6]|$)'
            summary_match = re.search(summary_pattern, content, re.IGNORECASE | re.DOTALL)
        
        # If not found, try "Key Takeaways" in H2
        if not summary_match:
            summary_pattern = r'<h2[^>]*>([^<]*Key\s+Takeaways[^<]*)</h2>(.*?)(?=<h[2-6]|$)'
            summary_match = re.search(summary_pattern, content, re.IGNORECASE | re.DOTALL)
        
        # If not found, try "Key Takeaways" in H3
        if not summary_match:
            summary_pattern = r'<h3[^>]*>([^<]*Key\s+Takeaways[^<]*)</h3>(.*?)(?=<h[2-6]|$)'
            summary_match = re.search(summary_pattern, content, re.IGNORECASE | re.DOTALL)
    
    intro = ''
    summary_title = ''
    summary_content = ''
    main_content = ''
    conclusion = ''
    
    if summary_match:
        # Extract intro (everything before the summary section)
        intro_end = summary_match.start()
        intro = content[:intro_end].strip()
        
        # Extract summary title and content
        summary_title_raw = summary_match.group(1).strip()
        summary_title = re.sub(r'<[^>]+>', '', summary_title_raw).strip()  # Remove HTML tags
        # Remove emojis (common emoji ranges and symbols)
        summary_title = re.sub(r'[\U0001F300-\U0001F9FF]|[\U00002600-\U000027BF]|[\U0001F600-\U0001F64F]|[\U0001F680-\U0001F6FF]|[\U0001F1E0-\U0001F1FF]|[\U00002700-\U000027BF]|[\U0001F900-\U0001F9FF]|[\U0001FA00-\U0001FA6F]|[\U0001FA70-\U0001FAFF]|[\U00002600-\U000026FF]|[\U00002700-\U000027BF]', '', summary_title).strip()
        summary_content = summary_match.group(2).strip()
        
        # Find the end of summary section (next H2/H3 or end of content)
        summary_end = summary_match.end()
        content_after_summary = content[summary_end:].strip()
        
        # Extract conclusion: find the last paragraph in the remaining content
        # Look for the last <p> tag
        paragraph_matches = list(re.finditer(r'<p[^>]*>.*?</p>', content_after_summary, re.IGNORECASE | re.DOTALL))
        
        if paragraph_matches:
            # Last paragraph is conclusion
            last_para_match = paragraph_matches[-1]
            conclusion = last_para_match.group(0)
            # Everything before the last paragraph is main_content
            main_content = content_after_summary[:last_para_match.start()].strip()
        else:
            # No paragraphs found, try to find last section
            # Look for last block of content (could be wrapped in div, or just text)
            # Split by common block-level tags
            blocks = re.split(r'(<h[2-6][^>]*>.*?</h[2-6]>)', content_after_summary, flags=re.IGNORECASE | re.DOTALL)
            if len(blocks) > 2:
                # Last block might be conclusion
                conclusion = blocks[-1].strip()
                main_content = ''.join(blocks[:-1]).strip()
            else:
                # Can't split, put everything in main_content
                main_content = content_after_summary
                conclusion = ''
    else:
        # No summary section found
        # Try to split content: conclusion is last paragraph, intro is first paragraph(s)
        paragraph_matches = list(re.finditer(r'<p[^>]*>.*?</p>', content, re.IGNORECASE | re.DOTALL))
        
        if paragraph_matches and len(paragraph_matches) > 1:
            # First paragraph as intro
            intro = paragraph_matches[0].group(0)
            # Last paragraph as conclusion
            conclusion = paragraph_matches[-1].group(0)
            # Everything in between is main_content
            main_content = content[paragraph_matches[0].end():paragraph_matches[-1].start()].strip()
        elif paragraph_matches:
            # Only one paragraph - use as intro
            intro = paragraph_matches[0].group(0)
            main_content = ''
            conclusion = ''
        else:
            # No paragraphs found, put everything in main_content
            main_content = content
            intro = ''
            conclusion = ''
    
    return {
        'intro': intro,
        'summary_title': summary_title,
        'summary_content': summary_content,
        'main_content': main_content,
        'conclusion': conclusion
    }


def _format_acf_field(value, label, field_type='text'):
    """
    Format an ACF field with both raw value and _source object
    
    Args:
        value: The raw field value
        label: The field label
        field_type: 'text' or 'wysiwyg'
    
    Returns:
        Dict with _source object containing label, type, and formatted_value
    """
    return {
        'label': label,
        'type': field_type,
        'formatted_value': value or ''
    }


def _format_faq_acf_fields(blog_post):
    """
    Format FAQ data into individual ACF fields (question_1, answer_1, etc.)
    
    Returns a dict with all FAQ-related ACF fields
    """
    import re
    
    faq_fields = {}
    
    # FAQ title
    faq_title = blog_post.faq_title or ''
    faq_fields['faqs_title'] = faq_title
    faq_fields['faqs_title_source'] = {
        'label': 'Title',
        'type': 'text',
        'formatted_value': faq_title
    }
    
    # FAQ questions and answers (up to 4)
    faq_list = blog_post.faq if blog_post.faq and isinstance(blog_post.faq, list) else []
    
    for i in range(1, 5):  # question_1 through question_4
        index = i - 1
        if index < len(faq_list) and isinstance(faq_list[index], dict):
            question = faq_list[index].get('question', '').strip()
            answer = faq_list[index].get('answer', '').strip()
        else:
            question = ''
            answer = ''
        
        # Question field (text)
        faq_fields[f'question_{i}'] = question
        faq_fields[f'question_{i}_source'] = {
            'label': 'Title',
            'type': 'text',
            'formatted_value': question
        }
        
        # Answer field (wysiwyg)
        # Raw answer is just the text
        faq_fields[f'answer_{i}'] = answer
        
        # Formatted answer: wrap in <p> tags if not already HTML
        if answer:
            # Check if answer already contains HTML tags
            if re.search(r'<[^>]+>', answer):
                formatted_answer = answer
            else:
                # Wrap in <p> tags
                formatted_answer = f'<p>{answer}</p>'
        else:
            formatted_answer = ''
        
        faq_fields[f'answer_{i}_source'] = {
            'label': 'Content',
            'type': 'wysiwyg',
            'formatted_value': formatted_answer
        }
    
    return faq_fields


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
    - model (optional): Model name for content generation. Default: 'gemini-3-pro-preview'
    - use_rag (optional): Whether to use RAG context. Default: false
    - num_chunks (optional): Number of RAG chunks to use. Default: 5
    - metadata_provider (optional): AI provider for metadata generation. Default: same as provider
    - metadata_model (optional): Model name for metadata generation. Default: same as model
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
        provider = data.get('provider', 'gemini').strip().lower()
        model = data.get('model', 'gemini-3-pro-preview').strip()
        use_rag = data.get('use_rag', False)
        num_chunks = int(data.get('num_chunks', 5))
        
        # Optional parameters for metadata generation (default to same as content generation)
        metadata_provider = data.get('metadata_provider', provider).strip().lower()
        metadata_model = data.get('metadata_model', model).strip()
        
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
        
        # Get default models if not provided
        if not model:
            if provider == 'ollama':
                try:
                    app_settings = Settings.get_settings()
                    model = app_settings.default_tagging_model
                except Exception:
                    model = 'gpt-oss:20b-cloud'
            elif provider == 'openai':
                model = 'gpt-4o-mini'
            elif provider == 'gemini':
                model = 'gemini-3-pro-preview'
        
        if not metadata_model:
            metadata_model = model
        
        # Load prompt templates
        import os
        from django.conf import settings as django_settings
        
        prompt_file_path = os.path.join(django_settings.BASE_DIR, 'prompt-post-generation.md')
        metadata_prompt_path = os.path.join(django_settings.BASE_DIR, 'prompt-metadata-generator')
        
        try:
            with open(prompt_file_path, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
        except FileNotFoundError:
            return JsonResponse({
                'success': False,
                'error': 'Prompt template file not found: prompt-post-generation.md'
            }, status=500)
        
        try:
            with open(metadata_prompt_path, 'r', encoding='utf-8') as f:
                metadata_prompt_template = f.read()
        except FileNotFoundError:
            return JsonResponse({
                'success': False,
                'error': 'Metadata prompt template file not found: prompt-metadata-generator'
            }, status=500)
        
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
        # Extract meta title
        meta_title = None
        patterns = [
            r'\*\*Meta Title:\*\*\s*(.+?)(?:\n|$)',
            r'Meta Title:\s*(.+?)(?:\n|$)',
            r'Title:\s*(.+?)(?:\n|$)',
        ]
        for pattern in patterns:
            match = re.search(pattern, generated_metadata, re.IGNORECASE)
            if match:
                meta_title = re.sub(r'\*\*|\*|\[|\]|`', '', match.group(1).strip())
                if meta_title and len(meta_title) <= 60:
                    blog_post.meta_title = meta_title
                    break
        
        # Extract meta description
        meta_description = None
        patterns = [
            r'\*\*Meta Description:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
            r'Meta Description:\s*(.+?)(?=\n\*\*|\n\n|$)',
            r'Description:\s*(.+?)(?=\n\*\*|\n\n|$)',
        ]
        for pattern in patterns:
            match = re.search(pattern, generated_metadata, re.IGNORECASE | re.DOTALL)
            if match:
                meta_description = re.sub(r'\*\*|\*|\[|\]|`', '', match.group(1).strip())
                meta_description = ' '.join(meta_description.split())
                if meta_description and len(meta_description) <= 160:
                    blog_post.meta_description = meta_description
                    break
        
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
