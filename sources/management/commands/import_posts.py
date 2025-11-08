"""
Management command to import blog posts from CSV file.
Usage: python manage.py import_posts path/to/posts.csv
"""
import csv
import os
from datetime import datetime
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from sources.models import Source, Content


class Command(BaseCommand):
    help = 'Import blog posts from a CSV file'

    def add_arguments(self, parser):
        parser.add_argument(
            'csv_file',
            type=str,
            help='Path to the posts CSV file'
        )
        parser.add_argument(
            '--skip-existing',
            action='store_true',
            help='Skip posts that already exist (based on source + external_id)',
        )
        parser.add_argument(
            '--load-content',
            action='store_true',
            help='Load content from content files if available',
        )
        parser.add_argument(
            '--content-dir',
            type=str,
            default=None,
            help='Path to content directory (default: content/ relative to CSV file or project root)',
        )

    def handle(self, *args, **options):
        csv_file = options['csv_file']
        skip_existing = options['skip_existing']
        load_content = options['load_content']
        content_dir = options['content_dir']
        
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
                f'Example: python manage.py import_posts posts.csv'
            )
        
        self.stdout.write(f'Reading posts from: {csv_file}')
        
        # Resolve content directory path
        if load_content:
            if content_dir:
                if not os.path.isabs(content_dir):
                    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
                    content_dir = os.path.join(project_root, content_dir)
                content_dir = os.path.abspath(content_dir)
            else:
                # Try to find content directory relative to CSV file
                csv_dir = os.path.dirname(csv_file)
                possible_content_dir = os.path.join(csv_dir, 'content')
                if os.path.exists(possible_content_dir):
                    content_dir = os.path.abspath(possible_content_dir)
                else:
                    # Try relative to project root
                    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
                    possible_content_dir = os.path.join(project_root, 'content')
                    if os.path.exists(possible_content_dir):
                        content_dir = os.path.abspath(possible_content_dir)
                    else:
                        self.stdout.write(self.style.WARNING('Content directory not found, skipping content loading'))
                        load_content = False
            
            if load_content and content_dir:
                self.stdout.write(f'Content directory: {content_dir}')
        
        imported = 0
        skipped = 0
        errors = 0
        content_loaded = 0
        
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
                        title = normalized_row.get('title', '').strip()
                        link = normalized_row.get('link', '').strip()
                        date_str = normalized_row.get('date', '').strip()
                        tags = normalized_row.get('tags', '').strip()
                        source_name = normalized_row.get('source', '').strip()
                        content_file = normalized_row.get('content_file', '').strip()
                        
                        if not post_id or not title or not link or not source_name:
                            self.stdout.write(
                                self.style.WARNING(f'  Row {row_num}: Skipping - missing required fields (id, title, link, or source)')
                            )
                            skipped += 1
                            continue
                        
                        # Find source by name (should be a blog source)
                        source = Source.objects.filter(
                            name=source_name,
                            source_type='blog'
                        ).first()
                        
                        if not source:
                            # Try without source_type filter in case it's not set correctly
                            source = Source.objects.filter(name=source_name).first()
                            if source:
                                self.stdout.write(
                                    self.style.WARNING(f'  Row {row_num}: Found source "{source_name}" but it\'s not a blog type')
                                )
                            else:
                                self.stdout.write(
                                    self.style.ERROR(f'  Row {row_num}: Source not found: "{source_name}"')
                                )
                                errors += 1
                                continue
                        
                        # Check if post already exists (always check to avoid duplicates)
                        existing = Content.objects.filter(
                            source=source,
                            external_id=post_id
                        ).first()
                        
                        if existing:
                            if skip_existing:
                                self.stdout.write(
                                    self.style.WARNING(f'  Row {row_num}: Skipping existing post "{title}"')
                                )
                            else:
                                self.stdout.write(
                                    self.style.WARNING(f'  Row {row_num}: Post "{title}" already exists (use --skip-existing to suppress)')
                                )
                            skipped += 1
                            continue
                        
                        # Parse date
                        date_obj = None
                        if date_str:
                            try:
                                # Try ISO format first (2025-11-05T00:00:00)
                                if 'T' in date_str:
                                    date_str = date_str.split('T')[0]
                                
                                # Try different date formats
                                for date_format in ['%Y-%m-%d', '%Y/%m/%d', '%d-%m-%Y', '%d/%m/%Y', '%m/%d/%Y']:
                                    try:
                                        date_obj = datetime.strptime(date_str, date_format).date()
                                        break
                                    except ValueError:
                                        continue
                                
                                if not date_obj:
                                    self.stdout.write(
                                        self.style.WARNING(f'  Row {row_num}: Could not parse date "{date_str}", using today')
                                    )
                                    date_obj = datetime.now().date()
                            except Exception as e:
                                self.stdout.write(
                                    self.style.WARNING(f'  Row {row_num}: Date parsing error: {str(e)}, using today')
                                )
                                date_obj = datetime.now().date()
                        else:
                            date_obj = datetime.now().date()
                        
                        # Load content from file if requested
                        content_text = ''
                        if load_content and content_dir and content_file:
                            content_file_path = os.path.join(content_dir, content_file)
                            if os.path.exists(content_file_path):
                                try:
                                    with open(content_file_path, 'r', encoding='utf-8') as cf:
                                        content_text = cf.read().strip()
                                    content_loaded += 1
                                except Exception as e:
                                    self.stdout.write(
                                        self.style.WARNING(f'  Row {row_num}: Could not load content file "{content_file}": {str(e)}')
                                    )
                            else:
                                self.stdout.write(
                                    self.style.WARNING(f'  Row {row_num}: Content file not found: "{content_file_path}"')
                                )
                        
                        # Create content
                        with transaction.atomic():
                            content = Content.objects.create(
                                source=source,
                                external_id=post_id,
                                title=title,
                                link=link,
                                content_type='blog_post',
                                date=date_obj,
                                content=content_text,
                                processed=False,
                            )
                        
                        self.stdout.write(
                            self.style.SUCCESS(f'  Row {row_num}: Imported "{title}" (ID: {post_id})')
                        )
                        imported += 1
                        
                    except Exception as e:
                        self.stdout.write(
                            self.style.ERROR(f'  Row {row_num}: Error - {str(e)}')
                        )
                        import traceback
                        self.stdout.write(self.style.ERROR(traceback.format_exc()))
                        errors += 1
        
        except Exception as e:
            raise CommandError(f'Error reading CSV file: {str(e)}')
        
        # Summary
        self.stdout.write(self.style.SUCCESS('\n' + '=' * 50))
        self.stdout.write(self.style.SUCCESS('Import Summary:'))
        self.stdout.write(self.style.SUCCESS(f'  Imported: {imported}'))
        self.stdout.write(self.style.WARNING(f'  Skipped: {skipped}'))
        if load_content and content_loaded > 0:
            self.stdout.write(self.style.SUCCESS(f'  Content files loaded: {content_loaded}'))
        if errors > 0:
            self.stdout.write(self.style.ERROR(f'  Errors: {errors}'))
        self.stdout.write(self.style.SUCCESS('=' * 50))

