"""
Management command to find and delete blog posts that exist in the database
but not in the CSV file.
Usage: python manage.py cleanup_posts posts.csv [--dry-run]
"""
import csv
import os
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from sources.models import Source, Content


class Command(BaseCommand):
    help = 'Find and delete blog posts that exist in DB but not in CSV file'

    def add_arguments(self, parser):
        parser.add_argument(
            'csv_file',
            type=str,
            help='Path to the posts CSV file'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting (default behavior)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Actually delete the posts (required to perform deletion)',
        )
        parser.add_argument(
            '--source',
            type=str,
            default=None,
            help='Only check posts from a specific source name',
        )

    def handle(self, *args, **options):
        csv_file = options['csv_file']
        # Default to dry-run unless --force is specified
        dry_run = not options['force']
        source_filter = options['source']
        
        # Resolve file path
        if not os.path.isabs(csv_file):
            # First, try as-is relative to current working directory
            csv_file_abs = os.path.abspath(csv_file)
            
            # If not found, try relative to project root (where manage.py is)
            if not os.path.exists(csv_file_abs):
                project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
                csv_file_from_root = os.path.join(project_root, csv_file)
                if os.path.exists(csv_file_from_root):
                    csv_file_abs = os.path.abspath(csv_file_from_root)
            
            csv_file = csv_file_abs
        
        if not os.path.exists(csv_file):
            raise CommandError(
                f'CSV file not found: {csv_file}\n'
                f'Please provide an absolute path or a path relative to the current directory.\n'
                f'Example: python manage.py cleanup_posts posts.csv'
            )
        
        self.stdout.write(f'Reading posts from: {csv_file}')
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No posts will be deleted'))
        else:
            self.stdout.write(self.style.ERROR('LIVE MODE - Posts will be DELETED!'))
        
        # Read CSV and build set of (source_name, external_id) tuples
        csv_posts = set()
        source_names_in_csv = set()
        
        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                # Normalize column names (strip whitespace)
                fieldnames = [name.strip() for name in reader.fieldnames] if reader.fieldnames else []
                
                for row_num, row in enumerate(reader, start=2):  # Start at 2 because row 1 is header
                    try:
                        # Normalize row keys
                        normalized_row = {k.strip(): v for k, v in row.items()}
                        
                        post_id = normalized_row.get('id', '').strip()
                        source_name = normalized_row.get('source', '').strip()
                        
                        if not post_id or not source_name:
                            continue
                        
                        # Apply source filter if specified
                        if source_filter and source_name != source_filter:
                            continue
                        
                        csv_posts.add((source_name, post_id))
                        source_names_in_csv.add(source_name)
                        
                    except Exception as e:
                        self.stdout.write(
                            self.style.WARNING(f'  Row {row_num}: Error reading row - {str(e)}')
                        )
                        continue
        
        except Exception as e:
            raise CommandError(f'Error reading CSV file: {str(e)}')
        
        self.stdout.write(f'\nFound {len(csv_posts)} posts in CSV file')
        if source_names_in_csv:
            self.stdout.write(f'Sources in CSV: {", ".join(sorted(source_names_in_csv))}')
        if source_filter:
            self.stdout.write(self.style.SUCCESS(f'Filtering by source: {source_filter}'))
        
        # Find all blog posts in database
        db_queryset = Content.objects.filter(
            content_type='blog_post'
        ).select_related('source')
        
        # Apply source filter if specified
        if source_filter:
            db_queryset = db_queryset.filter(source__name=source_filter)
        else:
            # Only check sources that appear in CSV
            db_queryset = db_queryset.filter(source__name__in=source_names_in_csv)
        
        # Find posts that are NOT in CSV
        posts_to_delete = []
        
        for content in db_queryset:
            source_name = content.source.name
            external_id = content.external_id
            
            # Check if this post exists in CSV
            if (source_name, external_id) not in csv_posts:
                posts_to_delete.append(content)
        
        # Display results
        self.stdout.write(f'\nFound {len(posts_to_delete)} blog post(s) in database that are NOT in CSV:')
        self.stdout.write('=' * 80)
        
        if not posts_to_delete:
            self.stdout.write(self.style.SUCCESS('No posts to delete! Database is in sync with CSV.'))
            return
        
        # Group by source for better display
        from collections import defaultdict
        by_source = defaultdict(list)
        for post in posts_to_delete:
            by_source[post.source.name].append(post)
        
        for source_name in sorted(by_source.keys()):
            posts = by_source[source_name]
            self.stdout.write(f'\n{source_name} ({len(posts)} post(s)):')
            for post in posts[:10]:  # Show first 10 per source
                self.stdout.write(f'  - ID: {post.external_id} | Title: {post.title[:60]}')
            if len(posts) > 10:
                self.stdout.write(f'  ... and {len(posts) - 10} more')
        
        # Summary
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(f'Total posts to delete: {len(posts_to_delete)}')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN - No posts were deleted'))
            self.stdout.write(self.style.WARNING('Run with --force to actually delete these posts'))
        else:
            # Confirm deletion
            self.stdout.write(self.style.ERROR('\n⚠️  WARNING: About to DELETE these posts!'))
            
            # Delete posts
            deleted_count = 0
            deleted_chunks = 0
            
            with transaction.atomic():
                for post in posts_to_delete:
                    # Count chunks that will be deleted (CASCADE)
                    chunks_count = post.chunks.count()
                    deleted_chunks += chunks_count
                    
                    # Delete the post (chunks will be deleted via CASCADE)
                    post.delete()
                    deleted_count += 1
            
            self.stdout.write(self.style.SUCCESS(f'\n✓ Successfully deleted {deleted_count} post(s)'))
            if deleted_chunks > 0:
                self.stdout.write(self.style.SUCCESS(f'✓ Also deleted {deleted_chunks} associated chunk(s)'))

