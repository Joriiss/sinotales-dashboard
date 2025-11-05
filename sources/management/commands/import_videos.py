"""
Management command to import videos from CSV file.
Usage: python manage.py import_videos path/to/videos.csv
"""
import csv
import os
from datetime import datetime
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from sources.models import Source, Content


class Command(BaseCommand):
    help = 'Import videos from a CSV file'

    def add_arguments(self, parser):
        parser.add_argument(
            'csv_file',
            type=str,
            help='Path to the videos CSV file'
        )
        parser.add_argument(
            '--skip-existing',
            action='store_true',
            help='Skip videos that already exist (based on source + external_id)',
        )
        parser.add_argument(
            '--load-transcripts',
            action='store_true',
            help='Load transcript content from transcript files if available',
        )

    def handle(self, *args, **options):
        csv_file = options['csv_file']
        skip_existing = options['skip_existing']
        load_transcripts = options['load_transcripts']
        
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
                else:
                    # Try with common relative path from dashboard to data folder
                    common_path = os.path.join(project_root, '..', 'china-blog-data', 'videos', os.path.basename(csv_file))
                    common_path = os.path.normpath(common_path)
                    if os.path.exists(common_path):
                        csv_file_abs = os.path.abspath(common_path)
            
            csv_file = csv_file_abs
        
        if not os.path.exists(csv_file):
            raise CommandError(
                f'CSV file not found: {csv_file}\n'
                f'Please provide an absolute path or a path relative to the current directory.\n'
                f'Example: python manage.py import_videos "C:\\Users\\Joris\\Desktop\\china-blog\\china-blog-data\\videos\\videos.csv"'
            )
        
        self.stdout.write(f'Reading videos from: {csv_file}')
        
        imported = 0
        skipped = 0
        errors = 0
        transcripts_loaded = 0
        
        # Get transcripts directory path if loading transcripts
        transcripts_dir = None
        if load_transcripts:
            # Try to find transcripts directory relative to CSV file
            csv_dir = os.path.dirname(csv_file)
            possible_transcripts_dir = os.path.join(csv_dir, 'transcripts')
            if os.path.exists(possible_transcripts_dir):
                transcripts_dir = possible_transcripts_dir
                self.stdout.write(f'Found transcripts directory: {transcripts_dir}')
            else:
                self.stdout.write(self.style.WARNING('Transcripts directory not found, skipping transcript loading'))
                load_transcripts = False
        
        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                # Normalize column names (strip whitespace)
                fieldnames = [name.strip() for name in reader.fieldnames] if reader.fieldnames else []
                
                for row_num, row in enumerate(reader, start=2):  # Start at 2 because row 1 is header
                    try:
                        # Normalize row keys
                        normalized_row = {k.strip(): v for k, v in row.items()}
                        
                        channel_name = normalized_row.get('channel_name', '').strip()
                        video_title = normalized_row.get('video_title', '').strip()
                        video_id = normalized_row.get('video_id', '').strip()
                        upload_date = normalized_row.get('upload_date', '').strip()
                        
                        if not channel_name or not video_id or not video_title:
                            self.stdout.write(
                                self.style.WARNING(f'  Row {row_num}: Skipping - missing required fields')
                            )
                            skipped += 1
                            continue
                        
                        # Find source by channel name
                        source = Source.objects.filter(name=channel_name).first()
                        if not source:
                            self.stdout.write(
                                self.style.ERROR(f'  Row {row_num}: Source not found: "{channel_name}"')
                            )
                            errors += 1
                            continue
                        
                        # Check if video already exists
                        if skip_existing:
                            existing = Content.objects.filter(
                                source=source,
                                external_id=video_id
                            ).first()
                            
                            if existing:
                                self.stdout.write(
                                    self.style.WARNING(f'  Row {row_num}: Skipping existing video "{video_title}"')
                                )
                                skipped += 1
                                continue
                        
                        # Parse date
                        date_obj = None
                        if upload_date:
                            try:
                                # Try different date formats
                                for date_format in ['%Y-%m-%d', '%Y/%m/%d', '%d-%m-%Y', '%d/%m/%Y']:
                                    try:
                                        date_obj = datetime.strptime(upload_date, date_format).date()
                                        break
                                    except ValueError:
                                        continue
                                if not date_obj:
                                    self.stdout.write(
                                        self.style.WARNING(f'  Row {row_num}: Could not parse date "{upload_date}", using today')
                                    )
                                    date_obj = datetime.now().date()
                            except Exception as e:
                                self.stdout.write(
                                    self.style.WARNING(f'  Row {row_num}: Date parsing error: {str(e)}, using today')
                                )
                                date_obj = datetime.now().date()
                        else:
                            date_obj = datetime.now().date()
                        
                        # Build YouTube video URL
                        video_link = f"https://www.youtube.com/watch?v={video_id}"
                        
                        # Load transcript if requested
                        content_text = ''
                        if load_transcripts and transcripts_dir:
                            transcript_file = os.path.join(transcripts_dir, f"{video_id}.txt")
                            if os.path.exists(transcript_file):
                                try:
                                    with open(transcript_file, 'r', encoding='utf-8') as tf:
                                        content_text = tf.read().strip()
                                    transcripts_loaded += 1
                                except Exception as e:
                                    self.stdout.write(
                                        self.style.WARNING(f'  Row {row_num}: Could not load transcript: {str(e)}')
                                    )
                        
                        # Create content
                        with transaction.atomic():
                            content = Content.objects.create(
                                source=source,
                                external_id=video_id,
                                title=video_title,
                                link=video_link,
                                content_type='video',
                                date=date_obj,
                                content=content_text,
                                processed=False,
                            )
                        
                        self.stdout.write(
                            self.style.SUCCESS(f'  Row {row_num}: Imported "{video_title}" (ID: {video_id})')
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
        if load_transcripts and transcripts_loaded > 0:
            self.stdout.write(self.style.SUCCESS(f'  Transcripts loaded: {transcripts_loaded}'))
        if errors > 0:
            self.stdout.write(self.style.ERROR(f'  Errors: {errors}'))
        self.stdout.write(self.style.SUCCESS('=' * 50))

