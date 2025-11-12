"""
Management command to test transcript extraction from a YouTube video
"""
from django.core.management.base import BaseCommand, CommandError
from sources.content_processing_service import ContentProcessingService
from sources.models import Source, Content
from django.utils import timezone
from datetime import date


class Command(BaseCommand):
    help = 'Test transcript extraction from a YouTube video'

    def add_arguments(self, parser):
        parser.add_argument(
            'video_id',
            type=str,
            help='YouTube video ID to test (e.g., Lca0ozE0T2o)'
        )
        parser.add_argument(
            '--source-id',
            type=int,
            help='Source ID to use (optional - will create a test content entry)'
        )
        parser.add_argument(
            '--use-proxy',
            action='store_true',
            help='Use proxy for transcript extraction'
        )
        parser.add_argument(
            '--title',
            type=str,
            default='Test Video',
            help='Title for the test content (default: "Test Video")'
        )

    def handle(self, *args, **options):
        video_id = options['video_id']
        source_id = options.get('source_id')
        use_proxy = options.get('use_proxy', False)
        title = options.get('title', 'Test Video')

        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS(f'Testing Transcript Extraction'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
        self.stdout.write(f'Video ID: {video_id}')
        self.stdout.write(f'Video URL: https://www.youtube.com/watch?v={video_id}')
        self.stdout.write(f'Use Proxy: {use_proxy}\n')

        # Get or create source
        source = None
        if source_id:
            try:
                source = Source.objects.get(pk=source_id, source_type='youtube')
                self.stdout.write(f'Using source: {source.name} (ID: {source.id})')
            except Source.DoesNotExist:
                raise CommandError(f'Source with ID {source_id} not found or is not a YouTube source')
        else:
            # Find first YouTube source or create a test one
            source = Source.objects.filter(source_type='youtube').first()
            if not source:
                self.stdout.write(self.style.WARNING('No YouTube source found. Creating a test source...'))
                source = Source.objects.create(
                    name='Test Source',
                    source_type='youtube',
                    channel_id='test',
                    is_active=True
                )
                self.stdout.write(f'Created test source: {source.name} (ID: {source.id})')
            else:
                self.stdout.write(f'Using existing source: {source.name} (ID: {source.id})')

        # Check if content already exists
        existing_content = Content.objects.filter(
            source=source,
            external_id=video_id
        ).first()

        if existing_content:
            self.stdout.write(self.style.WARNING(f'\nContent already exists (ID: {existing_content.id})'))
            self.stdout.write(f'Title: {existing_content.title}')
            self.stdout.write(f'Has content: {existing_content.has_content}')
            if existing_content.content:
                preview = existing_content.content[:200]
                self.stdout.write(f'Content preview: {preview}...')
            
            use_existing = input('\nUse existing content? (y/n): ').strip().lower()
            if use_existing == 'y':
                content = existing_content
                content.title = title  # Update title if provided
                content.save()
            else:
                # Delete and recreate
                existing_content.delete()
                content = self._create_test_content(source, video_id, title)
        else:
            content = self._create_test_content(source, video_id, title)

        self.stdout.write(f'\n{"="*80}')
        self.stdout.write('Attempting to extract transcript...')
        self.stdout.write(f'{"="*80}\n')

        # Check proxy configuration if requested
        if use_proxy:
            self.stdout.write('Checking proxy configuration...')
            try:
                from youtube_transcript_api.proxies import WebshareProxyConfig
                from django.conf import settings
                from pathlib import Path
                import os
                
                # Try to get from environment variables first
                proxy_username = os.environ.get('WEBSHARE_PROXY_USERNAME', '').strip()
                proxy_password = os.environ.get('WEBSHARE_PROXY_PASSWORD', '').strip()
                
                # If not in environment, try to load from .env file
                if not proxy_username or not proxy_password:
                    base_dir = Path(settings.BASE_DIR)
                    env_file = base_dir / '.env'
                    
                    if env_file.exists():
                        self.stdout.write(f'  Reading .env file from: {env_file}')
                        with open(env_file, 'r', encoding='utf-8') as f:
                            for line in f:
                                line = line.strip()
                                if not line or line.startswith('#'):
                                    continue
                                if '=' in line:
                                    key, value = line.split('=', 1)
                                    key = key.strip()
                                    value = value.strip()
                                    if value.startswith('"') and value.endswith('"'):
                                        value = value[1:-1]
                                    elif value.startswith("'") and value.endswith("'"):
                                        value = value[1:-1]
                                    
                                    if key == 'WEBSHARE_PROXY_USERNAME':
                                        proxy_username = value
                                    elif key == 'WEBSHARE_PROXY_PASSWORD':
                                        proxy_password = value
                    else:
                        self.stdout.write(self.style.WARNING(f'  .env file not found at: {env_file}'))
                
                if proxy_username and proxy_password:
                    self.stdout.write(self.style.SUCCESS(f'  ✓ Proxy credentials found'))
                    self.stdout.write(f'  Username: {proxy_username[:10]}... (hidden)')
                    try:
                        proxy_config = WebshareProxyConfig(
                            proxy_username=proxy_username,
                            proxy_password=proxy_password
                        )
                        self.stdout.write(self.style.SUCCESS(f'  ✓ WebshareProxyConfig created successfully'))
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f'  ✗ Failed to create WebshareProxyConfig: {str(e)}'))
                        import traceback
                        self.stdout.write(traceback.format_exc())
                else:
                    self.stdout.write(self.style.WARNING(f'  ⚠ Proxy credentials not found'))
                    self.stdout.write(f'  Username: {"set" if proxy_username else "missing"}')
                    self.stdout.write(f'  Password: {"set" if proxy_password else "missing"}')
            except ImportError:
                self.stdout.write(self.style.WARNING('  ⚠ Proxy support not available (youtube-transcript-api version may be too old)'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  ✗ Error checking proxy config: {str(e)}'))
            
            self.stdout.write('')

        try:
            # Create processing service
            self.stdout.write('Creating ContentProcessingService...')
            processing_service = ContentProcessingService(use_proxy=use_proxy)
            self.stdout.write(self.style.SUCCESS('  ✓ Service created\n'))
            
            # Extract transcript
            self.stdout.write('Extracting transcript...\n')
            success = processing_service.extract_transcript(content, force=True)
            
            # Refresh content from database
            content.refresh_from_db()
            
            if success:
                self.stdout.write(self.style.SUCCESS('\n✓ Transcript extracted successfully!\n'))
                self.stdout.write(f'Content ID: {content.id}')
                self.stdout.write(f'Has content: {content.has_content}')
                self.stdout.write(f'Content length: {len(content.content)} characters')
                self.stdout.write(f'Content length: {len(content.content.split())} words')
                
                # Show preview
                self.stdout.write(f'\n{"="*80}')
                self.stdout.write('Transcript Preview (first 500 characters):')
                self.stdout.write(f'{"="*80}')
                preview = content.content[:500]
                self.stdout.write(preview)
                if len(content.content) > 500:
                    self.stdout.write('...')
                
                # Show last part
                if len(content.content) > 500:
                    self.stdout.write(f'\n{"="*80}')
                    self.stdout.write('Transcript Preview (last 200 characters):')
                    self.stdout.write(f'{"="*80}')
                    self.stdout.write('...' + content.content[-200:])
                
                self.stdout.write(f'\n{"="*80}\n')
                
                # Ask if user wants to keep the content
                keep = input('Keep this content in the database? (y/n): ').strip().lower()
                if keep != 'y':
                    content.delete()
                    self.stdout.write(self.style.WARNING('Content deleted.'))
                else:
                    self.stdout.write(self.style.SUCCESS('Content kept in database.'))
            else:
                self.stdout.write(self.style.ERROR('\n✗ Failed to extract transcript\n'))
                self.stdout.write('Possible reasons:')
                self.stdout.write('  - Transcripts are disabled for this video')
                self.stdout.write('  - No transcript available in any language')
                self.stdout.write('  - Video is unavailable or private')
                self.stdout.write('  - Network/SSL error (try with --use-proxy or without)')
                
                # Clean up test content
                content.delete()
                self.stdout.write(self.style.WARNING('\nTest content deleted.'))

        except Exception as e:
            import traceback
            error_type = type(e).__name__
            error_msg = str(e)
            
            self.stdout.write(self.style.ERROR(f'\n✗ Error extracting transcript'))
            self.stdout.write(self.style.ERROR(f'Error Type: {error_type}'))
            self.stdout.write(self.style.ERROR(f'Error Message: {error_msg}\n'))
            
            # Check for specific error types
            error_lower = error_msg.lower()
            if 'ssl' in error_lower or 'sslerror' in error_lower:
                self.stdout.write(self.style.WARNING('  → This appears to be an SSL error.'))
                self.stdout.write(self.style.WARNING('  → Try running without --use-proxy flag'))
                if use_proxy:
                    self.stdout.write(self.style.WARNING('  → Or check if proxy credentials are correct'))
            elif 'connection' in error_lower or 'eof' in error_lower:
                self.stdout.write(self.style.WARNING('  → This appears to be a connection error.'))
                self.stdout.write(self.style.WARNING('  → The proxy might be having issues'))
                self.stdout.write(self.style.WARNING('  → Try running without --use-proxy flag'))
            elif 'rate limit' in error_lower or '429' in error_lower:
                self.stdout.write(self.style.WARNING('  → This appears to be a rate limiting error.'))
                self.stdout.write(self.style.WARNING('  → Wait a few minutes and try again'))
            elif 'transcript' in error_lower and ('disabled' in error_lower or 'not found' in error_lower):
                self.stdout.write(self.style.WARNING('  → This appears to be a transcript availability issue.'))
                self.stdout.write(self.style.WARNING('  → The video might not have transcripts available'))
            
            self.stdout.write('\nFull Traceback:')
            self.stdout.write('=' * 80)
            self.stdout.write(traceback.format_exc())
            self.stdout.write('=' * 80)
            
            # Clean up test content
            if content and content.id:
                content.delete()
                self.stdout.write(self.style.WARNING('\nTest content deleted.'))

    def _create_test_content(self, source, video_id, title):
        """Create a test content entry"""
        content = Content.objects.create(
            source=source,
            external_id=video_id,
            title=title,
            link=f"https://www.youtube.com/watch?v={video_id}",
            content_type='video',
            date=date.today(),
            content='',
            processed=False,
        )
        self.stdout.write(f'Created test content entry (ID: {content.id})')
        return content

