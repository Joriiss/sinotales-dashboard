"""
Management command to import YouTube channels from CSV file.
Usage: python manage.py import_channels path/to/channels.csv
"""
import csv
import os
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from sources.models import Source


class Command(BaseCommand):
    help = 'Import YouTube channels from a CSV file'

    def add_arguments(self, parser):
        parser.add_argument(
            'csv_file',
            type=str,
            help='Path to the channels CSV file'
        )
        parser.add_argument(
            '--skip-existing',
            action='store_true',
            help='Skip channels that already exist (based on channel_id or link)',
        )

    def handle(self, *args, **options):
        csv_file = options['csv_file']
        skip_existing = options['skip_existing']
        
        # Resolve file path
        if not os.path.isabs(csv_file):
            # Try relative to project root
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            csv_file = os.path.join(base_dir, '..', 'china-blog-data', 'videos', csv_file)
            csv_file = os.path.abspath(csv_file)
        
        if not os.path.exists(csv_file):
            raise CommandError(f'CSV file not found: {csv_file}')
        
        self.stdout.write(f'Reading channels from: {csv_file}')
        
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
                        link = normalized_row.get('link', '').strip()
                        include_shorts = normalized_row.get('include_shorts', 'False').strip().lower() == 'true'
                        language = normalized_row.get('language', 'English').strip()
                        channel_id = normalized_row.get('channel_id', '').strip()
                        
                        if not name or not link:
                            self.stdout.write(
                                self.style.WARNING(f'  Row {row_num}: Skipping - missing name or link')
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
                        
                        # Check if channel already exists
                        if skip_existing:
                            from django.db.models import Q
                            existing = Source.objects.filter(
                                Q(link=link) | Q(channel_id=channel_id)
                            ).first()
                            
                            if existing:
                                self.stdout.write(
                                    self.style.WARNING(f'  Row {row_num}: Skipping existing channel "{name}"')
                                )
                                skipped += 1
                                continue
                        
                        # Create source
                        with transaction.atomic():
                            source = Source.objects.create(
                                name=name,
                                source_type='youtube',
                                link=link,
                                language=language_code,
                                channel_id=channel_id if channel_id else None,
                                include_shorts=include_shorts,
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

