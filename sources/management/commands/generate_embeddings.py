"""
Management command to generate embeddings for content using OpenAI
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from sources.models import Content, ContentChunk
from sources.embedding_service import EmbeddingService
from sources.utils import log_activity
import time
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, local


class Command(BaseCommand):
    help = 'Generate embeddings for content using OpenAI'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit number of content items to process',
        )
        parser.add_argument(
            '--skip-embedded',
            action='store_true',
            help='Skip content that already has embeddings (chunks exist)',
        )
        parser.add_argument(
            '--has-content-only',
            action='store_true',
            help='Only process content that has text content',
        )
        parser.add_argument(
            '--source',
            type=int,
            default=None,
            help='Only process content from specific source ID',
        )
        parser.add_argument(
            '--chunk-size',
            type=int,
            default=8000,
            help='Maximum characters per chunk (default: 8000)',
        )
        parser.add_argument(
            '--overlap',
            type=int,
            default=200,
            help='Overlap between chunks in characters (default: 200)',
        )
        parser.add_argument(
            '--workers',
            type=int,
            default=1,
            help='Number of parallel workers for processing (default: 1, recommended: 2-3 for OpenAI)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be processed without actually saving',
        )
    
    def handle(self, *args, **options):
        limit = options['limit']
        skip_embedded = options['skip_embedded']
        has_content_only = options['has_content_only']
        source_id = options['source']
        chunk_size = options['chunk_size']
        overlap = options['overlap']
        workers = max(1, options['workers'])
        dry_run = options['dry_run']
        
        # Initialize embedding service
        try:
            service = EmbeddingService()
            self.stdout.write(
                self.style.SUCCESS(f'✓ Initialized OpenAI embedding service (model: {service.model})')
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ Failed to initialize embedding service: {str(e)}')
            )
            return
        
        # Build query
        queryset = Content.objects.select_related('source').prefetch_related('tags', 'chunks')
        
        if skip_embedded:
            # Only get content that doesn't have any chunks yet
            queryset = queryset.filter(chunks__isnull=True)
        
        # Only process content that has content text
        queryset = queryset.filter(has_content=True)
        
        # Only process content that has at least one tag
        queryset = queryset.filter(tags__isnull=False).distinct()
        
        if source_id:
            queryset = queryset.filter(source_id=source_id)
        
        if limit:
            queryset = queryset[:limit]
        
        total = queryset.count()
        
        if total == 0:
            self.stdout.write(self.style.WARNING('No content found to process.'))
            return
        
        self.stdout.write(f'\nFound {total} content item(s) to process')
        self.stdout.write(self.style.SUCCESS('Note: Only processing content with both text content and tags'))
        if skip_embedded:
            self.stdout.write(self.style.WARNING('Note: Skipping content that already has embeddings'))
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be saved'))
        if workers > 1:
            self.stdout.write(self.style.SUCCESS(f'Using {workers} parallel workers for faster processing'))
        self.stdout.write('')
        
        # Process content
        processed_count = 0
        skipped_count = 0
        error_count = 0
        total_chunks = 0
        
        # Thread-safe counters
        counters_lock = Lock()
        
        # Thread-local storage for service instances
        thread_local = local()
        
        def get_service():
            """Get or create service instance for current thread"""
            if not hasattr(thread_local, 'service'):
                thread_local.service = EmbeddingService()
            return thread_local.service
        
        def process_content(content):
            """Process a single content item"""
            nonlocal processed_count, skipped_count, error_count, total_chunks
            
            try:
                # Check if content already has chunks (double-check)
                if skip_embedded and content.chunks.exists():
                    with counters_lock:
                        skipped_count += 1
                    return {
                        'status': 'skipped',
                        'title': content.title[:50],
                        'reason': 'has embeddings',
                        'chunks': 0
                    }
                
                # Get tags
                tags = list(content.tags.values_list('name', flat=True))
                
                # Skip if no tags (safety check)
                if not tags:
                    with counters_lock:
                        skipped_count += 1
                    return {
                        'status': 'skipped',
                        'title': content.title[:50],
                        'reason': 'no tags',
                        'chunks': 0
                    }
                
                # Get content text
                content_text = content.content if hasattr(content, 'content') else ""
                
                # Skip if no content text (safety check)
                if not content_text or not content_text.strip():
                    with counters_lock:
                        skipped_count += 1
                    return {
                        'status': 'skipped',
                        'title': content.title[:50],
                        'reason': 'no content text',
                        'chunks': 0
                    }
                
                # Get service for this thread
                thread_service = get_service()
                
                # Generate embeddings for all chunks
                chunk_results = thread_service.generate_embeddings_for_content(
                    title=content.title,
                    content_text=content_text,
                    tags=tags,
                    chunk_size=chunk_size,
                    overlap=overlap
                )
                
                if not chunk_results:
                    with counters_lock:
                        skipped_count += 1
                    return {
                        'status': 'skipped',
                        'title': content.title[:50],
                        'reason': 'no embeddings generated',
                        'chunks': 0
                    }
                
                num_chunks = len(chunk_results)
                
                if not dry_run:
                    # Delete existing chunks if any (for re-processing)
                    content.chunks.all().delete()
                    
                    # Create chunks with embeddings
                    with transaction.atomic():
                        chunks_to_create = []
                        for idx, (chunk_text, embedding) in enumerate(chunk_results):
                            if embedding:  # Only create chunks with valid embeddings
                                chunks_to_create.append(
                                    ContentChunk(
                                        content=content,
                                        chunk_index=idx,
                                        text=chunk_text,
                                        embedding=embedding
                                    )
                                )
                        
                        # Bulk create chunks
                        if chunks_to_create:
                            ContentChunk.objects.bulk_create(chunks_to_create)
                            
                            # Mark content as processed
                            content.processed = True
                            content.save(update_fields=['processed'])
                
                with counters_lock:
                    processed_count += 1
                    total_chunks += num_chunks
                
                return {
                    'status': 'processed',
                    'title': content.title[:50],
                    'chunks': num_chunks
                }
                
            except Exception as e:
                with counters_lock:
                    error_count += 1
                return {
                    'status': 'error',
                    'title': content.title[:50] if hasattr(content, 'title') else 'unknown',
                    'error': str(e)[:50],
                    'chunks': 0
                }
        
        start_time = time.time()
        
        # Use tqdm for progress bar
        with tqdm(total=total, desc="Generating embeddings", unit="item", ncols=120) as pbar:
            if workers == 1:
                # Sequential processing
                thread_local.service = service
                for content in queryset:
                    result = process_content(content)
                    
                    if result['status'] == 'processed':
                        pbar.set_postfix({
                            "status": "embedded",
                            "chunks": result['chunks'],
                            "title": result['title'][:20]
                        })
                    elif result['status'] == 'skipped':
                        pbar.set_postfix({
                            "status": f"skipped ({result['reason']})",
                            "title": result['title'][:30]
                        })
                    else:
                        pbar.set_postfix({
                            "status": f"error: {result.get('error', 'unknown')[:20]}",
                            "title": result['title'][:20]
                        })
                    
                    pbar.update(1)
                    
                    # Small delay to avoid rate limits
                    time.sleep(0.1)
            else:
                # Parallel processing with thread pool
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    # Submit all tasks
                    future_to_content = {
                        executor.submit(process_content, content): content 
                        for content in queryset
                    }
                    
                    # Process completed tasks as they finish
                    for future in as_completed(future_to_content):
                        result = future.result()
                        
                        if result['status'] == 'processed':
                            pbar.set_postfix({
                                "status": "embedded",
                                "chunks": result['chunks'],
                                "title": result['title'][:20]
                            })
                        elif result['status'] == 'skipped':
                            pbar.set_postfix({
                                "status": f"skipped ({result['reason']})",
                                "title": result['title'][:30]
                            })
                        else:
                            pbar.set_postfix({
                                "status": f"error: {result.get('error', 'unknown')[:20]}",
                                "title": result['title'][:20]
                            })
                        
                        pbar.update(1)
        
        # Summary
        elapsed = time.time() - start_time
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS('SUMMARY'))
        self.stdout.write('='*60)
        self.stdout.write(f'Total processed: {total}')
        self.stdout.write(f'Successfully embedded: {processed_count}')
        self.stdout.write(f'Total chunks created: {total_chunks}')
        self.stdout.write(f'Skipped: {skipped_count}')
        self.stdout.write(f'Errors: {error_count}')
        self.stdout.write(f'Time elapsed: {elapsed/60:.1f} minutes')
        if processed_count > 0:
            self.stdout.write(f'Average time per item: {elapsed/processed_count:.1f} seconds')
            self.stdout.write(f'Average chunks per item: {total_chunks/processed_count:.1f}')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN - No changes were saved'))
        else:
            self.stdout.write(self.style.SUCCESS('\n✓ Embedding generation complete!'))
            # Log the activity
            if processed_count > 0:
                log_activity(
                    'embeddings_generated',
                    f'Generated embeddings for {processed_count} content items ({total_chunks} chunks total)',
                    metadata={
                        'processed_count': processed_count,
                        'total_chunks': total_chunks,
                        'skipped_count': skipped_count,
                        'error_count': error_count,
                    }
                )

