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


def get_channel_videos(channel_id: str, include_shorts: bool = False, api_key: Optional[str] = None, fetch_details: bool = False, max_videos: Optional[int] = None) -> List[Dict]:
    """
    Retrieves videos from a YouTube channel.
    
    Args:
        channel_id: YouTube channel ID
        include_shorts: Whether to include videos under 90 seconds (shorts)
        api_key: YouTube API key (if None, tries to get from settings)
        fetch_details: Whether to fetch description and tags
        max_videos: Maximum number of videos to fetch (None = all)
        
    Returns:
        List of dicts with keys: 'video_id', 'title', 'upload_date', 'duration', 'link'
    """
    if not YOUTUBE_API_AVAILABLE:
        raise ImportError("googleapiclient not available. Install with: pip install google-api-python-client")
    
    if not api_key:
        api_key = get_youtube_api_key()
    
    if not api_key:
        raise ValueError("YouTube API key is required. Set YOUTUBE_API_KEY in settings or environment.")
    
    print(f"  Building YouTube API client...", flush=True)
    youtube = googleapiclient.discovery.build("youtube", "v3", developerKey=api_key)
    video_data = []
    
    try:
        # 1. Get the 'uploads' playlist ID
        print(f"  Calling YouTube API: channels().list()...", flush=True)
        channel_request = youtube.channels().list(
            part="contentDetails",
            id=channel_id
        )
        channel_response = channel_request.execute()
        print(f"  ✓ Channel info retrieved", flush=True)
        
        if not channel_response.get('items'):
            raise ValueError(f"Channel ID {channel_id} not found")
        
        uploads_playlist_id = channel_response['items'][0]['contentDetails']['relatedPlaylists']['uploads']
        print(f"  Uploads playlist ID: {uploads_playlist_id}", flush=True)
    except (IndexError, KeyError) as e:
        raise ValueError(f"Could not retrieve 'uploads' playlist ID. Check the Channel ID: {str(e)}")
    except Exception as e:
        raise Exception(f"Error fetching channel info: {str(e)}")
    
    # 2. Get video IDs from the playlist (with pagination)
    next_page_token = None
    page_count = 0
    
    while True:
        try:
            page_count += 1
            print(f"  Fetching page {page_count} of playlist items...", flush=True)
            playlist_request = youtube.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=uploads_playlist_id,
                maxResults=50,  # Max results per page
                pageToken=next_page_token
            )
            playlist_response = playlist_request.execute()
            print(f"  ✓ Page {page_count} retrieved ({len(playlist_response.get('items', []))} items)", flush=True)
            
            # Extract video IDs for batch lookup
            video_ids = []
            for item in playlist_response.get('items', []):
                video_id = item['contentDetails']['videoId']
                video_ids.append(video_id)
            
            # 3. Batch Request for Upload Dates, Titles, and Duration
            if video_ids:
                print(f"  Fetching details for {len(video_ids)} videos...", flush=True)
                video_details_request = youtube.videos().list(
                    part="snippet,contentDetails",
                    id=",".join(video_ids)
                )
                video_details_response = video_details_request.execute()
                print(f"  ✓ Video details retrieved", flush=True)
                
                for item in video_details_response.get('items', []):
                    video_id = item['id']
                    upload_date_iso = item['snippet']['publishedAt']
                    title = item['snippet'].get('title', 'Unknown Title')
                    
                    # Get description and tags if fetch_details is True
                    description = ''
                    tags = []
                    if fetch_details:
                        description = item['snippet'].get('description', '')
                        tags = item['snippet'].get('tags', [])
                    
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
                    
                    # Stop if we've reached the max_videos limit
                    if max_videos and len(video_data) >= max_videos:
                        print(f"  Reached max_videos limit ({max_videos}), stopping...", flush=True)
                        return video_data
            
            # Check for the next page token
            next_page_token = playlist_response.get('nextPageToken')
            if not next_page_token:
                print(f"  No more pages to fetch", flush=True)
                break
            
            # Stop if we've reached max_videos
            if max_videos and len(video_data) >= max_videos:
                break
                
        except Exception as e:
            raise Exception(f"Error during pagination: {str(e)}")
    
    print(f"  Total videos fetched: {len(video_data)}", flush=True)
    return video_data

