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
from sources.utils import log_activity

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
            help='Path to TXT files directory (default: ebooks/ relative to project root)',
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
            # Default to ebooks/ relative to project root (for split files)
            default_txt_dir = os.path.join(project_root, 'ebooks')
            if os.path.exists(default_txt_dir):
                txt_dir = os.path.abspath(default_txt_dir)
            else:
                # Fallback to ebooks/txt for backward compatibility
                default_txt_dir_fallback = os.path.join(project_root, 'ebooks', 'txt')
                if os.path.exists(default_txt_dir_fallback):
                    txt_dir = os.path.abspath(default_txt_dir_fallback)
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
                
                part_files = ebook_data.get('part_files', [])
                if not title or (not txt_file and not part_files):
                    self.stdout.write(
                        self.style.WARNING(f'Skipping - missing title or files: {title or "no title"}')
                    )
                    skipped += 1
                    continue
                
                # Create a unique source for each ebook (using title + author as source name)
                # This ensures each ebook has its own source
                if title:
                    if author:
                        ebook_source_name = f"{title} - {author}"
                    else:
                        ebook_source_name = title
                else:
                    # Fallback
                    ebook_source_name = source_name or 'Imported Ebook'
                
                # Find or create unique source for this ebook
                source = Source.objects.filter(
                    name=ebook_source_name,
                    source_type='ebook'
                ).first()
                
                if not source:
                    # Create new unique source for this ebook
                    source = Source.objects.create(
                        name=ebook_source_name,
                        source_type='ebook',
                        link=link if link else None,
                        language=ebook_language if ebook_language else 'en',
                        is_active=True,
                    )
                    self.stdout.write(
                        self.style.SUCCESS(f'Created new ebook source: "{ebook_source_name}"')
                    )
                else:
                    self.stdout.write(
                        self.style.SUCCESS(f'Using existing ebook source: "{ebook_source_name}"')
                    )
                
                # Note: We don't check for existing content here because we're processing parts individually
                # Each part will be checked separately in the part processing loop
                
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
                
                # Process part files - create one content item per part
                if part_files:
                    self.stdout.write(f'Found {len(part_files)} part files for "{title}"')
                    
                    for part_file in part_files:
                        part_path = os.path.join(txt_dir, part_file)
                        if not os.path.exists(part_path):
                            self.stdout.write(
                                self.style.WARNING(f'Part file not found: "{part_path}"')
                            )
                            skipped += 1
                            continue
                        
                        # Load content from this part file
                        content_text = ''
                        try:
                            with open(part_path, 'r', encoding='utf-8') as tf:
                                content_text = tf.read().strip()
                            if not content_text:
                                self.stdout.write(
                                    self.style.WARNING(f'Part file "{part_file}" is empty, skipping')
                                )
                                skipped += 1
                                continue
                            content_loaded += 1
                        except Exception as e:
                            self.stdout.write(
                                self.style.WARNING(f'Could not load part file "{part_file}": {str(e)}')
                            )
                            errors += 1
                            continue
                        
                        # Extract part number for title and external_id
                        part_match = re.search(r'_part(\d+)', part_file, re.IGNORECASE)
                        part_num = int(part_match.group(1)) if part_match else 0
                        
                        # Generate external_id for this part (use filename base to ensure uniqueness)
                        part_external_id = os.path.splitext(part_file)[0]  # Remove .txt extension
                        part_external_id = re.sub(r'[^\w\-_]', '_', part_external_id)[:255]
                        
                        # Build title with part number
                        part_title = title
                        if author:
                            part_title = f"{title} - {author}"
                        part_title = f"{part_title} - Part {part_num}"
                        
                        # Check if this part already exists
                        existing = Content.objects.filter(
                            source=source,
                            external_id=part_external_id
                        ).first()
                        
                        if existing:
                            if skip_existing:
                                self.stdout.write(
                                    self.style.WARNING(f'Skipping existing part "{part_title}"')
                                )
                                skipped += 1
                                continue
                            else:
                                self.stdout.write(
                                    self.style.WARNING(f'Part "{part_title}" already exists (use --skip-existing to suppress)')
                                )
                                skipped += 1
                                continue
                        
                        # Translate French content if needed
                        ebook_language_lower = ebook_language.lower() if ebook_language else ''
                        should_translate = (
                            options.get('translate_fr', True) and 
                            not options.get('no_translate', False) and
                            ebook_language_lower in ('fr', 'french', 'français') and
                            content_text and
                            TRANSLATION_AVAILABLE
                        )
                        
                        if should_translate:
                            self.stdout.write(f'Translating French content for "{part_title}"...')
                            try:
                                # Translate in chunks to handle large files
                                chunk_size = 4500
                                translated_chunks = []
                                
                                if len(content_text) <= chunk_size:
                                    translator = GoogleTranslator(source='fr', target='en')
                                    translated_text = translator.translate(content_text)
                                    content_text = translated_text
                                else:
                                    translator = GoogleTranslator(source='fr', target='en')
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
                                    
                                    if current_chunk:
                                        translated_chunk = translator.translate(current_chunk)
                                        translated_chunks.append(translated_chunk)
                                    
                                    content_text = ' '.join(translated_chunks)
                                
                                self.stdout.write(
                                    self.style.SUCCESS(f'Translation completed for "{part_title}"')
                                )
                            except Exception as e:
                                self.stdout.write(
                                    self.style.WARNING(f'Translation failed for "{part_title}": {str(e)}. Using original content.')
                                )
                        elif ebook_language_lower in ('fr', 'french', 'français') and not TRANSLATION_AVAILABLE:
                            self.stdout.write(
                                self.style.WARNING(f'French content detected but translation library not available. Install: pip install deep-translator')
                            )
                        
                        # Create content for this part
                        with transaction.atomic():
                            content = Content.objects.create(
                                source=source,
                                external_id=part_external_id,
                                title=part_title,
                                link=link if link else None,
                                content_type='ebook',
                                date=date_obj,
                                content=content_text,
                                processed=False,
                            )
                        
                        self.stdout.write(
                            self.style.SUCCESS(f'Imported "{part_title}" (ID: {part_external_id})')
                        )
                        imported += 1
                
                else:
                    # Single file mode (backward compatibility)
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
                                                '_part' not in f.lower() and
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
                    
                    # Translate French content if needed
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
                            chunk_size = 4500
                            translated_chunks = []
                            
                            if len(content_text) <= chunk_size:
                                translator = GoogleTranslator(source='fr', target='en')
                                translated_text = translator.translate(content_text)
                                content_text = translated_text
                            else:
                                translator = GoogleTranslator(source='fr', target='en')
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
        
        # Log the import activity
        if imported > 0:
            log_activity(
                'import_completed',
                f'Imported {imported} ebook content items from CSV',
                metadata={
                    'imported': imported,
                    'skipped': skipped,
                    'content_loaded': content_loaded if load_content else 0,
                    'errors': errors,
                }
            )
    
    def find_part_files(self, title, author, txt_dir):
        """Find all part files for a given ebook title and author"""
        if not txt_dir or not os.path.exists(txt_dir):
            return []
        
        # Normalize title and author for matching
        # Remove punctuation and split into key words
        def normalize_text(text):
            """Normalize text for matching - remove punctuation, split into words"""
            if not text:
                return []
            # Replace common punctuation with spaces
            text = re.sub(r'[:\-\(\)/]', ' ', text.lower())
            # Split into words and filter out empty words
            # Keep words that are at least 2 chars OR contain numbers (like "17th", "2025")
            words = [w.strip() for w in text.split() 
                    if w.strip() and (len(w.strip()) >= 2 or re.search(r'\d', w.strip()))]
            return words
        
        title_words = normalize_text(title)
        author_words = normalize_text(author) if author else []
        
        # Build search patterns - try multiple matching strategies
        part_files = []
        all_files = [f for f in os.listdir(txt_dir) if f.endswith('.txt')]
        
        for filename in all_files:
            filename_lower = filename.lower()
            # Check if it's a part file
            if '_part' in filename_lower and filename_lower.endswith('.txt'):
                # Remove the _partXXX.txt suffix for matching
                filename_base = filename_lower.split('_part')[0]
                
                # Strategy 1: Check if all significant title words are in filename
                # (need at least 2 words to match, or if title is short, all words)
                title_match = False
                if title_words:
                    # Count how many title words appear in filename
                    matching_words = sum(1 for word in title_words if word in filename_base)
                    # Match if at least 60% of words match, or if we have 2+ matching words
                    min_words = max(2, len(title_words) // 2) if len(title_words) > 3 else len(title_words)
                    if matching_words >= min_words:
                        title_match = True
                
                # Strategy 2: Also try exact substring match (for backwards compatibility)
                if not title_match and title:
                    title_clean = re.sub(r'[:\-\(\)/]', ' ', title.lower())
                    # Try matching without punctuation
                    if title_clean.replace(' ', '') in filename_base.replace(' ', '').replace('_', '').replace('-', ''):
                        title_match = True
                
                # If title matches, check author if provided
                if title_match:
                    author_match = True
                    if author_words:
                        # Check if author words appear in filename
                        matching_author_words = sum(1 for word in author_words if word in filename_base)
                        # Need at least one author word to match (or all if author is short)
                        min_author = 1 if len(author_words) > 2 else len(author_words)
                        if matching_author_words < min_author:
                            # Author doesn't match, but if we have strong title match, still include
                            # (some files might not have author in filename)
                            pass
                    
                    part_files.append(filename)
        
        # Sort by part number to ensure correct order
        def extract_part_number(filename):
            match = re.search(r'_part(\d+)', filename, re.IGNORECASE)
            return int(match.group(1)) if match else 0
        
        part_files.sort(key=extract_part_number)
        return part_files
    
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
                
                title = normalized_row.get('title', '') or ''
                author = normalized_row.get('author', '') or ''
                txt_file_base = normalized_row.get('txt_file', '').strip() or ''
                
                # Find all part files for this ebook
                part_files = []
                
                # If txt_file column is provided, use it to find part files
                if txt_file_base and txt_dir:
                    # Normalize the base name for matching (remove extra spaces, handle variations)
                    txt_file_base_clean = re.sub(r'\s+', ' ', txt_file_base.strip())
                    txt_file_base_lower = txt_file_base_clean.lower()
                    
                    # Find all files that start with this base name and have _part in them
                    all_files = [f for f in os.listdir(txt_dir) if f.endswith('.txt')]
                    for filename in all_files:
                        filename_lower = filename.lower()
                        if '_part' in filename_lower:
                            # Remove _partXXX.txt to get the base
                            file_base = filename_lower.split('_part')[0].strip()
                            # Try exact match first
                            if file_base == txt_file_base_lower:
                                part_files.append(filename)
                            # Also try matching with normalized spaces/punctuation
                            elif (file_base.replace('_', ' ').replace('-', ' ') == 
                                  txt_file_base_lower.replace('_', ' ').replace('-', ' ')):
                                part_files.append(filename)
                            # Try substring match (in case of truncation)
                            elif (txt_file_base_lower in file_base or 
                                  file_base in txt_file_base_lower):
                                # Make sure it's a significant match (at least 10 chars)
                                if len(txt_file_base_lower) >= 10 or len(file_base) >= 10:
                                    part_files.append(filename)
                
                # If no part files found using txt_file column, try automatic matching
                if not part_files:
                    part_files = self.find_part_files(title, author, txt_dir)
                
                # If no part files found, try to find a single file (backward compatibility)
                txt_file = ''
                if not part_files:
                    if txt_file_base and txt_dir:
                        # Try to find exact match without _part
                        possible_files = [f for f in os.listdir(txt_dir) 
                                        if f.endswith('.txt') and 
                                        '_part' not in f.lower() and
                                        f.lower().startswith(txt_file_base_lower)]
                        if possible_files:
                            txt_file = possible_files[0]
                    elif title and txt_dir:
                        # Try to find matching file (non-part files)
                        possible_files = [f for f in os.listdir(txt_dir) 
                                        if f.endswith('.txt') and 
                                        '_part' not in f.lower() and
                                        title.lower() in f.lower()]
                        if possible_files:
                            txt_file = possible_files[0]
                
                ebooks_data.append({
                    'title': title,
                    'author': author,
                    'source': normalized_row.get('source', '') or '',
                    'language': normalized_row.get('language', '') or '',
                    'date': normalized_row.get('date', '') or '',
                    'link': normalized_row.get('link', '') or '',
                    'txt_file': txt_file,  # Single file (for backward compatibility)
                    'part_files': part_files,  # List of part files
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

