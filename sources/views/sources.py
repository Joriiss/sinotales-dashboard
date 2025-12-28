from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.db.models import Count, Q
from ..models import Source, Content, Tag, ContentChunk
from ..forms import SourceForm
from ..utils import log_activity
from ..youtube_service import get_channel_videos, is_video_relevant_to_china
from ..content_extraction_service import extract_article_content
from ..content_processing_service import ContentProcessingService
from ..embedding_service import EmbeddingService
import threading
from django.db import connection, transaction
from django.utils import timezone
import requests
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
import re
import json
from datetime import datetime
import os

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