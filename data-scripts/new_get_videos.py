#!/usr/bin/env python3
"""
Script to extract video information from YouTube channels using the YouTube API
Reads channel IDs from channels.csv and outputs to videos.csv
"""

import googleapiclient.discovery
import os
import csv
import argparse
from pathlib import Path
from datetime import datetime
import re


def parse_duration(iso_duration):
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
        # Examples: PT1H23M45S, PT15M30S, PT45S
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


def format_duration(seconds):
    """
    Format duration in seconds to HH:MM:SS or MM:SS format.
    
    Args:
        seconds: Duration in seconds
        
    Returns:
        Formatted duration string (e.g., "1:23:45", "15:30")
    """
    if not seconds:
        return ""
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


def load_env_file(env_path):
    """
    Load environment variables from a .env file.
    
    Args:
        env_path: Path to the .env file
        
    Returns:
        Dictionary of key-value pairs from the .env file
    """
    env_vars = {}
    
    if not os.path.exists(env_path):
        return env_vars
    
    try:
        with open(env_path, 'r', encoding='utf-8') as f:
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
                    
                    env_vars[key] = value
    except Exception as e:
        print(f"Warning: Could not read .env file: {str(e)}")
    
    return env_vars


def get_all_channel_videos(api_key, channel_id):
    """
    Retrieves the title, video ID, upload date, and duration for all videos in a YouTube channel.
    
    Args:
        api_key: YouTube API key
        channel_id: YouTube channel ID
        
    Returns:
        List of dicts with keys: 'id', 'title', 'upload_date', 'duration' (in seconds), 'duration_formatted'
    """
    youtube = googleapiclient.discovery.build("youtube", "v3", developerKey=api_key)
    video_data = []

    # 1. Get the 'uploads' playlist ID
    try:
        channel_request = youtube.channels().list(
            part="contentDetails",
            id=channel_id
        )
        channel_response = channel_request.execute()

        uploads_playlist_id = channel_response['items'][0]['contentDetails']['relatedPlaylists']['uploads']
    except (IndexError, KeyError) as e:
        print(f"  ⚠️  Error: Could not retrieve 'uploads' playlist ID. Check the Channel ID.")
        return []
    except Exception as e:
        print(f"  ⚠️  Error fetching channel info: {str(e)}")
        return []

    # 2. Get video IDs from the playlist (with pagination)
    next_page_token = None
    
    while True:
        try:
            playlist_request = youtube.playlistItems().list(
                part="snippet,contentDetails", 
                playlistId=uploads_playlist_id,
                maxResults=50, # Max results per page
                pageToken=next_page_token
            )
            playlist_response = playlist_request.execute()
            
            # Extract video IDs for batch lookup
            video_ids = []
            
            for item in playlist_response.get('items', []):
                video_id = item['contentDetails']['videoId']
                video_ids.append(video_id)
                
            # 3. Batch Request for Upload Dates, Titles, and Duration
            if video_ids:
                # The 'videos.list' endpoint is required to get the 'publishedAt' date, title, and duration
                video_details_request = youtube.videos().list(
                    part="snippet,contentDetails",
                    id=",".join(video_ids) # Join IDs into a comma-separated string
                )
                video_details_response = video_details_request.execute()
                
                for item in video_details_response.get('items', []):
                    video_id = item['id']
                    upload_date_iso = item['snippet']['publishedAt'] # ISO 8601 format
                    title = item['snippet'].get('title', 'Unknown Title')
                    
                    # Convert ISO 8601 (YYYY-MM-DDTHH:MM:SSZ) to YYYY-MM-DD
                    try:
                        dt = datetime.fromisoformat(upload_date_iso.replace('Z', '+00:00'))
                        upload_date = dt.strftime('%Y-%m-%d')
                    except:
                        # Fallback: try to extract date from string
                        upload_date = upload_date_iso[:10] if len(upload_date_iso) >= 10 else ''
                    
                    # Get duration (in ISO 8601 format like PT1H23M45S)
                    duration_iso = item.get('contentDetails', {}).get('duration', '')
                    duration_seconds = parse_duration(duration_iso)
                    duration_formatted = format_duration(duration_seconds)
                    
                    video_data.append({
                        'id': video_id,
                        'title': title,
                        'upload_date': upload_date,
                        'duration': duration_seconds,
                        'duration_formatted': duration_formatted
                    })
                
                print(f"  📥 Fetched {len(video_data)} videos so far...")

            # Check for the next page token for pagination
            next_page_token = playlist_response.get('nextPageToken')
            
            if not next_page_token:
                break
                
        except Exception as e:
            print(f"  ⚠️  Error during pagination: {str(e)}")
            break

    if video_data:
        print(f"  ✅ Successfully fetched {len(video_data)} videos")
    
    return video_data


def read_existing_videos_from_csv(file_path):
    """
    Read existing video IDs from videos.csv to avoid re-scraping.
    
    Args:
        file_path: Path to the videos.csv file
        
    Returns:
        Set of existing video IDs
    """
    existing_video_ids = set()
    
    if not os.path.exists(file_path):
        return existing_video_ids
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Normalize row keys by stripping whitespace
                normalized_row = {k.strip(): v for k, v in row.items()}
                video_id = normalized_row.get('video_id', '').strip()
                if video_id:
                    existing_video_ids.add(video_id)
    except Exception as e:
        print(f"Warning: Could not read existing videos.csv: {str(e)}")
    
    return existing_video_ids


def read_channels_from_csv(file_path):
    """
    Read channel information from a CSV file.
    
    Args:
        file_path: Path to the CSV file containing channel info
        
    Returns:
        List of dicts with keys: 'name', 'channel_id', 'language', 'include_shorts'
    """
    channels = []
    
    if not os.path.exists(file_path):
        print(f"Warning: {file_path} does not exist")
        return channels
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Normalize row keys by stripping whitespace
                normalized_row = {k.strip(): v for k, v in row.items()}
                
                name = normalized_row.get('name', '').strip()
                channel_id = normalized_row.get('channel_id', '').strip()
                channel_language = normalized_row.get('language', '').strip()
                include_shorts_str = normalized_row.get('include_shorts', 'False').strip()
                
                # Parse include_shorts (handle True/False strings, case-insensitive)
                include_shorts = include_shorts_str.lower() in ('true', '1', 'yes', 'y')
                
                if name and channel_id:
                    channels.append({
                        'name': name,
                        'channel_id': channel_id,
                        'language': channel_language,
                        'include_shorts': include_shorts
                    })
    except Exception as e:
        print(f"Error reading {file_path}: {str(e)}")
        return []
    
    return channels


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Extract video information from YouTube channels using the YouTube API',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python new_get_videos.py                    # Extract all videos (reads API key from .env)
  python new_get_videos.py --test             # Extract only 1 video per channel (for testing)
  python new_get_videos.py -n 5               # Extract 5 videos per channel
  python new_get_videos.py --api-key KEY      # Override API key from command line
  
Note: API key is loaded from .env file (YOUTUBE_API_KEY=your_key) or can be provided via --api-key
      Get an API key from: https://console.cloud.google.com/apis/credentials
        """
    )
    parser.add_argument(
        '--api-key', '-k',
        type=str,
        default=None,
        metavar='KEY',
        help='YouTube API key (will try to load from .env if not provided)'
    )
    parser.add_argument(
        '--test', '-t',
        action='store_true',
        help='Test mode: extract only 1 video per channel (default: extract all videos)'
    )
    parser.add_argument(
        '--number', '-n',
        type=int,
        default=None,
        metavar='N',
        help='Extract N videos per channel (overrides --test if specified)'
    )
    args = parser.parse_args()
    
    # Get the directory where this script is located
    script_dir = Path(__file__).parent
    
    # Try to load API key from .env file if not provided via command line
    api_key = args.api_key
    if not api_key:
        env_file = script_dir.parent / '.env'
        if env_file.exists():
            env_vars = load_env_file(env_file)
            api_key = env_vars.get('YOUTUBE_API_KEY', '').strip()
            if api_key:
                print(f"📝 Loaded API key from .env file ({env_file})")
            else:
                print(f"⚠️  YOUTUBE_API_KEY not found in .env file ({env_file})")
        else:
            print(f"⚠️  .env file not found at {env_file}")
    
    # Validate API key
    if not api_key:
        print("\n❌ Error: YouTube API key is required!")
        print("   Please either:")
        print(f"   1. Add YOUTUBE_API_KEY=your_key to a .env file in the parent directory ({script_dir.parent}), or")
        print("   2. Provide it via --api-key command line argument")
        print("\n   Get an API key from: https://console.cloud.google.com/apis/credentials")
        return
    
    # Determine max videos per channel
    max_videos = args.number if args.number else (1 if args.test else None)
    
    if max_videos:
        print(f"TEST MODE: Extracting up to {max_videos} video(s) per channel")
    else:
        print("Extracting all videos from all channels")
    
    print()
    
    channels_file = script_dir / 'channels.csv'
    
    # Read channel information from CSV file
    print("Reading channels from channels.csv...")
    channels = read_channels_from_csv(channels_file)
    
    if not channels:
        print("No channels found in channels.csv")
        print("Please add channels to channels.csv with columns: name, link, include_shorts, language, channel_id")
        return
    
    print(f"Found {len(channels)} channel(s)\n")
    
    # Check for existing videos.csv
    output_file = script_dir / 'videos.csv'
    existing_video_ids = read_existing_videos_from_csv(output_file)
    
    if existing_video_ids:
        print(f"📋 Found {len(existing_video_ids)} existing videos in videos.csv")
        print(f"   Skipping these to avoid re-scraping\n")
    else:
        print(f"📋 No existing videos.csv found - starting fresh\n")
    
    # Collect all video information
    all_videos = []
    skipped_count = 0
    
    for i, channel_info in enumerate(channels, 1):
        channel_name = channel_info['name']
        channel_id = channel_info['channel_id']
        channel_language = channel_info.get('language', '')
        include_shorts = channel_info.get('include_shorts', False)
        
        print(f"\n{'='*60}")
        print(f"📺 Processing channel {i}/{len(channels)}: {channel_name}")
        print(f"{'='*60}")
        print(f"  Channel ID: {channel_id}")
        if channel_language:
            print(f"  Language: {channel_language}")
        print(f"  Include Shorts: {include_shorts}")
        if max_videos:
            print(f"  Limit: {max_videos} videos")
        else:
            print(f"  Limit: All videos")
        print()
        
        videos = get_all_channel_videos(api_key, channel_id)
        
        if videos:
            # Filter shorts based on include_shorts setting
            # Videos under 60 seconds are considered shorts
            videos_before_filter = len(videos)
            if not include_shorts:
                videos = [v for v in videos if v.get('duration', 0) >= 90]
                shorts_filtered = videos_before_filter - len(videos)
                if shorts_filtered > 0:
                    print(f"  🎬 Filtered out {shorts_filtered} short(s) (under 60 seconds)")
            
            # Apply max_videos limit if specified
            if max_videos:
                videos = videos[:max_videos]
            
            print(f"\n  ✅ Extracted {len(videos)} videos from '{channel_name}'")
            
            # Filter out videos that already exist
            new_videos = []
            for video in videos:
                if video['id'] not in existing_video_ids:
                    new_videos.append(video)
                    # Add to existing set to avoid duplicates within this run
                    existing_video_ids.add(video['id'])
                else:
                    skipped_count += 1
            
            if skipped_count > 0 and new_videos:
                print(f"  ⏭️  Skipped {len(videos) - len(new_videos)} videos (already in CSV)")
                print(f"  ➕ Adding {len(new_videos)} new videos")
            elif skipped_count > 0:
                print(f"  ⏭️  All {len(videos)} videos already exist in CSV - skipping")
            else:
                print(f"  ➕ All {len(videos)} videos are new")
            
            # Add new videos to list
            for video in new_videos:
                all_videos.append({
                    'channel_name': channel_name,
                    'video_title': video['title'],
                    'video_id': video['id'],
                    'upload_date': video.get('upload_date', ''),
                    'channel_language': channel_language
                })
            
            # Show a few example new videos
            if new_videos:
                print(f"  📝 Sample new videos:")
                for j, video in enumerate(new_videos[:3], 1):
                    title_preview = video['title'][:60] + '...' if len(video['title']) > 60 else video['title']
                    print(f"     {j}. {title_preview}")
                if len(new_videos) > 3:
                    print(f"     ... and {len(new_videos) - 3} more")
            print()
        else:
            print(f"  ⚠️  No videos found or error occurred\n")
    
    # Write to CSV file
    print("=" * 50)
    print(f"📊 Summary:")
    print(f"  New videos found: {len(all_videos)}")
    if skipped_count > 0:
        print(f"  Videos skipped (already exist): {skipped_count}")
    print("=" * 50)
    
    if all_videos:
        # Append mode if file exists, write mode if new
        file_exists = output_file.exists()
        mode = 'a' if file_exists else 'w'
        
        with open(output_file, mode, newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['channel_name', 'video_title', 'video_id', 'upload_date', 'channel_language'])
            
            # Write header only if file is new
            if not file_exists:
                writer.writeheader()
            
            writer.writerows(all_videos)
        
        action = "Appended to" if file_exists else "Created"
        print(f"\n✅ {action} CSV file: {output_file}")
        print(f"   Added {len(all_videos)} new video(s)")
        
        if file_exists:
            total_count = len(existing_video_ids) + len(all_videos)
            print(f"   Total videos in CSV: {total_count}")
        
        print("\n📝 First few new entries:")
        for i, video in enumerate(all_videos[:5], 1):
            print(f"  {i}. [{video['channel_name']}] {video['video_title']} - {video['video_id']}")
        if len(all_videos) > 5:
            print(f"  ... and {len(all_videos) - 5} more")
    else:
        print("\n⚠️  No new videos found to save.")
        if skipped_count > 0:
            print(f"   (All videos were already in the CSV)")


if __name__ == '__main__':
    main()
