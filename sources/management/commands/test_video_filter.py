"""
Management command to test video filtering for China relevance
"""
import json
import os
import re
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from typing import List, Optional, Tuple
from django.conf import settings
from sources.youtube_service import get_channel_videos, is_video_relevant_to_china, get_youtube_api_key

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


class Command(BaseCommand):
    help = 'Test video filtering for China relevance on a YouTube channel'

    def add_arguments(self, parser):
        parser.add_argument(
            'channel_id',
            type=str,
            help='YouTube channel ID to test'
        )
        parser.add_argument(
            '--max-videos',
            type=int,
            default=20,
            help='Maximum number of videos to test (default: 20)'
        )
        parser.add_argument(
            '--include-shorts',
            action='store_true',
            help='Include YouTube Shorts in the test'
        )
        parser.add_argument(
            '--use-ollama',
            action='store_true',
            help='Use Ollama AI to analyze transcripts for relevance (requires transcript fetching)'
        )
        parser.add_argument(
            '--ollama-model',
            type=str,
            default=None,
            help='Ollama model to use (default: from settings or gpt-oss:20b-cloud)'
        )
        parser.add_argument(
            '--skip-transcript-failures',
            action='store_true',
            help='Continue testing even if transcript cannot be fetched (fallback to keyword-only)'
        )

    def handle(self, *args, **options):
        channel_id = options['channel_id']
        max_videos = options['max_videos']
        include_shorts = options['include_shorts']
        use_ollama = options['use_ollama']
        ollama_model = options['ollama_model']
        skip_transcript_failures = options['skip_transcript_failures']

        # Check API key
        api_key = get_youtube_api_key()
        if not api_key:
            raise CommandError(
                'YouTube API key is required. Set YOUTUBE_API_KEY in settings or environment.'
            )

        # Load proxy config for transcript fetching
        self._proxy_config = self._load_proxy_config()
        if self._proxy_config:
            self.stdout.write(self.style.SUCCESS('Proxy configuration loaded for transcript fetching'))
        else:
            self.stdout.write(self.style.WARNING('No proxy configuration found - transcript fetching may fail on VPS/cloud IPs'))

        # Check Ollama availability if requested
        if use_ollama:
            if not YOUTUBE_TRANSCRIPT_AVAILABLE:
                raise CommandError(
                    'YouTube Transcript API is required for Ollama analysis. '
                    'Install with: pip install youtube-transcript-api'
                )
            # Get model from settings if not provided
            if not ollama_model:
                try:
                    from sources.models import Settings
                    app_settings = Settings.get_settings()
                    ollama_model = app_settings.default_tagging_model
                except Exception:
                    ollama_model = 'gpt-oss:20b-cloud'
            self.stdout.write(self.style.SUCCESS(f'Using Ollama model: {ollama_model}'))

        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS(f'Testing Video Filter for Channel: {channel_id}'))
        if use_ollama:
            self.stdout.write(self.style.SUCCESS(f'Mode: Keyword-based + Ollama AI (with transcript)'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Mode: Keyword-based only'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))

        try:
            # Fetch videos without filtering
            self.stdout.write('Fetching videos from YouTube...')
            videos = get_channel_videos(
                channel_id=channel_id,
                include_shorts=include_shorts,
                filter_china=False,  # Don't filter, we want to test all
                api_key=api_key
            )

            if not videos:
                self.stdout.write(self.style.WARNING('No videos found for this channel.'))
                return

            # Limit to max_videos
            videos = videos[:max_videos]

            self.stdout.write(self.style.SUCCESS(f'Found {len(videos)} video(s) to test\n'))

            # Test each video
            keyword_relevant_count = 0
            keyword_not_relevant_count = 0
            ollama_relevant_count = 0
            ollama_not_relevant_count = 0
            transcript_fetched_count = 0
            transcript_failed_count = 0

            for i, video in enumerate(videos, 1):
                video_id = video['video_id']
                title = video['title']
                description_full = video.get('description', '')  # Full description for testing
                description_preview = description_full[:200]  # Truncated for display only
                tags = video.get('tags', [])

                # Check relevance using keyword-based method (same as actual filter)
                is_relevant_keyword = is_video_relevant_to_china(title, description_full, tags)
                matched_keywords = self._find_matched_keywords(title, description_full, tags)

                # Fetch transcript and check with Ollama if requested
                transcript = None
                transcript_text = None
                is_relevant_ollama = None
                ollama_reasoning = None
                
                if use_ollama:
                    transcript_text, error_msg = self._fetch_transcript(video_id)
                    if transcript_text:
                        transcript_fetched_count += 1
                        is_relevant_ollama, ollama_reasoning = self._check_relevance_with_ollama(
                            title, description_full, tags, transcript_text, ollama_model
                        )
                    else:
                        # Transcript unavailable - treat as not relevant
                        transcript_failed_count += 1
                        is_relevant_ollama = False
                        ollama_reasoning = f"Transcript unavailable: {error_msg}"
                        if not skip_transcript_failures:
                            # Still show the video but mark it as not relevant
                            pass
                        else:
                            self.stdout.write(self.style.WARNING(f'  WARNING: Could not fetch transcript: {error_msg} (marked as not relevant)'))

                # Display result
                self.stdout.write(f'\n[{i}/{len(videos)}] {title[:60]}{"..." if len(title) > 60 else ""}')
                self.stdout.write(f'  Video ID: {video_id}')
                self.stdout.write(f'  Link: {video.get("link", "N/A")}')

                # Keyword-based result
                if is_relevant_keyword:
                    keyword_relevant_count += 1
                    self.stdout.write(self.style.SUCCESS(f'  [KEYWORD] ✓ RELEVANT'))
                    if matched_keywords:
                        self.stdout.write(f'    Matched keywords: {", ".join(matched_keywords)}')
                else:
                    keyword_not_relevant_count += 1
                    self.stdout.write(self.style.WARNING(f'  [KEYWORD] ✗ NOT RELEVANT'))

                # Ollama-based result
                if use_ollama and is_relevant_ollama is not None:
                    if is_relevant_ollama:
                        ollama_relevant_count += 1
                        self.stdout.write(self.style.SUCCESS(f'  [OLLAMA] ✓ RELEVANT'))
                        if ollama_reasoning:
                            # Truncate reasoning if too long
                            reasoning_preview = ollama_reasoning[:200] + "..." if len(ollama_reasoning) > 200 else ollama_reasoning
                            self.stdout.write(f'    Reasoning: {reasoning_preview}')
                    else:
                        ollama_not_relevant_count += 1
                        self.stdout.write(self.style.WARNING(f'  [OLLAMA] ✗ NOT RELEVANT'))
                        if ollama_reasoning:
                            reasoning_preview = ollama_reasoning[:200] + "..." if len(ollama_reasoning) > 200 else ollama_reasoning
                            self.stdout.write(f'    Reasoning: {reasoning_preview}')

                # Show transcript info if fetched
                if transcript_text:
                    transcript_preview = transcript_text[:150] + "..." if len(transcript_text) > 150 else transcript_text
                    self.stdout.write(f'  Transcript preview: {transcript_preview}')

                if description_preview:
                    self.stdout.write(f'  Description preview: {description_preview}...')
                if tags:
                    self.stdout.write(f'  Tags: {", ".join(tags[:5])}{"..." if len(tags) > 5 else ""}')

            # Summary
            self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
            self.stdout.write(self.style.SUCCESS('Summary:'))
            self.stdout.write(f'  Total videos tested: {len(videos)}')
            
            # Keyword-based summary
            self.stdout.write(self.style.SUCCESS('\n  [KEYWORD-BASED FILTERING]:'))
            self.stdout.write(self.style.SUCCESS(f'    Relevant: {keyword_relevant_count}'))
            self.stdout.write(self.style.WARNING(f'    Not relevant: {keyword_not_relevant_count}'))
            if len(videos) > 0:
                keyword_percentage = (keyword_relevant_count / len(videos)) * 100
                self.stdout.write(f'    Relevance rate: {keyword_percentage:.1f}%')
            
            # Ollama-based summary
            if use_ollama:
                self.stdout.write(self.style.SUCCESS('\n  [OLLAMA AI FILTERING (with transcript)]:'))
                self.stdout.write(f'    Transcripts fetched: {transcript_fetched_count}')
                if transcript_failed_count > 0:
                    self.stdout.write(self.style.WARNING(f'    Transcripts failed: {transcript_failed_count}'))
                if transcript_fetched_count > 0:
                    self.stdout.write(self.style.SUCCESS(f'    Relevant: {ollama_relevant_count}'))
                    self.stdout.write(self.style.WARNING(f'    Not relevant: {ollama_not_relevant_count}'))
                    ollama_percentage = (ollama_relevant_count / transcript_fetched_count) * 100
                    self.stdout.write(f'    Relevance rate: {ollama_percentage:.1f}%')
                
                # Comparison
                if transcript_fetched_count > 0:
                    self.stdout.write(self.style.SUCCESS('\n  [COMPARISON]:'))
                    # Count agreements and disagreements
                    # Note: We can only compare videos where we have both results
                    # For simplicity, we'll just show the counts
                    self.stdout.write(f'    Videos analyzed by both methods: {transcript_fetched_count}')
            
            self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))

        except Exception as e:
            raise CommandError(f'Error: {str(e)}')

    def _find_matched_keywords(self, title: str, description: str = '', tags: List[str] = None) -> List[str]:
        """
        Find which China-related keywords matched in the video.
        
        Returns:
            List of matched keywords
        """
        if tags is None:
            tags = []

        # Use the same keywords as in youtube_service.py
        # (duplicated here for testing purposes to show which keywords matched)
        
        # Single-word keywords that need word boundaries
        single_word_keywords = [
            'china', 'chinese', 'chinois', 'chine',
            'beijing', 'peking', 'pékin',
            'shanghai', 'shanghaï',
            'guangzhou', 'canton',
            'shenzhen',
            'taiwan', 'taipei',
            'tibet', 'tibetan', 'tibetain',
            'xinjiang', 'xingjiang',
            'terracotta',
            'yangtze',
            'confucius', 'confucian',
            'buddhism', 'buddhist',
            'daoism', 'taoism',
            'mandarin', 'putonghua',
            'cantonese',
            'han',
            'ming',
            'qing',
            'tang',
            'song',
            'yuan',
            'mao',
            'ccp',
            'panda',
            'dragon', 'phoenix',
            'kungfu',
            'dumpling', 'wonton',
            'zhongguo', '中国', '中文',
        ]
        
        # Multi-word phrases
        multi_word_keywords = [
            'hong kong', 'hongkong', 'hong-kong',
            'great wall', 'greatwall',
            'forbidden city', 'forbiddencity',
            'terracotta army',
            'yangtze river',
            'yellow river', 'huang he',
            'han chinese',
            'mao zedong',
            'communist party',
            'giant panda',
            'silk road', 'silkroad',
            'kung fu', 'martial arts',
            'dim sum',
            'tea ceremony', 'chinese tea',
            'chinese new year', 'lunar new year',
            'spring festival',
        ]

        search_text = f"{title} {description} {' '.join(tags)}".lower()
        matched = []

        # Check single-word keywords with word boundaries
        for keyword in single_word_keywords:
            pattern = r'\b' + re.escape(keyword.lower()) + r'\b'
            if re.search(pattern, search_text, re.IGNORECASE):
                matched.append(keyword)
        
        # Check multi-word phrases
        for keyword in multi_word_keywords:
            if keyword.lower() in search_text:
                matched.append(keyword)

        return matched

    def _load_proxy_config(self):
        """
        Load Webshare proxy configuration from .env file or environment variables.
        Uses the same logic as content_processing_service.py for consistency.
        
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
                    pass  # Silently fail, will try without proxy
        
        if proxy_username and proxy_password:
            try:
                proxy_config = WebshareProxyConfig(
                    proxy_username=proxy_username,
                    proxy_password=proxy_password
                )
                return proxy_config
            except Exception:
                return None
        
        return None

    def _fetch_transcript(self, video_id: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Fetch transcript for a YouTube video.
        Tries with proxy first if available, then falls back to no proxy.
        
        Args:
            video_id: YouTube video ID
            
        Returns:
            Tuple of (transcript_text, error_message)
            If successful: (transcript_text, None)
            If failed: (None, error_message)
        """
        if not YOUTUBE_TRANSCRIPT_AVAILABLE:
            return None, "YouTube Transcript API not available"
        
        # Try with proxy first if configured, then fallback to no proxy
        api_configs_to_try = []
        if self._proxy_config:
            try:
                api_with_proxy = YouTubeTranscriptApi(proxy_config=self._proxy_config)
                api_configs_to_try.append(('with proxy', api_with_proxy))
            except Exception:
                pass  # If proxy config fails, try without proxy
        api_configs_to_try.append(('without proxy', YouTubeTranscriptApi()))
        
        last_error = None
        for config_name, api in api_configs_to_try:
            try:
                # Try to fetch transcript (auto-detect language)
                transcript = api.fetch(video_id)
                
                # Extract text from transcript snippets
                # transcript.snippets is a list of objects with .text attribute
                transcript_text = '\n'.join([snippet.text for snippet in transcript.snippets])
                return transcript_text, None
                
            except TranscriptsDisabled:
                return None, "Transcripts are disabled for this video"
            except NoTranscriptFound:
                return None, "No transcript found for this video"
            except VideoUnavailable:
                return None, "Video is unavailable"
            except Exception as e:
                # Check if it's an SSL/connection error that might be proxy-related
                error_str = str(e).lower()
                is_ssl_error = (
                    'ssl' in error_str or 
                    'sslerror' in error_str or 
                    'connection' in error_str or
                    'eof' in error_str or
                    'retries exceeded' in error_str or
                    'blocking' in error_str or
                    'ip' in error_str
                )
                
                last_error = str(e)
                
                if is_ssl_error and config_name == 'with proxy' and len(api_configs_to_try) > 1:
                    # SSL/connection error with proxy, try without proxy
                    continue
                elif config_name == 'without proxy' or len(api_configs_to_try) == 1:
                    # Last attempt or no proxy available, return error
                    return None, f"Error fetching transcript: {last_error}"
        
        # If we get here, all attempts failed
        return None, f"Error fetching transcript: {last_error or 'Unknown error'}"

    def _check_relevance_with_ollama(
        self, 
        title: str, 
        description: str, 
        tags: List[str], 
        transcript: str,
        model: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if video is relevant to China using Ollama AI.
        
        Args:
            title: Video title
            description: Video description
            tags: List of video tags
            transcript: Video transcript text
            model: Ollama model name
            
        Returns:
            Tuple of (is_relevant: bool, reasoning: str)
        """
        try:
            import requests
        except ImportError:
            raise CommandError("requests library required for Ollama. Install with: pip install requests")
        
        # Prepare the content for analysis
        tags_str = ', '.join(tags) if tags else 'None'
        
        # Truncate transcript if too long (keep first 3000 chars for context)
        transcript_preview = transcript[:3000] if len(transcript) > 3000 else transcript
        if len(transcript) > 3000:
            transcript_preview += "\n[... transcript truncated ...]"
        
        # Create prompt
        prompt = f"""Analyze the following YouTube video and determine if it is relevant to China, Chinese culture, Chinese history, Chinese geography, or Chinese topics in general.

Title: {title}

Description:
{description[:500] if len(description) > 500 else description}

Tags: {tags_str}

Transcript (video content):
{transcript_preview}

Instructions:
- Determine if this video is relevant to China or Chinese topics
- Consider: Chinese culture, history, geography, cities, food, traditions, language, people, travel, etc.
- Be thoughtful: a video might mention China briefly but not be primarily about China
- A video about Chinese food, Chinese cities, Chinese history, or Chinese culture should be considered relevant
- A video that only briefly mentions China in passing might not be relevant

Respond in the following JSON format:
{{
    "relevant": true or false,
    "reasoning": "Brief explanation of why this video is or is not relevant to China (2-3 sentences)"
}}

Response:"""

        # Call Ollama
        ollama_url = getattr(settings, 'OLLAMA_URL', 'http://localhost:11434')
        url = f"{ollama_url}/api/generate"
        
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,  # Lower temperature for more consistent results
                "top_p": 0.9,
            }
        }
        
        try:
            response = requests.post(url, json=payload, timeout=120)
            response.raise_for_status()
            result = response.json()
            response_text = result.get('response', '').strip()
            
            # Parse JSON response
            # Try to extract JSON from response (in case there's extra text)
            # First, try to find JSON object boundaries
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}')
            
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx + 1]
                try:
                    parsed = json.loads(json_str)
                    is_relevant = parsed.get('relevant', False)
                    reasoning = parsed.get('reasoning', 'No reasoning provided')
                    return bool(is_relevant), reasoning
                except json.JSONDecodeError:
                    pass  # Fall through to try parsing whole response
            
            # Fallback: try to parse the whole response as JSON
            try:
                parsed = json.loads(response_text)
                is_relevant = parsed.get('relevant', False)
                reasoning = parsed.get('reasoning', 'No reasoning provided')
                return bool(is_relevant), reasoning
            except json.JSONDecodeError:
                pass  # Will be caught by outer exception handler
                
        except requests.exceptions.ConnectionError:
            raise CommandError(
                f"Could not connect to Ollama at {ollama_url}. "
                "Make sure Ollama is running: https://ollama.ai"
            )
        except json.JSONDecodeError as e:
            # If JSON parsing fails, try to infer from response text
            response_lower = response_text.lower()
            if 'relevant' in response_lower and ('true' in response_lower or 'yes' in response_lower):
                return True, f"Parsed from response (JSON parse failed): {response_text[:200]}"
            elif 'relevant' in response_lower and ('false' in response_lower or 'no' in response_lower):
                return False, f"Parsed from response (JSON parse failed): {response_text[:200]}"
            else:
                raise CommandError(f"Could not parse Ollama response as JSON: {str(e)}\nResponse: {response_text[:500]}")
        except Exception as e:
            raise CommandError(f"Ollama API error: {str(e)}")

