"""
Management command to automatically tag content using LLM (Ollama or OpenAI)
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from sources.models import Content, Tag
from sources.services import TaggingService
import time
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, local


class Command(BaseCommand):
    help = 'Automatically tag content using LLM (Ollama or OpenAI)'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--provider',
            type=str,
            default='ollama',
            choices=['ollama', 'openai'],
            help='LLM provider to use (default: ollama)',
        )
        parser.add_argument(
            '--model',
            type=str,
            default=None,
            help='Model name (e.g., 2 for Ollama, gpt-3.5-turbo for OpenAI)',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit number of content items to process',
        )
        parser.add_argument(
            '--re-tag',
            action='store_true',
            help='Re-tag content even if it already has tags (default: skips content with existing tags)',
        )
        parser.add_argument(
            '--has-content-only',
            action='store_true',
            help='Only tag content that has text content',
        )
        parser.add_argument(
            '--source',
            type=int,
            default=None,
            help='Only tag content from specific source ID',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be tagged without actually saving',
        )
        parser.add_argument(
            '--workers',
            type=int,
            default=1,
            help='Number of parallel workers for processing (default: 1, recommended: 2-4 for Ollama, 3-5 for OpenAI)',
        )
        parser.add_argument(
            '--delay',
            type=float,
            default=None,
            help='Delay between requests in seconds (default: 0.5 for Ollama, 0.1 for OpenAI)',
        )
    
    def handle(self, *args, **options):
        provider = options['provider']
        model = options['model']
        limit = options['limit']
        skip_tagged = not options['re_tag']  # Skip tagged by default unless --re-tag is used
        has_content_only = options['has_content_only']
        source_id = options['source']
        dry_run = options['dry_run']
        workers = max(1, options['workers'])  # At least 1 worker
        delay = options['delay']
        
        # Initialize tagging service
        try:
            service = TaggingService(provider=provider, model=model)
            self.stdout.write(
                self.style.SUCCESS(f'✓ Initialized {provider.upper()} service with model: {service.model}')
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'✗ Failed to initialize tagging service: {str(e)}')
            )
            return
        
        # Build query
        queryset = Content.objects.select_related('source').prefetch_related('tags')
        
        # Skip content that already has tags by default (unless --re-tag is used)
        if skip_tagged:
            queryset = queryset.filter(tags__isnull=True)
        
        if has_content_only:
            queryset = queryset.filter(has_content=True)
        
        if source_id:
            queryset = queryset.filter(source_id=source_id)
        
        if limit:
            queryset = queryset[:limit]
        
        total = queryset.count()
        
        if total == 0:
            self.stdout.write(self.style.WARNING('No content found to tag.'))
            return
        
        self.stdout.write(f'\nFound {total} content item(s) to tag')
        if skip_tagged:
            self.stdout.write(self.style.WARNING('Note: Skipping content that already has tags'))
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be saved'))
        if workers > 1:
            self.stdout.write(self.style.SUCCESS(f'Using {workers} parallel workers for faster processing'))
        self.stdout.write('')
        
        # Set default delay if not specified
        if delay is None:
            delay = 0.2 if provider == 'ollama' else 0.05  # Reduced delays
        
        # Process content with progress bar
        tagged_count = 0
        skipped_count = 0
        error_count = 0
        tags_created = 0
        
        # Thread-safe counters
        counters_lock = Lock()
        
        # Thread-local storage for service instances (each thread gets its own)
        thread_local = local()
        
        def get_service():
            """Get or create service instance for current thread"""
            if not hasattr(thread_local, 'service'):
                thread_local.service = TaggingService(provider=provider, model=model)
            return thread_local.service
        
        def process_content(content):
            """Process a single content item"""
            nonlocal tagged_count, skipped_count, error_count, tags_created
            
            # Get service instance for this thread
            thread_service = get_service()
            
            try:
                # Check if content already has tags (double-check in case query filter didn't work)
                if skip_tagged and content.tags.exists():
                    with counters_lock:
                        skipped_count += 1
                    return {
                        'status': 'skipped',
                        'title': content.title[:50],
                        'reason': 'has tags'
                    }
                
                # Generate tags
                content_text = content.content if hasattr(content, 'content') else ""
                generated_tags = thread_service.generate_tags(
                    title=content.title,
                    content=content_text,
                    content_type=content.content_type
                )
                
                if not generated_tags:
                    with counters_lock:
                        skipped_count += 1
                    return {
                        'status': 'skipped',
                        'title': content.title[:50],
                        'reason': 'no tags generated'
                    }
                
                # Batch fetch existing tags to avoid individual queries
                existing_tags = {tag.name: tag for tag in Tag.objects.filter(name__in=generated_tags)}
                
                # Get or create tag objects
                tag_objects = []
                new_tags_count = 0
                for tag_name in generated_tags:
                    if tag_name in existing_tags:
                        tag_objects.append(existing_tags[tag_name])
                    else:
                        tag, created = Tag.objects.get_or_create(name=tag_name)
                        tag_objects.append(tag)
                        if created:
                            new_tags_count += 1
                            existing_tags[tag_name] = tag  # Cache for potential duplicates in same batch
                
                if not dry_run:
                    # Save tags to content
                    with transaction.atomic():
                        content.tags.set(tag_objects)
                
                with counters_lock:
                    tagged_count += 1
                    tags_created += new_tags_count
                
                return {
                    'status': 'tagged',
                    'title': content.title[:50],
                    'tags': generated_tags
                }
                
            except Exception as e:
                with counters_lock:
                    error_count += 1
                return {
                    'status': 'error',
                    'title': content.title[:50] if hasattr(content, 'title') else 'unknown',
                    'error': str(e)[:50]
                }
        
        start_time = time.time()
        
        # Use tqdm for progress bar
        with tqdm(total=total, desc="Tagging content", unit="item", ncols=120) as pbar:
            if workers == 1:
                # Sequential processing (simpler, no threading overhead)
                # Use the main service instance directly
                thread_local.service = service
                for content in queryset:
                    result = process_content(content)
                    
                    if result['status'] == 'tagged':
                        pbar.set_postfix({
                            "status": "tagged",
                            "tags": ", ".join(result['tags'][:2]) + ("..." if len(result['tags']) > 2 else ""),
                            "title": result['title'][:20]
                        })
                    elif result['status'] == 'skipped':
                        pbar.set_postfix({"status": f"skipped ({result['reason']})", "title": result['title'][:30]})
                    else:
                        pbar.set_postfix({"status": f"error: {result.get('error', 'unknown')[:20]}", "title": result['title'][:20]})
                    
                    pbar.update(1)
                    
                    # Small delay to avoid overwhelming the API
                    if delay > 0:
                        time.sleep(delay)
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
                        
                        if result['status'] == 'tagged':
                            pbar.set_postfix({
                                "status": "tagged",
                                "tags": ", ".join(result['tags'][:2]) + ("..." if len(result['tags']) > 2 else ""),
                                "title": result['title'][:20]
                            })
                        elif result['status'] == 'skipped':
                            pbar.set_postfix({"status": f"skipped ({result['reason']})", "title": result['title'][:30]})
                        else:
                            pbar.set_postfix({"status": f"error: {result.get('error', 'unknown')[:20]}", "title": result['title'][:20]})
                        
                        pbar.update(1)
        
        # Summary
        elapsed = time.time() - start_time
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS('SUMMARY'))
        self.stdout.write('='*60)
        self.stdout.write(f'Total processed: {total}')
        self.stdout.write(f'Successfully tagged: {tagged_count}')
        self.stdout.write(f'Skipped: {skipped_count}')
        self.stdout.write(f'Errors: {error_count}')
        self.stdout.write(f'New tags created: {tags_created}')
        self.stdout.write(f'Time elapsed: {elapsed/60:.1f} minutes')
        if tagged_count > 0:
            self.stdout.write(f'Average time per item: {elapsed/tagged_count:.1f} seconds')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN - No changes were saved'))
        else:
            self.stdout.write(self.style.SUCCESS('\n✓ Tagging complete!'))

