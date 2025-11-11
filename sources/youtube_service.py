"""
Service for fetching videos from YouTube channels using the YouTube API
"""
import re
from datetime import datetime
from typing import List, Dict, Optional
from django.conf import settings

try:
    import googleapiclient.discovery
    YOUTUBE_API_AVAILABLE = True
except ImportError:
    YOUTUBE_API_AVAILABLE = False
    googleapiclient = None


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


def is_video_relevant_to_china(title: str, description: str = '', tags: List[str] = None) -> bool:
    """
    Check if a video is relevant to China based on title, description, and tags.
    
    Args:
        title: Video title
        description: Video description (optional)
        tags: List of video tags (optional)
        
    Returns:
        True if video appears to be about China, False otherwise
    """
    if tags is None:
        tags = []
    
    # China-related keywords (case-insensitive)
    # Single-word keywords that need word boundaries to avoid false positives
    # (e.g., "ming" in "charming", "han" in "thanks")
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
    
    # Check single-word keywords with word boundaries
    for keyword in single_word_keywords:
        # Use word boundaries (\b) to match whole words only
        pattern = r'\b' + re.escape(keyword.lower()) + r'\b'
        if re.search(pattern, search_text, re.IGNORECASE):
            return True
    
    # Check multi-word phrases (can appear anywhere)
    for keyword in multi_word_keywords:
        if keyword.lower() in search_text:
            return True
    
    return False


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
                        is_relevant = is_video_relevant_to_china(title, description, tags)
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

