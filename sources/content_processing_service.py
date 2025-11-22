"""
Service for processing content: extract, translate, tag, and embed
"""
from typing import Optional, Tuple
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


def format_transcript_for_translation(text: str) -> str:
    """
    Format transcript text by removing line breaks that are not at sentence endings.
    This improves translation quality by providing better context to the translator.
    
    Example:
        Input: "Hello everyone. So the weather is nice\ntoday as you can see.\nIf it's blue, it's fall. The\nJink are losing their\nleaves but not quite yet\ngolden."
        Output: "Hello everyone. So the weather is nice today as you can see.\nIf it's blue, it's fall. The Jink are losing their leaves but not quite yet golden."
    
    Args:
        text: Raw transcript text with many line breaks
        
    Returns:
        Formatted text with line breaks only after sentence endings
    """
    if not text:
        return text
    
    lines = text.split('\n')
    formatted_lines = []
    current_sentence = ''
    
    for line in lines:
        line = line.strip()
        if not line:
            # Empty line - if we have accumulated text, add it and preserve the paragraph break
            if current_sentence:
                formatted_lines.append(current_sentence.strip())
                current_sentence = ''
            # Preserve empty lines as paragraph breaks
            if formatted_lines and formatted_lines[-1]:  # Only add if previous line wasn't empty
                formatted_lines.append('')
            continue
        
        # Add line to current sentence
        current_sentence += (' ' if current_sentence else '') + line
        
        # Check if the line ends with sentence punctuation
        # If so, this is the end of a sentence - add to formatted_lines and start a new sentence
        if line and line[-1] in '.!?。！？':
            formatted_lines.append(current_sentence.strip())
            current_sentence = ''
    
    # Add any remaining text
    if current_sentence:
        formatted_lines.append(current_sentence.strip())
    
    # Join lines, preserving paragraph breaks (double newlines)
    result = []
    for i, line in enumerate(formatted_lines):
        if line == '':
            # Empty line - check if we should add it (avoid consecutive empty lines)
            if i == 0 or formatted_lines[i-1] != '':
                result.append('')
        else:
            result.append(line)
    
    return '\n'.join(result).strip()


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
        self._requests_proxies = None  # For requests library (blog posts, sitemaps)
        
        # Load proxy config if requested
        if use_proxy and PROXY_SUPPORT:
            self._proxy_config = self._load_proxy_config()
            if self._proxy_config:
                print(f"  [PROXY] Proxy config loaded successfully", flush=True)
            else:
                print(f"  [PROXY] Warning: use_proxy=True but proxy config could not be loaded", flush=True)
        elif use_proxy and not PROXY_SUPPORT:
            print(f"  [PROXY] Warning: use_proxy=True but proxy support not available (youtube-transcript-api version may be too old)", flush=True)
        
        # Also load requests-format proxies for blog post extraction
        if use_proxy:
            self._requests_proxies = self._load_requests_proxies()
            if self._requests_proxies:
                print(f"  [PROXY] Requests proxies loaded successfully", flush=True)
    
    def _load_proxy_config(self):
        """
        Load Webshare proxy configuration from .env file or environment variables.
        Uses the same logic as get_transcripts.py for consistency.
        
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
                    # Use the same parsing logic as get_transcripts.py
                    with open(env_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            # Skip empty lines and comments
                            if not line or line.startswith('#'):
                                continue
                            
                            # Split by = sign, handling quoted values
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
                    print(f"  [PROXY] Warning: Could not read .env file: {str(e)}", flush=True)
            else:
                print(f"  [PROXY] Warning: .env file not found at {env_file}", flush=True)
        
        if proxy_username and proxy_password:
            try:
                proxy_config = WebshareProxyConfig(
                    proxy_username=proxy_username,
                    proxy_password=proxy_password
                )
                print(f"  [PROXY] Successfully created WebshareProxyConfig", flush=True)
                return proxy_config
            except Exception as e:
                print(f"  [PROXY] Error creating proxy config: {str(e)}", flush=True)
                import traceback
                print(f"  [PROXY] Traceback: {traceback.format_exc()}", flush=True)
                return None
        else:
            print(f"  [PROXY] Warning: Proxy credentials not found (username: {'set' if proxy_username else 'missing'}, password: {'set' if proxy_password else 'missing'})", flush=True)
            return None
    
    def _load_requests_proxies(self):
        """
        Load Webshare proxy configuration in requests library format.
        Uses Webshare API v2 to fetch proxy list.
        
        Returns:
            Dict with 'http' and 'https' proxy URLs for requests library, or None
        """
        # Try to get from environment variables first
        api_token = os.environ.get('WEBSHARE_API_TOKEN', '').strip()
        proxy_username = os.environ.get('WEBSHARE_PROXY_USERNAME', '').strip()
        proxy_password = os.environ.get('WEBSHARE_PROXY_PASSWORD', '').strip()
        
        # If not in environment, try to load from .env file
        if not api_token and (not proxy_username or not proxy_password):
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
                                
                                if key == 'WEBSHARE_API_TOKEN':
                                    api_token = value
                                elif key == 'WEBSHARE_PROXY_USERNAME':
                                    proxy_username = value
                                elif key == 'WEBSHARE_PROXY_PASSWORD':
                                    proxy_password = value
                except Exception:
                    pass
        
        # Use API token if available, otherwise use username/password
        if not api_token and (not proxy_username or not proxy_password):
            return None
        
        # Use API token if available, otherwise username will be used as token
        token_to_use = api_token if api_token else proxy_username
        
        if not token_to_use or not token_to_use.strip():
            return None
        
        # Fetch proxy list from Webshare API
        try:
            import requests
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            api_url = 'https://proxy.webshare.io/api/v2/proxy/list/'
            headers = {
                'Authorization': f'Token {token_to_use}'
            }
            
            # Try backbone mode first, then fallback to other modes
            modes_to_try = ['backbone', None, 'backconnect', 'datacenter', 'direct']
            response = None
            
            for mode in modes_to_try:
                params = {
                    'page': 1,
                    'page_size': 25,  # Fetch multiple proxies for rotation
                }
                if mode:
                    params['mode'] = mode
                
                try:
                    test_response = requests.get(api_url, headers=headers, params=params, timeout=10, verify=False)
                except requests.exceptions.SSLError:
                    test_response = requests.get(api_url, headers=headers, params=params, timeout=10, verify=False)
                
                if test_response.status_code == 200:
                    response = test_response
                    break
                elif test_response.status_code == 400:
                    continue
                else:
                    continue
            
            # If all modes failed and we have username/password, try basic auth as fallback
            if (not response or (hasattr(response, 'status_code') and response.status_code != 200)) and not api_token and proxy_username and proxy_password:
                params = {'page': 1, 'page_size': 25}
                try:
                    auth = (proxy_username, proxy_password)
                    response = requests.get(api_url, auth=auth, params=params, timeout=10, verify=False)
                except:
                    pass
            
            if response and hasattr(response, 'status_code') and response.status_code == 200:
                data = response.json()
                results = data.get('results', [])
                
                if results:
                    import random
                    # Select a random proxy from the list for better distribution
                    proxy = random.choice(results)
                    proxy_address = proxy.get('proxy_address')
                    port = proxy.get('port')
                    username = proxy.get('username')
                    password = proxy.get('password')
                    
                    # For backbone proxies, proxy_address can be null, use p.webshare.io as default
                    if not proxy_address:
                        proxy_address = 'p.webshare.io'
                    
                    if proxy_address and port and username and password:
                        proxy_url = f'http://{username}:{password}@{proxy_address}:{port}'
                        proxies = {
                            'http': proxy_url,
                            'https': proxy_url
                        }
                        return proxies
        except Exception:
            pass
        
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
    
    def extract_transcript(self, content: Content, force: bool = False, user=None, transcript_text: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """
        Extract transcript from YouTube video if content is empty and link/external_id is available.
        
        Args:
            content: Content object
            force: If True, extract even if content already exists (for re-fetching)
            user: Optional user object for activity logging
            transcript_text: Optional pre-fetched transcript text to use directly
            
        Returns:
            Tuple of (success: bool, language_code: str or None)
            If successful: (True, language_code)
            If failed: (False, None)
        """
        # Only extract for videos
        if content.content_type != 'video':
            print(f"  [EXTRACT] Skipping: content type is {content.content_type}, not 'video'", flush=True)
            return False, None
        
        # Skip if content already exists (unless force is True)
        if not force and content.content and content.content.strip():
            print(f"  [EXTRACT] Skipping: content already exists for video {content.external_id}", flush=True)
            return False, None
        
        # If transcript_text is provided, use it directly (no need to fetch)
        if transcript_text:
            print(f"  [EXTRACT] Using pre-fetched transcript for video {content.external_id}...", flush=True)
            content.content = transcript_text
            content.has_content = True
            content.save(update_fields=['content', 'has_content'])
            
            # Log activity
            log_activity(
                'transcript_extracted',
                f'Extracted transcript for video "{content.title}" (pre-fetched)',
                user=user,
                content=content,
                metadata={'video_id': content.external_id, 'chars': len(transcript_text)}
            )
            
            print(f"  [EXTRACT] ✓ Successfully saved pre-fetched transcript for video {content.external_id} ({len(transcript_text)} chars)", flush=True)
            # Return None for language since we don't know it for pre-fetched transcripts
            return True, None
        
        # Check if YouTube Transcript API is available
        if not YOUTUBE_TRANSCRIPT_AVAILABLE:
            print("  [EXTRACT] ERROR: YouTube Transcript API not available. Install with: pip install youtube-transcript-api", flush=True)
            return False, None
        
        # Get video ID from external_id or link
        video_id = content.external_id
        if not video_id and content.link:
            video_id = self.extract_youtube_video_id(content.link)
        
        if not video_id:
            print(f"  [EXTRACT] ERROR: No video ID found for content {content.id}", flush=True)
            return False, None
        
        print(f"  [EXTRACT] Attempting to extract transcript for video {video_id}...", flush=True)
        
        try:
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
            
            # Try with proxy first if configured, then fallback to no proxy on SSL/connection errors
            api_configs_to_try = []
            if self._proxy_config:
                try:
                    api_with_proxy = YouTubeTranscriptApi(proxy_config=self._proxy_config)
                    api_configs_to_try.append(('with proxy', api_with_proxy))
                    print(f"  [EXTRACT] Proxy config available, will try with proxy first", flush=True)
                except Exception as e:
                    print(f"  [EXTRACT] Warning: Failed to create API instance with proxy: {str(e)}", flush=True)
                    print(f"  [EXTRACT] Will try without proxy only", flush=True)
            api_configs_to_try.append(('without proxy', YouTubeTranscriptApi()))
            
            for config_name, api in api_configs_to_try:
                try:
                    print(f"  [EXTRACT] Trying {config_name}...", flush=True)
                    
                    try:
                        # Try to fetch with language priority
                        if languages_to_try:
                            print(f"  [EXTRACT] Trying languages: {languages_to_try[:5]}...", flush=True)
                            transcript = api.fetch(video_id, languages=languages_to_try)
                            language_used = transcript.language_code if hasattr(transcript, 'language_code') else 'unknown'
                        else:
                            transcript = api.fetch(video_id)
                            language_used = transcript.language_code if hasattr(transcript, 'language_code') else 'auto'
                    except (NoTranscriptFound, TranscriptsDisabled):
                        # If that fails, try without specifying languages (auto-detect)
                        print(f"  [EXTRACT] Language-specific fetch failed, trying auto-detect...", flush=True)
                        try:
                            transcript = api.fetch(video_id)
                            language_used = transcript.language_code if hasattr(transcript, 'language_code') else 'auto'
                        except (NoTranscriptFound, TranscriptsDisabled) as e:
                            if isinstance(e, TranscriptsDisabled):
                                print(f"  [EXTRACT] ERROR: Transcripts are disabled for video {video_id}", flush=True)
                                return False, None
                            print(f"  [EXTRACT] ERROR: No transcript found for video {video_id}", flush=True)
                            return False, None
                    except VideoUnavailable:
                        print(f"  [EXTRACT] ERROR: Video {video_id} is unavailable", flush=True)
                        return False, None
                    
                    # If we got here, we successfully fetched the transcript
                    print(f"  [EXTRACT] Successfully fetched transcript {config_name}", flush=True)
                    
                    # Add a small delay after successful fetch to avoid overwhelming proxy/YouTube
                    import time
                    time.sleep(0.5)  # 500ms delay between requests
                    
                    break
                    
                except Exception as e:
                    # Check if it's an SSL/connection error that might be proxy-related
                    error_str = str(e).lower()
                    is_ssl_error = (
                        'ssl' in error_str or 
                        'sslerror' in error_str or 
                        'connection' in error_str or
                        'eof' in error_str or
                        'retries exceeded' in error_str
                    )
                    
                    if is_ssl_error and config_name == 'with proxy' and len(api_configs_to_try) > 1:
                        # SSL/connection error with proxy, try without proxy
                        print(f"  [EXTRACT] SSL/Connection error {config_name}, will retry without proxy...", flush=True)
                        continue
                    else:
                        # Re-raise if it's not a proxy-related error or we've already tried both
                        raise
            
            # Check if we successfully got a transcript
            if transcript is None:
                print(f"  [EXTRACT] ERROR: Failed to fetch transcript for video {video_id} (tried all configurations)", flush=True)
                return False, None
            
            # Extract text from transcript snippets
            transcript_text = '\n'.join([snippet.text for snippet in transcript.snippets])
            
            if transcript_text and transcript_text.strip():
                content.content = transcript_text.strip()
                content.has_content = True
                
                # Save the extracted transcript
                content.save(update_fields=['content', 'has_content'])
                print(f"  [EXTRACT] ✓ Successfully extracted transcript for video {video_id} (language: {language_used}, {len(transcript_text)} chars)", flush=True)
                
                # Log successful transcript fetch
                log_activity(
                    'transcript_fetched',
                    f'Transcript fetched for video "{content.title}" (ID: {video_id})',
                    user=user,
                    content=content,
                    source=content.source,
                    metadata={'video_id': video_id, 'language': language_used, 'char_count': len(transcript_text)}
                )
                
                # Return True and the language code for potential translation
                return True, language_used
            else:
                print(f"  [EXTRACT] ERROR: Empty transcript for video {video_id}", flush=True)
                # Log failed transcript fetch
                log_activity(
                    'transcript_fetched',
                    f'Failed to fetch transcript for video "{content.title}" (ID: {video_id}): Empty transcript',
                    user=user,
                    content=content,
                    source=content.source,
                    metadata={'success': False, 'video_id': video_id, 'reason': 'Empty transcript'}
                )
                return False, None
        except Exception as e:
            # Log error but don't fail
            import traceback
            error_msg = str(e)
            print(f"  [EXTRACT] ERROR: Exception extracting transcript for video {video_id}: {error_msg}", flush=True)
            print(f"  [EXTRACT] Traceback: {traceback.format_exc()}", flush=True)
            
            # Log failed transcript fetch
            log_activity(
                'transcript_fetched',
                f'Error fetching transcript for video "{content.title}" (ID: {video_id}): {error_msg}',
                user=user,
                content=content,
                source=content.source,
                metadata={'success': False, 'video_id': video_id, 'error': error_msg}
            )
            return False, None
    
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
            extracted, _ = self.extract_transcript(content, force=force)
            return extracted
        
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
            
            # Extract content (pass proxies if available)
            print(f"  [EXTRACT] Starting content extraction for: {content.link}", flush=True)
            result = extract_article_content(content.link, base_url, proxies=self._requests_proxies)
            
            # Check for errors in result
            if result and result.get('error'):
                error_msg = result.get('error', 'Unknown error')
                print(f"  [EXTRACT] ✗ Extraction failed: {error_msg}", flush=True)
                # Log failed content fetch with detailed error
                log_activity(
                    'content_fetched',
                    f'Failed to fetch content from "{content.link}" for "{content.title}": {error_msg}',
                    content=content,
                    source=content.source,
                    metadata={'success': False, 'url': content.link, 'error': error_msg, 'status_code': result.get('status_code')}
                )
                return False
            
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
                
                # Log successful content fetch
                log_activity(
                    'content_fetched',
                    f'Content fetched from "{content.link}" for "{content.title}"',
                    content=content,
                    source=content.source,
                    metadata={'url': content.link, 'char_count': len(content.content)}
                )
                
                return True
            else:
                # Log failed content fetch (no result or empty content)
                log_activity(
                    'content_fetched',
                    f'Failed to fetch content from "{content.link}" for "{content.title}": No content extracted',
                    content=content,
                    source=content.source,
                    metadata={'success': False, 'url': content.link, 'reason': 'No content extracted'}
                )
                return False
        except Exception as e:
            # Log error but don't fail
            error_msg = str(e)
            print(f"Error extracting content from {content.link}: {error_msg}")
            
            # Log failed content fetch
            log_activity(
                'content_fetched',
                f'Error fetching content from "{content.link}" for "{content.title}": {error_msg}',
                content=content,
                source=content.source,
                metadata={'success': False, 'url': content.link, 'error': error_msg}
            )
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
            
            # Format transcript text before translation if this is a video (transcript)
            # This removes unnecessary line breaks to improve translation quality
            if content.content_type == 'video':
                content_text = format_transcript_for_translation(content_text)
                print(f"Formatted transcript for better translation quality")
            
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
    
    def translate_to_english(self, content: Content, source_language: Optional[str] = None) -> bool:
        """
        Translate content to English from any language.
        Uses auto-detect if source_language is not provided.
        Works for both blog posts and video transcripts.
        
        Args:
            content: Content object
            source_language: Optional source language code (e.g., 'fr', 'zh'). If None, uses auto-detect.
            
        Returns:
            True if content was translated, False otherwise
        """
        # Check if translation is available
        if not TRANSLATION_AVAILABLE:
            print("Translation library not available. Skipping translation.")
            return False
        
        # Skip if no content to translate
        if not content.content or not content.content.strip():
            return False
        
        try:
            content_text = content.content
            
            # Format transcript text before translation if this is a video (transcript)
            # This removes unnecessary line breaks to improve translation quality
            if content.content_type == 'video':
                content_text = format_transcript_for_translation(content_text)
                print(f"Formatted transcript for better translation quality")
            
            chunk_size = 4500  # Max characters per translation request
            translated_chunks = []
            
            # Determine source language
            if source_language:
                # Normalize language code (handle variants like 'zh-CN', 'zh-TW')
                source_lang = source_language.split('-')[0].lower()  # Get base language code
                print(f"Translating content {content.id} from {source_lang} to English...")
            else:
                source_lang = 'auto'
                print(f"Translating content {content.id} to English (auto-detect source language)...")
            
            if len(content_text) <= chunk_size:
                # Small content - translate in one go
                translator = GoogleTranslator(source=source_lang, target='en')
                translated_text = translator.translate(content_text)
                content.content = translated_text
                content.save(update_fields=['content'])
                print(f"Successfully translated content {content.id} to English")
                return True
            else:
                # Large content - translate in chunks
                translator = GoogleTranslator(source=source_lang, target='en')
                
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
                
                content.save(update_fields=['content'])
                print(f"Successfully translated content {content.id} to English ({len(translated_chunks)} chunks)")
                return True
        except Exception as e:
            import traceback
            print(f"Error translating content {content.id} to English: {str(e)}")
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
            'errors': []
        }
        
        # Step 1: Extract content
        if extract:
            print(f"  [PROCESS] Step 1: Extracting content...", flush=True)
            try:
                results['extracted'] = self.extract_content(content)
                # Content is saved inside extract_content, refresh to get updated content
                if results['extracted']:
                    content.refresh_from_db()
                    print(f"  [PROCESS] Step 1: ✓ Content extracted", flush=True)
                else:
                    print(f"  [PROCESS] Step 1: ✗ Content extraction failed", flush=True)
                    results['errors'].append('Content extraction failed - check server logs for details')
            except Exception as e:
                print(f"  [PROCESS] Step 1: ✗ Exception during extraction: {str(e)}", flush=True)
                results['errors'].append(f'Extraction error: {str(e)}')
        
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

