"""
Management command to update blog post content from content files.
Finds posts by matching filename to external_id, updates content,
sets processed=False, clears tags, and deletes chunks.
Usage: python manage.py update_posts_content [content_dir]
"""
import os
import re
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from sources.models import Source, Content, ContentChunk


class Command(BaseCommand):
    help = 'Update blog post content from content files'

    def add_arguments(self, parser):
        parser.add_argument(
            'content_dir',
            type=str,
            nargs='?',
            default='content',
            help='Path to content directory (default: content)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without actually updating',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Actually update the posts (overrides --dry-run)',
        )
        parser.add_argument(
            '--source',
            type=str,
            default=None,
            help='Only update posts from a specific source name',
        )

    def extract_content_from_file(self, file_path):
        """
        Extract the actual content from a content file.
        Content files have a header section separated by '=' characters,
        then the actual content.
        Returns: (content_text, header_info_dict)
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Parse header (lines before separator)
            header_info = {}
            separator_index = None
            
            for i, line in enumerate(lines):
                # Check for separator line
                if '=' * 20 in line or '=' * 40 in line or '=' * 60 in line or '=' * 80 in line:
                    separator_index = i
                    break
                
                # Parse header fields
                if ':' in line:
                    key, value = line.split(':', 1)
                    header_info[key.strip().lower()] = value.strip()
            
            if separator_index is not None:
                # Content starts after the separator line
                content_lines = lines[separator_index + 1:]
            else:
                # No separator found, use all lines
                content_lines = lines
            
            # Join and strip
            content = ''.join(content_lines).strip()
            return content, header_info
        
        except Exception as e:
            raise CommandError(f'Error reading file {file_path}: {str(e)}')

    def parse_filename(self, filename):
        """
        Parse filename to extract external_id and optionally source identifier.
        Format: {source-slug}_{external_id}.txt
        Returns: (external_id, source_slug)
        """
        # Remove .txt extension
        base_name = os.path.splitext(filename)[0]
        
        # Split by underscore - last part should be external_id
        parts = base_name.split('_')
        
        if len(parts) < 2:
            # Try to extract external_id from the end (hex string pattern)
            # Look for a pattern like: abc123def456 (hex characters)
            match = re.search(r'([a-f0-9]{12,})$', base_name, re.IGNORECASE)
            if match:
                external_id = match.group(1)
                source_slug = base_name[:match.start()].rstrip('_')
            else:
                # Assume the whole thing is external_id
                external_id = base_name
                source_slug = None
        else:
            # Last part is external_id, everything before is source slug
            external_id = parts[-1]
            source_slug = '_'.join(parts[:-1])
        
        return external_id, source_slug

    def find_content_by_external_id(self, external_id, source_filter=None):
        """
        Find Content object by external_id, optionally filtered by source.
        """
        queryset = Content.objects.filter(
            external_id=external_id,
            content_type='blog_post'
        ).select_related('source')
        
        if source_filter:
            queryset = queryset.filter(source__name=source_filter)
        
        # If multiple found, prefer exact source match if source_slug was provided
        contents = list(queryset)
        
        if len(contents) == 0:
            return None
        elif len(contents) == 1:
            return contents[0]
        else:
            # Multiple matches - return the first one
            # User can use --source to narrow down
            return contents[0]

    def handle(self, *args, **options):
        content_dir = options['content_dir']
        dry_run = not options['force']
        source_filter = options['source']
        
        # Resolve content directory path
        if not os.path.isabs(content_dir):
            # Try relative to current working directory
            content_dir_abs = os.path.abspath(content_dir)
            
            # If not found, try relative to project root
            if not os.path.exists(content_dir_abs):
                project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
                content_dir_from_root = os.path.join(project_root, content_dir)
                if os.path.exists(content_dir_from_root):
                    content_dir_abs = os.path.abspath(content_dir_from_root)
                else:
                    raise CommandError(
                        f'Content directory not found: {content_dir}\n'
                        f'Tried: {content_dir_abs}\n'
                        f'Tried: {content_dir_from_root}'
                    )
            
            content_dir = content_dir_abs
        
        if not os.path.exists(content_dir):
            raise CommandError(f'Content directory not found: {content_dir}')
        
        if not os.path.isdir(content_dir):
            raise CommandError(f'Path is not a directory: {content_dir}')
        
        self.stdout.write(f'Scanning content directory: {content_dir}')
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No posts will be updated'))
        else:
            self.stdout.write(self.style.SUCCESS('LIVE MODE - Posts will be updated!'))
        if source_filter:
            self.stdout.write(self.style.SUCCESS(f'Filtering by source: {source_filter}'))
        
        # Find all .txt files
        txt_files = [f for f in os.listdir(content_dir) if f.lower().endswith('.txt')]
        
        if not txt_files:
            self.stdout.write(self.style.WARNING('No .txt files found in content directory'))
            return
        
        self.stdout.write(f'\nFound {len(txt_files)} content file(s)')
        
        # Process each file
        updated_count = 0
        not_found_count = 0
        error_count = 0
        chunks_deleted = 0
        tags_cleared = 0
        
        for filename in sorted(txt_files):
            file_path = os.path.join(content_dir, filename)
            
            try:
                # Parse filename to get external_id
                external_id, source_slug = self.parse_filename(filename)
                
                # Extract content from file (also get header info)
                new_content, header_info = self.extract_content_from_file(file_path)
                
                # If external_id not found from filename, try header
                if not external_id or len(external_id) < 8:  # Too short to be a valid ID
                    if 'id' in header_info:
                        external_id = header_info['id']
                        self.stdout.write(f'  {filename}: Using external_id from file header: {external_id}')
                
                if not external_id:
                    self.stdout.write(
                        self.style.WARNING(f'  {filename}: Could not determine external_id from filename or header')
                    )
                    error_count += 1
                    continue
                
                # Find matching content
                content = self.find_content_by_external_id(external_id, source_filter)
                
                if not content:
                    self.stdout.write(
                        self.style.WARNING(f'  {filename}: Post not found (external_id: {external_id})')
                    )
                    not_found_count += 1
                    continue
                
                if not new_content:
                    self.stdout.write(
                        self.style.WARNING(f'  {filename}: No content found in file')
                    )
                    continue
                
                # Show what will be updated
                self.stdout.write(
                    self.style.SUCCESS(f'  {filename}: Found post "{content.title[:50]}" (ID: {content.external_id}, Source: {content.source.name})')
                )
                
                # Count what will be deleted/cleared
                chunks_count = content.chunks.count()
                tags_count = content.tags.count()
                
                if chunks_count > 0:
                    self.stdout.write(f'    - Will delete {chunks_count} chunk(s)')
                if tags_count > 0:
                    self.stdout.write(f'    - Will clear {tags_count} tag(s)')
                
                if not dry_run:
                    # Perform update
                    with transaction.atomic():
                        # Delete chunks
                        if chunks_count > 0:
                            deleted = content.chunks.all().delete()
                            chunks_deleted += chunks_count
                        
                        # Clear tags
                        if tags_count > 0:
                            content.tags.clear()
                            tags_cleared += tags_count
                        
                        # Update content and set processed=False
                        content.content = new_content
                        content.processed = False
                        content.save()  # This will auto-update has_content
                    
                    self.stdout.write(
                        self.style.SUCCESS(f'    ✓ Updated content ({len(new_content)} chars), set processed=False')
                    )
                
                updated_count += 1
                
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'  {filename}: Error - {str(e)}')
                )
                error_count += 1
                import traceback
                if options.get('verbosity', 1) >= 2:
                    self.stdout.write(self.style.ERROR(traceback.format_exc()))
        
        # Summary
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('UPDATE SUMMARY'))
        self.stdout.write('=' * 80)
        self.stdout.write(f'Files processed: {len(txt_files)}')
        self.stdout.write(self.style.SUCCESS(f'Posts updated: {updated_count}'))
        if not_found_count > 0:
            self.stdout.write(self.style.WARNING(f'Posts not found: {not_found_count}'))
        if error_count > 0:
            self.stdout.write(self.style.ERROR(f'Errors: {error_count}'))
        
        if not dry_run:
            if chunks_deleted > 0:
                self.stdout.write(self.style.SUCCESS(f'Chunks deleted: {chunks_deleted}'))
            if tags_cleared > 0:
                self.stdout.write(self.style.SUCCESS(f'Tags cleared: {tags_cleared}'))
            self.stdout.write(self.style.SUCCESS('\n✓ Update complete!'))
        else:
            self.stdout.write(self.style.WARNING('\nDRY RUN - No posts were updated'))
            self.stdout.write(self.style.WARNING('Run with --force to actually update posts'))

