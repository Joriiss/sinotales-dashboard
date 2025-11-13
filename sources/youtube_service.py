"""
Service for fetching videos from YouTube channels using the YouTube API
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from django.conf import settings

try:
    import googleapiclient.discovery
    YOUTUBE_API_AVAILABLE = True
except ImportError:
    YOUTUBE_API_AVAILABLE = False
    googleapiclient = None

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


def parse_duration(iso_duration: str) -> int:
    """
    Parse ISO 8601 duration format (PT1H23M45S) to seconds.
    
    Args:
        iso_duration: ISO 8601 duration string (e.g., "PT1H23M45S", "PT15M30S")
        
    Returns:
        Duration in seconds as integer, or 0 if parsing fails
    """
    if not iso_duration:
        return 0
    
    try:
        # Parse ISO 8601 duration format: PT[#H][#M][#S]
        pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
        match = re.match(pattern, iso_duration)
        
        if not match:
            return 0
        
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        
        total_seconds = hours * 3600 + minutes * 60 + seconds
        return total_seconds
    except:
        return 0


def get_youtube_api_key() -> Optional[str]:
    """
    Get YouTube API key from Django settings or environment.
    
    Returns:
        API key string or None
    """
    api_key = getattr(settings, 'YOUTUBE_API_KEY', None)
    if not api_key:
        import os
        api_key = os.environ.get('YOUTUBE_API_KEY')
    return api_key


def is_video_relevant_to_china(title: str, description: str = '', tags: List[str] = None, video_id: Optional[str] = None) -> bool:
    """
    Check if a video is relevant to China using Ollama AI with transcript analysis.
    Falls back to keyword-based filtering if transcript is unavailable.
    
    Args:
        title: Video title
        description: Video description (optional)
        tags: List of video tags (optional)
        video_id: YouTube video ID (required for transcript fetching)
        
    Returns:
        True if video appears to be about China, False otherwise
    """
    is_relevant, _ = is_video_relevant_to_china_with_details(title, description, tags, video_id)
    return is_relevant


def _load_proxy_config():
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
            except Exception:
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


def _fetch_transcript(video_id: str, proxy_config=None) -> Tuple[Optional[str], Optional[str]]:
    """
    Fetch transcript for a YouTube video.
    Tries with proxy first if available, then falls back to no proxy.
    
    Args:
        video_id: YouTube video ID
        proxy_config: Optional WebshareProxyConfig instance
        
    Returns:
        Tuple of (transcript_text, error_message)
        If successful: (transcript_text, None)
        If failed: (None, error_message)
    """
    if not YOUTUBE_TRANSCRIPT_AVAILABLE:
        return None, "YouTube Transcript API not available"
    
    # Try with proxy first if configured, then fallback to no proxy
    api_configs_to_try = []
    if proxy_config:
        try:
            api_with_proxy = YouTubeTranscriptApi(proxy_config=proxy_config)
            api_configs_to_try.append(('with proxy', api_with_proxy))
            print(f"  [FILTER] Attempting transcript fetch with proxy...", flush=True)
        except Exception as e:
            print(f"  [FILTER] Warning: Failed to create API instance with proxy: {str(e)}", flush=True)
            print(f"  [FILTER] Will try without proxy", flush=True)
    else:
        print(f"  [FILTER] No proxy config available, attempting transcript fetch without proxy...", flush=True)
    api_configs_to_try.append(('without proxy', YouTubeTranscriptApi()))
    
    last_error = None
    for config_name, api in api_configs_to_try:
        try:
            # Try to fetch transcript (auto-detect language)
            transcript = api.fetch(video_id)
            
            # Extract text from transcript snippets
            transcript_text = '\n'.join([snippet.text for snippet in transcript.snippets])
            print(f"  [FILTER] ✓ Successfully fetched transcript {config_name}", flush=True)
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
            error_type = type(e).__name__
            is_ssl_error = (
                'ssl' in error_str or 
                'sslerror' in error_str or 
                'connection' in error_str or
                'eof' in error_str or
                'retries exceeded' in error_str or
                'blocking' in error_str or
                'ip' in error_str or
                'proxy' in error_str or
                'timeout' in error_str or
                'certificate' in error_str
            )
            
            last_error = str(e)
            print(f"  [FILTER] ✗ Failed {config_name}: {error_type}: {str(e)[:200]}...", flush=True)
            
            if is_ssl_error and config_name == 'with proxy' and len(api_configs_to_try) > 1:
                # SSL/connection error with proxy, try without proxy
                print(f"  [FILTER] Retrying without proxy...", flush=True)
                continue
            elif config_name == 'without proxy' or len(api_configs_to_try) == 1:
                # Last attempt or no proxy available, return error
                # Truncate very long error messages
                error_msg = last_error[:500] if len(last_error) > 500 else last_error
                return None, f"Error fetching transcript: {error_msg}"
        
    # If we get here, all attempts failed
    error_msg = last_error[:500] if last_error and len(last_error) > 500 else (last_error or 'Unknown error')
    return None, f"Error fetching transcript: {error_msg}"


def _check_relevance_with_ollama(
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
        raise ImportError("requests library required for Ollama. Install with: pip install requests")
    
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
        raise ConnectionError(
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
            raise ValueError(f"Could not parse Ollama response as JSON: {str(e)}\nResponse: {response_text[:500]}")
    except Exception as e:
        raise Exception(f"Ollama API error: {str(e)}")


def is_video_relevant_to_china_with_details(title: str, description: str = '', tags: List[str] = None, video_id: Optional[str] = None) -> tuple:
    """
    Check if a video is relevant to China using Ollama AI with transcript analysis.
    Falls back to keyword-based filtering if transcript is unavailable.
    
    Args:
        title: Video title
        description: Video description (optional)
        tags: List of video tags (optional)
        video_id: YouTube video ID (required for transcript fetching)
        
    Returns:
        Tuple of (is_relevant: bool, matched_keywords: list[str] or reasoning: str)
        If Ollama is used: (is_relevant: bool, reasoning: str)
        If keyword fallback: (is_relevant: bool, matched_keywords: list[str])
    """
    if tags is None:
        tags = []
    
    # Try Ollama-based filtering with transcript if video_id is provided
    if video_id and YOUTUBE_TRANSCRIPT_AVAILABLE:
        try:
            # Get Ollama model from settings
            try:
                from .models import Settings
                app_settings = Settings.get_settings()
                ollama_model = app_settings.default_tagging_model
            except Exception:
                ollama_model = 'gpt-oss:20b-cloud'
            
            # Load proxy config
            proxy_config = _load_proxy_config()
            if proxy_config:
                print(f"  [FILTER] Proxy configuration loaded for transcript fetching", flush=True)
            else:
                print(f"  [FILTER] Warning: No proxy configuration found - transcript fetching may fail on VPS/cloud IPs", flush=True)
            
            # Fetch transcript
            transcript_text, error_msg = _fetch_transcript(video_id, proxy_config)
            
            if transcript_text:
                # Use Ollama to analyze
                try:
                    is_relevant, reasoning = _check_relevance_with_ollama(
                        title, description, tags, transcript_text, ollama_model
                    )
                    # Return with reasoning as the "matched_keywords" field for compatibility
                    return is_relevant, [reasoning] if reasoning else []
                except Exception as e:
                    # If Ollama fails, fall back to keyword-based
                    print(f"  [FILTER] Ollama analysis failed: {str(e)}, falling back to keyword-based filtering", flush=True)
            else:
                # Transcript unavailable - treat as not relevant
                print(f"  [FILTER] Transcript unavailable: {error_msg}, marking as not relevant", flush=True)
                return False, [f"Transcript unavailable: {error_msg}"]
        except Exception as e:
            # If anything fails, fall back to keyword-based
            print(f"  [FILTER] Error in Ollama filtering: {str(e)}, falling back to keyword-based filtering", flush=True)
    
    # Fallback to keyword-based filtering
    # China-related keywords (case-insensitive)
    single_word_keywords = [
        'china', 'chinese', 'chinois', 'chine',
        'beijing', 'peking', 'pékin',
        'shanghai', 'shanghaï',
        'guangzhou', 'canton',
        'shenzhen',
        'yunnan', 'kunming',
        'sichuan', 'chengdu',
        'guizhou', 'guiyang',
        'shandong', 'jinan',
        'jiangsu', 'nanjing',
        'zhejiang', 'hangzhou',
        'anhui', 'hefei',
        'fujian', 'xiamen',
        'jiangxi', 'nanchang',
        'henan', 'zhengzhou',
        'hubei', 'wuhan',
        'hunan', 'changsha',
        'guangxi', 'nanning',
        'hainan', 'haikou',
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
        'han',  # Word boundary prevents matching "thanks", "hand", etc.
        'ming',  # Word boundary prevents matching "charming", "coming", etc.
        'qing',  # Word boundary prevents matching "requesting", etc.
        'tang',  # Word boundary prevents matching "tangent", etc.
        'song',  # Word boundary - careful: "song" can be English word too
        'yuan',  # Word boundary - careful: "yuan" can be currency
        'mao',
        'ccp',
        'panda',
        'dragon', 'phoenix',
        'kungfu',
        'dumpling', 'wonton',
        'zhongguo', '中国', '中文',
    ]
    
    # Multi-word phrases (can match anywhere in text)
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
    
    # Combine all text to search
    search_text = f"{title} {description} {' '.join(tags)}".lower()
    
    matched_keywords = []
    
    # Check single-word keywords with word boundaries
    for keyword in single_word_keywords:
        # Use word boundaries (\b) to match whole words only
        pattern = r'\b' + re.escape(keyword.lower()) + r'\b'
        if re.search(pattern, search_text, re.IGNORECASE):
            matched_keywords.append(keyword)
    
    # Check multi-word phrases (can appear anywhere)
    for keyword in multi_word_keywords:
        if keyword.lower() in search_text:
            matched_keywords.append(keyword)
    
    is_relevant = len(matched_keywords) > 0
    return is_relevant, matched_keywords


def get_channel_videos(channel_id: str, include_shorts: bool = False, filter_china: bool = False, api_key: Optional[str] = None) -> List[Dict]:
    """
    Retrieves all videos from a YouTube channel.
    
    Args:
        channel_id: YouTube channel ID
        include_shorts: Whether to include videos under 90 seconds (shorts)
        filter_china: Whether to filter videos to only include China-related content
        api_key: YouTube API key (if None, tries to get from settings)
        
    Returns:
        List of dicts with keys: 'video_id', 'title', 'upload_date', 'duration', 'link', 'description', 'tags'
    """
    if not YOUTUBE_API_AVAILABLE:
        raise ImportError("googleapiclient not available. Install with: pip install google-api-python-client")
    
    if not api_key:
        api_key = get_youtube_api_key()
    
    if not api_key:
        raise ValueError("YouTube API key is required. Set YOUTUBE_API_KEY in settings or environment.")
    
    youtube = googleapiclient.discovery.build("youtube", "v3", developerKey=api_key)
    video_data = []
    
    try:
        # 1. Get the 'uploads' playlist ID
        channel_request = youtube.channels().list(
            part="contentDetails",
            id=channel_id
        )
        channel_response = channel_request.execute()
        
        if not channel_response.get('items'):
            raise ValueError(f"Channel ID {channel_id} not found")
        
        uploads_playlist_id = channel_response['items'][0]['contentDetails']['relatedPlaylists']['uploads']
    except (IndexError, KeyError) as e:
        raise ValueError(f"Could not retrieve 'uploads' playlist ID. Check the Channel ID: {str(e)}")
    except Exception as e:
        raise Exception(f"Error fetching channel info: {str(e)}")
    
    # 2. Get video IDs from the playlist (with pagination)
    next_page_token = None
    
    while True:
        try:
            playlist_request = youtube.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=uploads_playlist_id,
                maxResults=50,  # Max results per page
                pageToken=next_page_token
            )
            playlist_response = playlist_request.execute()
            
            # Extract video IDs for batch lookup
            video_ids = []
            for item in playlist_response.get('items', []):
                video_id = item['contentDetails']['videoId']
                video_ids.append(video_id)
            
            # 3. Batch Request for Upload Dates, Titles, Duration, Description, and Tags
            if video_ids:
                # Request snippet (title, description, tags) and contentDetails (duration)
                parts = "snippet,contentDetails"
                video_details_request = youtube.videos().list(
                    part=parts,
                    id=",".join(video_ids)
                )
                video_details_response = video_details_request.execute()
                
                for item in video_details_response.get('items', []):
                    video_id = item['id']
                    snippet = item.get('snippet', {})
                    upload_date_iso = snippet.get('publishedAt', '')
                    title = snippet.get('title', 'Unknown Title')
                    description = snippet.get('description', '')
                    tags = snippet.get('tags', []) or []
                    
                    # Convert ISO 8601 to YYYY-MM-DD
                    try:
                        dt = datetime.fromisoformat(upload_date_iso.replace('Z', '+00:00'))
                        upload_date = dt.date()
                    except:
                        upload_date = None
                    
                    # Get duration
                    duration_iso = item.get('contentDetails', {}).get('duration', '')
                    duration_seconds = parse_duration(duration_iso)
                    
                    # Filter shorts if needed (videos under 90 seconds are considered shorts)
                    if not include_shorts and duration_seconds < 90:
                        continue
                    
                    # Filter China-related videos if requested
                    if filter_china:
                        is_relevant = is_video_relevant_to_china(title, description, tags, video_id)
                        if not is_relevant:
                            # Debug: print skipped videos when filtering is enabled
                            print(f"  [FILTER] Skipping video (not China-related): {title[:60]}...", flush=True)
                            continue
                    
                    video_link = f"https://www.youtube.com/watch?v={video_id}"
                    
                    video_data.append({
                        'video_id': video_id,
                        'title': title,
                        'upload_date': upload_date,
                        'duration': duration_seconds,
                        'link': video_link,
                        'description': description,
                        'tags': tags
                    })
            
            # Check for the next page token
            next_page_token = playlist_response.get('nextPageToken')
            if not next_page_token:
                break
                
        except Exception as e:
            raise Exception(f"Error during pagination: {str(e)}")
    
    return video_data

