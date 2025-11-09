"""
Management command to automatically tag content using LLM (Ollama or OpenAI)
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from sources.models import Content, Tag
from sources.services import TaggingService
import time


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
            '--skip-tagged',
            action='store_true',
            help='Skip content that already has tags',
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
            '--batch-size',
            type=int,
            default=10,
            help='Number of items to process before showing progress (default: 10)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be tagged without actually saving',
        )
    
    def handle(self, *args, **options):
        provider = options['provider']
        model = options['model']
        limit = options['limit']
        skip_tagged = options['skip_tagged']
        has_content_only = options['has_content_only']
        source_id = options['source']
        batch_size = options['batch_size']
        dry_run = options['dry_run']
        
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
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be saved'))
        self.stdout.write('')
        
        # Process content
        tagged_count = 0
        skipped_count = 0
        error_count = 0
        tags_created = 0
        
        start_time = time.time()
        
        for idx, content in enumerate(queryset, 1):
            try:
                # Generate tags
                content_text = content.content if hasattr(content, 'content') else ""
                generated_tags = service.generate_tags(
                    title=content.title,
                    content=content_text,
                    content_type=content.content_type
                )
                
                if not generated_tags:
                    self.stdout.write(
                        self.style.WARNING(f'  [{idx}/{total}] No tags generated for: {content.title[:50]}')
                    )
                    skipped_count += 1
                    continue
                
                # Get or create tag objects
                tag_objects = []
                for tag_name in generated_tags:
                    tag, created = Tag.objects.get_or_create(
                        name=tag_name
                    )
                    if created:
                        tags_created += 1
                    tag_objects.append(tag)
                
                if not dry_run:
                    # Save tags to content
                    with transaction.atomic():
                        content.tags.set(tag_objects)
                
                self.stdout.write(
                    self.style.SUCCESS(
                        f'  [{idx}/{total}] Tagged: {content.title[:50]} '
                        f'→ {", ".join(generated_tags)}'
                    )
                )
                tagged_count += 1
                
                # Show progress every batch_size items
                if idx % batch_size == 0:
                    elapsed = time.time() - start_time
                    rate = idx / elapsed if elapsed > 0 else 0
                    remaining = (total - idx) / rate if rate > 0 else 0
                    self.stdout.write(
                        f'\n  Progress: {idx}/{total} ({idx*100//total}%) | '
                        f'Rate: {rate:.1f}/min | ETA: {remaining/60:.1f} min\n'
                    )
                
                # Small delay to avoid overwhelming the API
                if provider == 'ollama':
                    time.sleep(0.5)  # Ollama can handle requests quickly
                elif provider == 'openai':
                    time.sleep(0.1)  # OpenAI has rate limits
                
            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING('\n\nInterrupted by user'))
                break
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'  [{idx}/{total}] Error tagging "{content.title[:50]}": {str(e)}')
                )
                error_count += 1
                continue
        
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

