"""
Management command to import ebooks from CSV file or directory.
Usage: 
    python manage.py import_ebooks path/to/ebooks.csv
    python manage.py import_ebooks --scan-dir
"""
import csv
import os
import re
from datetime import datetime
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from sources.models import Source, Content

# Translation imports
try:
    from deep_translator import GoogleTranslator
    TRANSLATION_AVAILABLE = True
except ImportError:
    TRANSLATION_AVAILABLE = False


class Command(BaseCommand):
    help = 'Import ebooks from a CSV file or scan directory'

    def add_arguments(self, parser):
        parser.add_argument(
            'csv_file',
            type=str,
            nargs='?',
            default=None,
            help='Path to the ebooks CSV file (optional if using --scan-dir)'
        )
        parser.add_argument(
            '--skip-existing',
            action='store_true',
            help='Skip ebooks that already exist (based on source + external_id)',
        )
        parser.add_argument(
            '--load-content',
            action='store_true',
            default=True,
            help='Load content from TXT files (default: True)',
        )
        parser.add_argument(
            '--txt-dir',
            type=str,
            default=None,
            help='Path to TXT files directory (default: ebooks/txt/ relative to project root)',
        )
        parser.add_argument(
            '--scan-dir',
            action='store_true',
            help='Scan ebooks/txt directory and import all TXT files (requires --txt-dir or default location)',
        )
        parser.add_argument(
            '--translate-fr',
            action='store_true',
            default=True,
            help='Translate French content to English (default: True)',
        )
        parser.add_argument(
            '--no-translate',
            action='store_true',
            help='Skip translation even for French content',
        )

    def handle(self, *args, **options):
        csv_file = options.get('csv_file')
        skip_existing = options['skip_existing']
        load_content = options['load_content']
        txt_dir = options['txt_dir']
        scan_dir = options['scan_dir']
        
        # Get project root
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        
        # Resolve TXT directory path
        if txt_dir:
            if not os.path.isabs(txt_dir):
                txt_dir = os.path.join(project_root, txt_dir)
            txt_dir = os.path.abspath(txt_dir)
        else:
            # Default to ebooks/txt relative to project root
            default_txt_dir = os.path.join(project_root, 'ebooks', 'txt')
            if os.path.exists(default_txt_dir):
                txt_dir = os.path.abspath(default_txt_dir)
            else:
                txt_dir = None
        
        if scan_dir:
            # Scan directory mode
            if not txt_dir or not os.path.exists(txt_dir):
                raise CommandError(
                    f'TXT directory not found: {txt_dir}\n'
                    f'Please specify --txt-dir or ensure ebooks/txt/ exists in project root.'
                )
            
            self.stdout.write(f'Scanning ebooks directory: {txt_dir}')
            ebooks_data = self.scan_directory(txt_dir)
        elif csv_file:
            # CSV file mode
            if not os.path.isabs(csv_file):
                csv_file_abs = os.path.abspath(csv_file)
                if not os.path.exists(csv_file_abs):
                    csv_file_from_root = os.path.join(project_root, csv_file)
                    if os.path.exists(csv_file_from_root):
                        csv_file_abs = os.path.abspath(csv_file_from_root)
                    else:
                        raise CommandError(f'CSV file not found: {csv_file}')
                csv_file = csv_file_abs
            
            if not os.path.exists(csv_file):
                raise CommandError(f'CSV file not found: {csv_file}')
            
            self.stdout.write(f'Reading ebooks from: {csv_file}')
            ebooks_data = self.read_csv(csv_file, txt_dir)
        else:
            raise CommandError(
                'Either provide a CSV file or use --scan-dir option.\n'
                'Example: python manage.py import_ebooks ebooks.csv\n'
                '         python manage.py import_ebooks --scan-dir'
            )
        
        if load_content and txt_dir:
            self.stdout.write(f'TXT directory: {txt_dir}')
        
        imported = 0
        skipped = 0
        errors = 0
        content_loaded = 0
        
        for ebook_data in ebooks_data:
            try:
                title = ebook_data.get('title', '').strip()
                source_name = ebook_data.get('source', '').strip()
                txt_file = ebook_data.get('txt_file', '').strip()
                date_str = ebook_data.get('date', '').strip()
                author = ebook_data.get('author', '').strip()
                link = ebook_data.get('link', '').strip()
                ebook_language = ebook_data.get('language', '').strip()
                
                if not title or not txt_file:
                    self.stdout.write(
                        self.style.WARNING(f'Skipping - missing title or txt_file: {title or txt_file}')
                    )
                    skipped += 1
                    continue
                
                # Generate external_id from filename (remove extension, sanitize)
                external_id = os.path.splitext(os.path.basename(txt_file))[0]
                external_id = re.sub(r'[^\w\-_]', '_', external_id)[:255]
                
                # Find or create ebook source
                if source_name:
                    source = Source.objects.filter(
                        name=source_name,
                        source_type='ebook'
                    ).first()
                    
                    if not source:
                        # Try without source_type filter
                        source = Source.objects.filter(name=source_name).first()
                        if source:
                            self.stdout.write(
                                self.style.WARNING(f'Found source "{source_name}" but it\'s not an ebook type')
                            )
                        else:
                            # Create new ebook source
                            source = Source.objects.create(
                                name=source_name,
                                source_type='ebook',
                                link=link if link else None,
                                language='en',  # Default, can be updated later
                                is_active=True,
                            )
                            self.stdout.write(
                                self.style.SUCCESS(f'Created new ebook source: "{source_name}"')
                            )
                else:
                    # Use a default source name based on filename or create generic
                    source_name = 'Imported Ebooks'
                    source = Source.objects.filter(
                        name=source_name,
                        source_type='ebook'
                    ).first()
                    
                    if not source:
                        source = Source.objects.create(
                            name=source_name,
                            source_type='ebook',
                            link=None,
                            language='en',
                            is_active=True,
                        )
                
                # Check if ebook already exists (always check to avoid duplicates)
                existing = Content.objects.filter(
                    source=source,
                    external_id=external_id
                ).first()
                
                if existing:
                    # If existing but no content, try to load it
                    if not existing.has_content and load_content and txt_dir:
                        # Try to load content for existing ebook
                        if txt_file and not txt_file.endswith('.txt'):
                            txt_file = txt_file + '.txt'
                        
                        txt_path = os.path.join(txt_dir, txt_file) if not os.path.isabs(txt_file) else txt_file
                        if not os.path.exists(txt_path) and txt_dir:
                            # Try to find file with similar name
                            possible_files = [f for f in os.listdir(txt_dir) 
                                            if f.lower().endswith('.txt') and 
                                            (os.path.splitext(txt_file)[0].lower() in f.lower() or 
                                             os.path.splitext(f)[0].lower() == os.path.splitext(txt_file)[0].lower())]
                            if possible_files:
                                txt_path = os.path.join(txt_dir, possible_files[0])
                        
                        if os.path.exists(txt_path):
                            try:
                                with open(txt_path, 'r', encoding='utf-8') as tf:
                                    content_text = tf.read().strip()
                                
                                # Update existing ebook with content
                                existing.content = content_text
                                existing.save()  # This will auto-update has_content
                                
                                self.stdout.write(
                                    self.style.SUCCESS(f'Updated existing ebook "{title}" with content from "{os.path.basename(txt_path)}"')
                                )
                                imported += 1
                                continue
                            except Exception as e:
                                self.stdout.write(
                                    self.style.WARNING(f'Could not load content for existing ebook "{title}": {str(e)}')
                                )
                    
                    if skip_existing:
                        self.stdout.write(
                            self.style.WARNING(f'Skipping existing ebook "{title}"')
                        )
                    else:
                        self.stdout.write(
                            self.style.WARNING(f'Ebook "{title}" already exists (use --skip-existing to suppress)')
                        )
                    skipped += 1
                    continue
                
                # Parse date
                date_obj = None
                if date_str:
                    try:
                        if 'T' in date_str:
                            date_str = date_str.split('T')[0]
                        
                        for date_format in ['%Y-%m-%d', '%Y/%m/%d', '%d-%m-%Y', '%d/%m/%Y', '%m/%d/%Y']:
                            try:
                                date_obj = datetime.strptime(date_str, date_format).date()
                                break
                            except ValueError:
                                continue
                    except Exception:
                        pass
                
                if not date_obj:
                    # Use file modification date or today
                    if txt_dir and txt_file:
                        txt_path = os.path.join(txt_dir, txt_file) if not os.path.isabs(txt_file) else txt_file
                        if os.path.exists(txt_path):
                            try:
                                date_obj = datetime.fromtimestamp(os.path.getmtime(txt_path)).date()
                            except Exception:
                                date_obj = datetime.now().date()
                        else:
                            date_obj = datetime.now().date()
                    else:
                        date_obj = datetime.now().date()
                
                # Load content from TXT file
                content_text = ''
                if load_content and txt_dir:
                    # Ensure .txt extension
                    if txt_file and not txt_file.endswith('.txt'):
                        txt_file = txt_file + '.txt'
                    
                    txt_path = os.path.join(txt_dir, txt_file) if not os.path.isabs(txt_file) else txt_file
                    if os.path.exists(txt_path):
                        try:
                            with open(txt_path, 'r', encoding='utf-8') as tf:
                                content_text = tf.read().strip()
                            content_loaded += 1
                        except Exception as e:
                            self.stdout.write(
                                self.style.WARNING(f'Could not load content from "{txt_file}": {str(e)}')
                            )
                    else:
                        # Try to find file without extension or with different case
                        if txt_dir:
                            possible_files = [f for f in os.listdir(txt_dir) 
                                            if f.lower().endswith('.txt') and 
                                            (os.path.splitext(txt_file)[0].lower() in f.lower() or 
                                             os.path.splitext(f)[0].lower() == os.path.splitext(txt_file)[0].lower())]
                            if possible_files:
                                txt_path = os.path.join(txt_dir, possible_files[0])
                                try:
                                    with open(txt_path, 'r', encoding='utf-8') as tf:
                                        content_text = tf.read().strip()
                                    content_loaded += 1
                                    self.stdout.write(
                                        self.style.SUCCESS(f'Found file with different name: "{possible_files[0]}"')
                                    )
                                except Exception as e:
                                    self.stdout.write(
                                        self.style.WARNING(f'Could not load content from "{possible_files[0]}": {str(e)}')
                                    )
                            else:
                                self.stdout.write(
                                    self.style.WARNING(f'TXT file not found: "{txt_path}"')
                                )
                        else:
                            self.stdout.write(
                                self.style.WARNING(f'TXT file not found: "{txt_path}"')
                            )
                
                # Translate French content to English if needed
                ebook_language_lower = ebook_language.lower() if ebook_language else ''
                should_translate = (
                    options.get('translate_fr', True) and 
                    not options.get('no_translate', False) and
                    ebook_language_lower in ('fr', 'french', 'français') and
                    content_text and
                    TRANSLATION_AVAILABLE
                )
                
                if should_translate:
                    self.stdout.write(f'Translating French content for "{title}"...')
                    try:
                        # Translate in chunks to handle large files
                        # Google Translate has a 5000 character limit per request
                        chunk_size = 4500  # Leave some margin
                        translated_chunks = []
                        
                        if len(content_text) <= chunk_size:
                            # Small content, translate directly
                            translator = GoogleTranslator(source='fr', target='en')
                            translated_text = translator.translate(content_text)
                            content_text = translated_text
                        else:
                            # Large content, translate in chunks
                            translator = GoogleTranslator(source='fr', target='en')
                            # Split by sentences or paragraphs to avoid breaking mid-sentence
                            sentences = re.split(r'([.!?]\s+)', content_text)
                            current_chunk = ''
                            
                            for sentence in sentences:
                                if len(current_chunk) + len(sentence) <= chunk_size:
                                    current_chunk += sentence
                                else:
                                    if current_chunk:
                                        translated_chunk = translator.translate(current_chunk)
                                        translated_chunks.append(translated_chunk)
                                    current_chunk = sentence
                            
                            # Translate remaining chunk
                            if current_chunk:
                                translated_chunk = translator.translate(current_chunk)
                                translated_chunks.append(translated_chunk)
                            
                            content_text = ' '.join(translated_chunks)
                        
                        self.stdout.write(
                            self.style.SUCCESS(f'Translation completed for "{title}"')
                        )
                    except Exception as e:
                        self.stdout.write(
                            self.style.WARNING(f'Translation failed for "{title}": {str(e)}. Using original content.')
                        )
                elif ebook_language_lower in ('fr', 'french', 'français') and not TRANSLATION_AVAILABLE:
                    self.stdout.write(
                        self.style.WARNING(f'French content detected but translation library not available. Install: pip install deep-translator')
                    )
                
                # Build full title with author if available
                full_title = title
                if author:
                    full_title = f"{title} - {author}"
                
                # Create content
                with transaction.atomic():
                    content = Content.objects.create(
                        source=source,
                        external_id=external_id,
                        title=full_title,
                        link=link if link else None,
                        content_type='ebook',
                        date=date_obj,
                        content=content_text,
                        processed=False,
                    )
                
                self.stdout.write(
                    self.style.SUCCESS(f'Imported "{full_title}" (ID: {external_id})')
                )
                imported += 1
                
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'Error importing ebook: {str(e)}')
                )
                import traceback
                self.stdout.write(self.style.ERROR(traceback.format_exc()))
                errors += 1
        
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
    
    def read_csv(self, csv_file, txt_dir):
        """Read ebooks data from CSV file"""
        ebooks_data = []
        
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                # Handle None values and different types in CSV cells
                normalized_row = {}
                for k, v in row.items():
                    key = k.strip() if k and isinstance(k, str) else (str(k) if k else '')
                    if v is None:
                        value = ''
                    elif isinstance(v, str):
                        value = v.strip()
                    elif isinstance(v, list):
                        value = ' '.join(str(item) for item in v).strip()
                    else:
                        value = str(v).strip() if v else ''
                    normalized_row[key] = value
                
                # Get txt_file path
                txt_file = normalized_row.get('txt_file', '') or ''
                if not txt_file:
                    # Try to infer from title or filename
                    title = normalized_row.get('title', '') or ''
                    if title and txt_dir:
                        # Try to find matching file
                        possible_files = [f for f in os.listdir(txt_dir) if title.lower() in f.lower() and f.endswith('.txt')]
                        if possible_files:
                            txt_file = possible_files[0]
                
                ebooks_data.append({
                    'title': normalized_row.get('title', '') or '',
                    'author': normalized_row.get('author', '') or '',
                    'source': normalized_row.get('source', '') or '',
                    'language': normalized_row.get('language', '') or '',
                    'date': normalized_row.get('date', '') or '',
                    'link': normalized_row.get('link', '') or '',
                    'txt_file': txt_file,
                })
        
        return ebooks_data
    
    def scan_directory(self, txt_dir):
        """Scan directory and create ebook data from filenames"""
        ebooks_data = []
        
        if not os.path.exists(txt_dir):
            return ebooks_data
        
        for filename in os.listdir(txt_dir):
            if not filename.endswith('.txt'):
                continue
            
            # Extract title from filename (remove extension)
            title = os.path.splitext(filename)[0]
            
            # Try to extract author if format is "Title - Author.txt"
            author = ''
            if ' - ' in title:
                parts = title.split(' - ', 1)
                if len(parts) == 2:
                    title = parts[0].strip()
                    author = parts[1].strip()
            
            # Try to infer source from filename patterns
            source_name = ''
            if 'lonely planet' in filename.lower():
                source_name = 'Lonely Planet'
            elif 'guide' in filename.lower():
                source_name = 'Travel Guide'
            
            # Try to detect language from filename or content
            detected_language = 'en'  # Default
            if 'fut' in filename.lower() or 'futé' in filename.lower():
                detected_language = 'fr'
            
            ebooks_data.append({
                'title': title,
                'author': author,
                'source': source_name,
                'language': detected_language,
                'date': '',
                'link': '',
                'txt_file': filename,
            })
        
        return ebooks_data

