"""
Management command to import blogs from CSV file.
Usage: python manage.py import_blogs path/to/blogs.csv
"""
import csv
import os
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from sources.models import Source


class Command(BaseCommand):
    help = 'Import blogs from a CSV file'

    def add_arguments(self, parser):
        parser.add_argument(
            'csv_file',
            type=str,
            help='Path to the blogs CSV file'
        )
        parser.add_argument(
            '--skip-existing',
            action='store_true',
            help='Skip blogs that already exist (based on link)',
        )

    def handle(self, *args, **options):
        csv_file = options['csv_file']
        skip_existing = options['skip_existing']
        
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
                f'Example: python manage.py import_blogs blogs.csv'
            )
        
        self.stdout.write(f'Reading blogs from: {csv_file}')
        
        # Language mapping
        language_map = {
            'english': 'en',
            'french': 'fr',
            'français': 'fr',
            'chinese': 'zh',
            'chinois': 'zh',
            'spanish': 'es',
            'german': 'de',
        }
        
        imported = 0
        skipped = 0
        errors = 0
        
        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                # Normalize column names (strip whitespace)
                fieldnames = [name.strip() for name in reader.fieldnames] if reader.fieldnames else []
                
                for row_num, row in enumerate(reader, start=2):  # Start at 2 because row 1 is header
                    try:
                        # Normalize row keys
                        normalized_row = {k.strip(): v for k, v in row.items()}
                        
                        name = normalized_row.get('name', '').strip()
                        url = normalized_row.get('url', '').strip()
                        language = normalized_row.get('language', 'English').strip()
                        rss_feed = normalized_row.get('rss_feed', '').strip()
                        sitemaps = normalized_row.get('sitemaps', '').strip()
                        filter_china = normalized_row.get('filter_china', 'False').strip()
                        blog_only = normalized_row.get('blog_only', 'False').strip()
                        
                        if not name or not url:
                            self.stdout.write(
                                self.style.WARNING(f'  Row {row_num}: Skipping - missing name or url')
                            )
                            skipped += 1
                            continue
                        
                        # Map language to code
                        language_lower = language.lower()
                        language_code = language_map.get(language_lower, 'en')
                        if language_code == 'en' and language_lower not in language_map:
                            # Try to match first part
                            for key, code in language_map.items():
                                if language_lower.startswith(key):
                                    language_code = code
                                    break
                        
                        # Convert boolean strings
                        filter_china_bool = filter_china.lower() in ('true', '1', 'yes')
                        blog_only_bool = blog_only.lower() in ('true', '1', 'yes')
                        
                        # Build metadata JSON
                        metadata = {}
                        if rss_feed:
                            metadata['rss_feed'] = rss_feed
                        if sitemaps:
                            metadata['sitemaps'] = sitemaps
                        metadata['filter_china'] = filter_china_bool
                        metadata['blog_only'] = blog_only_bool
                        
                        # Check if blog already exists
                        if skip_existing:
                            existing = Source.objects.filter(
                                link=url,
                                source_type='blog'
                            ).first()
                            
                            if existing:
                                self.stdout.write(
                                    self.style.WARNING(f'  Row {row_num}: Skipping existing blog "{name}"')
                                )
                                skipped += 1
                                continue
                        
                        # Create source
                        with transaction.atomic():
                            source = Source.objects.create(
                                name=name,
                                source_type='blog',
                                link=url,
                                language=language_code,
                                metadata=metadata,
                                is_active=True,
                            )
                        
                        self.stdout.write(
                            self.style.SUCCESS(f'  Row {row_num}: Imported "{name}"')
                        )
                        imported += 1
                        
                    except Exception as e:
                        self.stdout.write(
                            self.style.ERROR(f'  Row {row_num}: Error - {str(e)}')
                        )
                        errors += 1
        
        except Exception as e:
            raise CommandError(f'Error reading CSV file: {str(e)}')
        
        # Summary
        self.stdout.write(self.style.SUCCESS('\n' + '=' * 50))
        self.stdout.write(self.style.SUCCESS('Import Summary:'))
        self.stdout.write(self.style.SUCCESS(f'  Imported: {imported}'))
        self.stdout.write(self.style.WARNING(f'  Skipped: {skipped}'))
        if errors > 0:
            self.stdout.write(self.style.ERROR(f'  Errors: {errors}'))
        self.stdout.write(self.style.SUCCESS('=' * 50))
