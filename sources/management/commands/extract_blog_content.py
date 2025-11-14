"""
Management command to extract content from blog post URLs.
Fetches content from URLs for blog posts that don't have content yet.
Usage: python manage.py extract_blog_content [--source SOURCE] [--force] [--use-proxy] [--limit N]
"""
from django.core.management.base import BaseCommand
from django.db.models import Q
from sources.models import Content
from sources.content_processing_service import ContentProcessingService


class Command(BaseCommand):
    help = 'Extract content from blog post URLs'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source',
            type=str,
            default=None,
            help='Only extract content for posts from a specific source name',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Re-extract content even if it already exists',
        )
        parser.add_argument(
            '--use-proxy',
            action='store_true',
            help='Use Webshare proxies for fetching content (helps bypass Cloudflare)',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit the number of posts to process',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be extracted without actually extracting',
        )

    def handle(self, *args, **options):
        source_name = options.get('source')
        force = options.get('force', False)
        use_proxy = options.get('use_proxy', False)
        limit = options.get('limit')
        dry_run = options.get('dry_run', False)
        
        self.stdout.write(self.style.SUCCESS('\n' + '=' * 60))
        self.stdout.write(self.style.SUCCESS('Extract Blog Post Content'))
        self.stdout.write(self.style.SUCCESS('=' * 60 + '\n'))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No content will be extracted\n'))
        
        # Build query for blog posts
        query = Q(content_type='blog_post')
        
        # Filter by source if specified
        if source_name:
            query &= Q(source__name__icontains=source_name)
            self.stdout.write(f'Filtering by source: {source_name}\n')
        
        # Filter by content existence
        if force:
            # Include all blog posts (will re-extract)
            self.stdout.write('Mode: Force re-extraction (will update existing content)\n')
        else:
            # Only posts without content
            query &= (Q(content__isnull=True) | Q(content='') | Q(content__regex=r'^\s*$'))
            self.stdout.write('Mode: Extract only posts without content\n')
        
        # Must have a link
        query &= Q(link__isnull=False) & ~Q(link='')
        
        # Get posts to process
        posts = Content.objects.filter(query).order_by('-date', 'id')
        
        total_count = posts.count()
        self.stdout.write(f'Found {total_count} blog post(s) to process\n')
        
        if limit:
            posts = posts[:limit]
            self.stdout.write(f'Limited to {len(posts)} post(s)\n')
        
        if not posts.exists():
            self.stdout.write(self.style.WARNING('No posts found to process'))
            return
        
        if dry_run:
            self.stdout.write('Posts that would be processed:')
            for post in posts[:20]:  # Show first 20
                self.stdout.write(f'  - {post.title[:60]}... ({post.source.name})')
            if posts.count() > 20:
                self.stdout.write(f'  ... and {posts.count() - 20} more')
            return
        
        # Initialize processing service
        self.stdout.write('Initializing content processing service...')
        if use_proxy:
            self.stdout.write('  Using proxy support')
        processing_service = ContentProcessingService(use_proxy=use_proxy)
        
        # Process each post
        self.stdout.write(f'\nProcessing {len(posts)} post(s)...\n')
        
        success_count = 0
        failed_count = 0
        skipped_count = 0
        
        for idx, post in enumerate(posts, 1):
            self.stdout.write(f'[{idx}/{len(posts)}] {post.title[:60]}...')
            self.stdout.write(f'  Source: {post.source.name}')
            self.stdout.write(f'  URL: {post.link[:80]}...')
            
            try:
                # Check if already has content (unless force)
                if not force and post.content and post.content.strip():
                    self.stdout.write(self.style.WARNING('  ⚠️  Skipped: Already has content (use --force to re-extract)'))
                    skipped_count += 1
                    continue
                
                # Extract content
                extracted = processing_service.extract_content(post, force=force)
                
                if extracted:
                    # Refresh to get updated content
                    post.refresh_from_db()
                    content_length = len(post.content) if post.content else 0
                    self.stdout.write(self.style.SUCCESS(f'  ✅ Success: Extracted {content_length} characters'))
                    success_count += 1
                else:
                    self.stdout.write(self.style.ERROR('  ❌ Failed: Could not extract content'))
                    failed_count += 1
                    
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ❌ Error: {str(e)}'))
                failed_count += 1
            
            self.stdout.write('')  # Blank line between posts
        
        # Summary
        self.stdout.write(self.style.SUCCESS('\n' + '=' * 60))
        self.stdout.write(self.style.SUCCESS('Summary:'))
        self.stdout.write(self.style.SUCCESS(f'  Total processed: {len(posts)}'))
        self.stdout.write(self.style.SUCCESS(f'  ✅ Success: {success_count}'))
        self.stdout.write(self.style.ERROR(f'  ❌ Failed: {failed_count}'))
        if skipped_count > 0:
            self.stdout.write(self.style.WARNING(f'  ⚠️  Skipped: {skipped_count}'))
        self.stdout.write(self.style.SUCCESS('=' * 60 + '\n'))

