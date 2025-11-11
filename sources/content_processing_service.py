"""
Service for processing content: extract, translate, tag, and embed
"""
from typing import Optional
from django.db import transaction
from django.conf import settings
from pathlib import Path
import os
from .models import Content, Tag, ContentChunk
from .content_extraction_service import extract_article_content
from .services import TaggingService
from .embedding_service import EmbeddingService
from .utils import log_activity
from urllib.parse import urlparse
import re

# Translation imports
try:
    from deep_translator import GoogleTranslator
    TRANSLATION_AVAILABLE = True
except ImportError:
    TRANSLATION_AVAILABLE = False

# YouTube Transcript API imports
try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, VideoUnavailable
    YOUTUBE_TRANSCRIPT_AVAILABLE = True
except ImportError:
    YOUTUBE_TRANSCRIPT_AVAILABLE = False
    YouTubeTranscriptApi = None

# Proxy support
try:
    from youtube_transcript_api.proxies import WebshareProxyConfig
    PROXY_SUPPORT = True
except ImportError:
    PROXY_SUPPORT = False
    WebshareProxyConfig = None


class ContentProcessingService:
    """Service for processing content through the full pipeline"""
    
    def __init__(self, tagging_provider=None, tagging_model=None, use_proxy=False):
        """
        Initialize the processing service
        
        Args:
            tagging_provider: Provider for tagging ('ollama' or 'openai'). If None, uses settings.
            tagging_model: Model name for tagging (optional). If None, uses settings.
            use_proxy: If True, use Webshare proxies for YouTube transcript fetching (for batch operations)
        """
        # Get settings from database if not provided
        if tagging_provider is None or tagging_model is None:
            from .models import Settings as SettingsModel
            app_settings = SettingsModel.get_settings()
            if tagging_provider is None:
                tagging_provider = app_settings.default_tagging_provider
            if tagging_model is None:
                tagging_model = app_settings.default_tagging_model
        
        self.tagging_service = TaggingService(provider=tagging_provider, model=tagging_model)
        self.embedding_service = EmbeddingService()
        self.use_proxy = use_proxy
        self._proxy_config = None
        
        # Load proxy config if requested
        if use_proxy and PROXY_SUPPORT:
            self._proxy_config = self._load_proxy_config()
    
    def _load_proxy_config(self):
        """
        Load Webshare proxy configuration from .env file or environment variables.
        
        Returns:
            WebshareProxyConfig instance or None
        """
        if not PROXY_SUPPORT:
            return None
        
        # Try to get from environment variables first
        proxy_username = os.environ.get('WEBSHARE_PROXY_USERNAME', '').strip()
        proxy_password = os.environ.get('WEBSHARE_PROXY_PASSWORD', '').strip()
        
        # If not in environment, try to load from .env file
        if not proxy_username or not proxy_password:
            # Try to find .env file in project root
            base_dir = Path(settings.BASE_DIR)
            env_file = base_dir / '.env'
            
            if env_file.exists():
                try:
                    with open(env_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith('#'):
                                continue
                            if '=' in line:
                                key, value = line.split('=', 1)
                                key = key.strip()
                                value = value.strip()
                                # Remove quotes if present
                                if value.startswith('"') and value.endswith('"'):
                                    value = value[1:-1]
                                elif value.startswith("'") and value.endswith("'"):
                                    value = value[1:-1]
                                
                                if key == 'WEBSHARE_PROXY_USERNAME':
                                    proxy_username = value
                                elif key == 'WEBSHARE_PROXY_PASSWORD':
                                    proxy_password = value
                except Exception as e:
                    print(f"Warning: Could not read .env file: {str(e)}")
        
        if proxy_username and proxy_password:
            try:
                return WebshareProxyConfig(
                    proxy_username=proxy_username,
                    proxy_password=proxy_password
                )
            except Exception as e:
                print(f"Warning: Could not create proxy config: {str(e)}")
                return None
        
        return None
    
    def extract_youtube_video_id(self, url: str) -> Optional[str]:
        """
        Extract YouTube video ID from various URL formats.
        
        Args:
            url: YouTube URL
            
        Returns:
            Video ID or None
        """
        if not url:
            return None
        
        url = url.strip()
        
        # Pattern 1: youtube.com/watch?v=VIDEO_ID
        match = re.match(r'(?:youtube\.com/watch\?v=|youtube\.com/watch\?.*&v=)([a-zA-Z0-9_-]{11})', url)
        if match:
            return match.group(1)
        
        # Pattern 2: youtu.be/VIDEO_ID
        match = re.match(r'youtu\.be/([a-zA-Z0-9_-]{11})', url)
        if match:
            return match.group(1)
        
        # Pattern 3: youtube.com/embed/VIDEO_ID
        match = re.match(r'youtube\.com/embed/([a-zA-Z0-9_-]{11})', url)
        if match:
            return match.group(1)
        
        # Pattern 4: youtube.com/v/VIDEO_ID
        match = re.match(r'youtube\.com/v/([a-zA-Z0-9_-]{11})', url)
        if match:
            return match.group(1)
        
        # Pattern 5: If it's just the video ID itself (11 characters)
        if re.match(r'^[a-zA-Z0-9_-]{11}$', url):
            return url
        
        return None
    
    def extract_transcript(self, content: Content, force: bool = False) -> bool:
        """
        Extract transcript from YouTube video if content is empty and link/external_id is available.
        
        Args:
            content: Content object
            force: If True, extract even if content already exists (for re-fetching)
            
        Returns:
            True if transcript was extracted, False otherwise
        """
        # Only extract for videos
        if content.content_type != 'video':
            return False
        
        # Skip if content already exists (unless force is True)
        if not force and content.content and content.content.strip():
            return False
        
        # Check if YouTube Transcript API is available
        if not YOUTUBE_TRANSCRIPT_AVAILABLE:
            print("YouTube Transcript API not available. Install with: pip install youtube-transcript-api")
            return False
        
        # Get video ID from external_id or link
        video_id = content.external_id
        if not video_id and content.link:
            video_id = self.extract_youtube_video_id(content.link)
        
        if not video_id:
            return False
        
        try:
            # Create API instance with proxy config if available
            if self._proxy_config:
                api = YouTubeTranscriptApi(proxy_config=self._proxy_config)
            else:
                api = YouTubeTranscriptApi()
            
            # Get source language for preferred transcript language
            source_language = None
            if content.source and content.source.language:
                lang_map = {
                    'english': 'en',
                    'anglais': 'en',
                    'french': 'fr',
                    'français': 'fr',
                    'chinese': 'zh',
                    'chinois': 'zh',
                    'en': 'en',
                    'fr': 'fr',
                    'zh': 'zh',
                }
                source_language = lang_map.get(content.source.language.lower(), content.source.language.lower())
            
            # Build language list with source language first, then common fallbacks
            languages_to_try = []
            if source_language:
                languages_to_try.append(source_language)
            
            # Add common fallback languages
            fallback_languages = ['en', 'fr', 'zh', 'zh-CN', 'zh-TW', 'es', 'de', 'ja', 'ko', 'ru', 'it', 'pt']
            for lang in fallback_languages:
                if lang not in languages_to_try:
                    languages_to_try.append(lang)
            
            transcript = None
            language_used = None
            
            try:
                # Try to fetch with language priority
                if languages_to_try:
                    transcript = api.fetch(video_id, languages=languages_to_try)
                    language_used = transcript.language_code if hasattr(transcript, 'language_code') else 'unknown'
            except (NoTranscriptFound, TranscriptsDisabled):
                # If that fails, try without specifying languages (auto-detect)
                try:
                    transcript = api.fetch(video_id)
                    language_used = transcript.language_code if hasattr(transcript, 'language_code') else 'auto'
                except (NoTranscriptFound, TranscriptsDisabled) as e:
                    if isinstance(e, TranscriptsDisabled):
                        print(f"Transcripts are disabled for video {video_id}")
                        return False
                    print(f"No transcript found for video {video_id}")
                    return False
            except VideoUnavailable:
                print(f"Video {video_id} is unavailable")
                return False
            
            if transcript is None:
                return False
            
            # Extract text from transcript snippets
            transcript_text = '\n'.join([snippet.text for snippet in transcript.snippets])
            
            if transcript_text and transcript_text.strip():
                content.content = transcript_text.strip()
                content.has_content = True
                
                # Save the extracted transcript
                content.save(update_fields=['content', 'has_content'])
                print(f"Successfully extracted transcript for video {video_id} (language: {language_used})")
                return True
            else:
                print(f"Empty transcript for video {video_id}")
                return False
                
        except Exception as e:
            # Log error but don't fail
            print(f"Error extracting transcript for video {video_id}: {str(e)}")
            return False
        
        return False
    
    def extract_content(self, content: Content, force: bool = False) -> bool:
        """
        Extract content from URL if content is empty and link is available.
        For blog posts: extracts article content from URL
        For videos: extracts transcript from YouTube
        
        Args:
            content: Content object
            force: If True, extract even if content already exists (for re-fetching)
            
        Returns:
            True if content was extracted, False otherwise
        """
        # Handle videos differently (extract transcript)
        if content.content_type == 'video':
            return self.extract_transcript(content, force=force)
        
        # Handle blog posts (extract article content)
        if content.content_type != 'blog_post':
            return False
        
        # Skip if content already exists (unless force is True)
        if not force and content.content and content.content.strip():
            return False
        
        # Skip if no link
        if not content.link:
            return False
        
        try:
            # Extract base URL for Referer header
            parsed_url = urlparse(content.link)
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            
            # Extract content
            result = extract_article_content(content.link, base_url)
            
            if result and result.get('content'):
                content.content = result['content']
                # Set has_content explicitly since we're using update_fields
                content.has_content = True
                
                # Update date if missing and we found one
                if result.get('date') and not content.date:
                    try:
                        from datetime import datetime
                        date_str = result['date']
                        # Parse ISO format date
                        if 'T' in date_str:
                            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                        else:
                            dt = datetime.strptime(date_str, '%Y-%m-%d')
                        content.date = dt.date()
                    except (ValueError, AttributeError):
                        pass
                
                # Save the extracted content (include has_content in update_fields)
                content.save(update_fields=['content', 'date', 'has_content'])
                return True
        except Exception as e:
            # Log error but don't fail
            print(f"Error extracting content from {content.link}: {str(e)}")
            return False
        
        return False
    
    def translate_content(self, content: Content) -> bool:
        """
        Translate content from French to English if source language is French.
        Works for both blog posts and video transcripts.
        
        Args:
            content: Content object
            
        Returns:
            True if content was translated, False otherwise
        """
        # Check if translation is available
        if not TRANSLATION_AVAILABLE:
            print("Translation library not available. Skipping translation.")
            return False
        
        # Check if source is French
        source = content.source
        if not source or source.language.lower() not in ('fr', 'french', 'français'):
            return False
        
        # Skip if no content to translate
        if not content.content or not content.content.strip():
            return False
        
        try:
            content_text = content.content
            chunk_size = 4500  # Max characters per translation request
            translated_chunks = []
            
            print(f"Translating content {content.id} from French to English...")
            
            if len(content_text) <= chunk_size:
                # Small content - translate in one go
                translator = GoogleTranslator(source='fr', target='en')
                translated_text = translator.translate(content_text)
                content.content = translated_text
                print(f"Successfully translated content {content.id}")
                return True
            else:
                # Large content - translate in chunks
                # For transcripts (newline-separated) and blog posts (sentence-separated)
                translator = GoogleTranslator(source='fr', target='en')
                
                # Try splitting by sentences first (for blog posts)
                if '. ' in content_text or '.\n' in content_text:
                    # Split by periods (with space or newline after)
                    import re
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
                else:
                    # For transcripts or content without periods, split by newlines or fixed size
                    lines = content_text.split('\n')
                    current_chunk = ''
                    
                    for line in lines:
                        if len(current_chunk) + len(line) + 1 <= chunk_size:
                            current_chunk += line + '\n' if current_chunk else line
                        else:
                            if current_chunk:
                                translated_chunk = translator.translate(current_chunk)
                                translated_chunks.append(translated_chunk)
                            current_chunk = line + '\n'
                    
                    if current_chunk:
                        translated_chunk = translator.translate(current_chunk)
                        translated_chunks.append(translated_chunk)
                
                # Join translated chunks, preserving structure
                if '\n' in content_text:
                    # Preserve newlines for transcripts
                    content.content = '\n'.join(translated_chunks)
                else:
                    # Join with spaces for blog posts
                    content.content = ' '.join(translated_chunks)
                
                print(f"Successfully translated content {content.id} ({len(translated_chunks)} chunks)")
                return True
        except Exception as e:
            import traceback
            print(f"Error translating content {content.id}: {str(e)}")
            print(traceback.format_exc())
            return False
    
    def add_tags(self, content: Content) -> bool:
        """
        Add tags to content using AI tagging service.
        
        Args:
            content: Content object
            
        Returns:
            True if tags were added, False otherwise
        """
        # Skip if content already has tags
        if content.tags.exists():
            print(f"Content {content.id} already has tags, skipping")
            return False
        
        # Skip if no title
        if not content.title:
            print(f"Content {content.id} has no title, skipping tagging")
            return False
        
        try:
            # Refresh content to get latest content text
            content.refresh_from_db()
            
            # Generate tags
            content_text = content.content if hasattr(content, 'content') else ""
            if not content_text or not content_text.strip():
                print(f"Content {content.id} has no content text, skipping tagging")
                return False
            
            print(f"Generating tags for content {content.id}: {content.title[:50]}")
            generated_tags = self.tagging_service.generate_tags(
                title=content.title,
                content=content_text,
                content_type=content.content_type
            )
            
            if not generated_tags:
                print(f"No tags generated for content {content.id}")
                return False
            
            print(f"Generated {len(generated_tags)} tags: {generated_tags}")
            
            # Get or create tag objects
            tag_objects = []
            for tag_name in generated_tags:
                tag, created = Tag.objects.get_or_create(name=tag_name)
                tag_objects.append(tag)
            
            # Set tags
            content.tags.set(tag_objects)
            print(f"Successfully added tags to content {content.id}")
            
            # Log the tagging activity
            log_activity(
                'content_tagged',
                f'Content "{content.title}" was tagged with {len(generated_tags)} tags',
                content=content,
                source=content.source,
                metadata={'tags': generated_tags}
            )
            
            return True
        except Exception as e:
            import traceback
            print(f"Error adding tags to content {content.id}: {str(e)}")
            print(traceback.format_exc())
            return False
    
    def generate_embeddings(self, content: Content, chunk_size: int = 8000, overlap: int = 200) -> bool:
        """
        Generate embeddings for content.
        
        Args:
            content: Content object
            chunk_size: Maximum characters per chunk
            overlap: Overlap between chunks
            
        Returns:
            True if embeddings were generated, False otherwise
        """
        # Skip if content already has embeddings
        if content.chunks.exists():
            print(f"Content {content.id} already has embeddings, skipping")
            return False
        
        # Refresh content to get latest data
        content.refresh_from_db()
        
        # Skip if no content text
        if not content.content or not content.content.strip():
            print(f"Content {content.id} has no content text, skipping embedding")
            return False
        
        # Skip if no tags (required for embedding context)
        if not content.tags.exists():
            print(f"Content {content.id} has no tags, skipping embedding")
            return False
        
        try:
            # Get tags
            tags = list(content.tags.values_list('name', flat=True))
            print(f"Generating embeddings for content {content.id} with {len(tags)} tags")
            
            # Generate embeddings
            chunk_results = self.embedding_service.generate_embeddings_for_content(
                title=content.title,
                content_text=content.content,
                tags=tags,
                chunk_size=chunk_size,
                overlap=overlap
            )
            
            if not chunk_results:
                print(f"No embeddings generated for content {content.id}")
                return False
            
            print(f"Generated {len(chunk_results)} chunks for content {content.id}")
            
            # Delete existing chunks if any
            content.chunks.all().delete()
            
            # Create chunks with embeddings
            chunks_to_create = []
            for idx, (chunk_text, embedding) in enumerate(chunk_results):
                if embedding:  # Only create chunks with valid embeddings
                    chunks_to_create.append(
                        ContentChunk(
                            content=content,
                            chunk_index=idx,
                            text=chunk_text,
                            embedding=embedding
                        )
                    )
            
            if chunks_to_create:
                ContentChunk.objects.bulk_create(chunks_to_create)
                content.processed = True
                content.save(update_fields=['processed'])
                print(f"Successfully created {len(chunks_to_create)} chunks with embeddings for content {content.id}")
                
                # Log the embedding activity
                log_activity(
                    'embeddings_generated',
                    f'Generated embeddings for content "{content.title}" ({len(chunks_to_create)} chunks)',
                    content=content,
                    source=content.source,
                    metadata={'chunks': len(chunks_to_create)}
                )
                
                return True
            else:
                print(f"No valid chunks created for content {content.id}")
                return False
        except Exception as e:
            import traceback
            print(f"Error generating embeddings for content {content.id}: {str(e)}")
            print(traceback.format_exc())
            return False
    
    def process_content(self, content: Content, extract: bool = True, translate: bool = True, 
                       tag: bool = True, embed: bool = True) -> dict:
        """
        Process content through the full pipeline.
        
        Args:
            content: Content object
            extract: Whether to extract content from URL
            translate: Whether to translate French content
            tag: Whether to add tags
            embed: Whether to generate embeddings
            
        Returns:
            Dict with processing results
        """
        results = {
            'extracted': False,
            'translated': False,
            'tagged': False,
            'embedded': False,
        }
        
        # Step 1: Extract content
        if extract:
            results['extracted'] = self.extract_content(content)
            # Content is saved inside extract_content, refresh to get updated content
            if results['extracted']:
                content.refresh_from_db()
        
        # Step 2: Translate if French
        if translate:
            results['translated'] = self.translate_content(content)
            if results['translated']:
                content.save(update_fields=['content'])
                content.refresh_from_db()
        
        # Step 3: Add tags (needs content to be present)
        if tag:
            # Refresh to ensure we have latest content
            content.refresh_from_db()
            # Only tag if we have content
            if content.content and content.content.strip():
                results['tagged'] = self.add_tags(content)
                # Tags are saved via ManyToMany, refresh to get updated tags
                if results['tagged']:
                    content.refresh_from_db()
            else:
                print(f"Skipping tagging for content {content.id}: no content text")
        
        # Step 4: Generate embeddings (needs tags)
        if embed:
            # Refresh to ensure we have latest content and tags
            content.refresh_from_db()
            # Only embed if we have content and tags
            if content.content and content.content.strip():
                if content.tags.exists():
                    results['embedded'] = self.generate_embeddings(content)
                    if results['embedded']:
                        content.save(update_fields=['processed'])
                else:
                    print(f"Skipping embedding for content {content.id}: no tags")
            else:
                print(f"Skipping embedding for content {content.id}: no content text")
        
        return results

