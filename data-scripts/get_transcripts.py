#!/usr/bin/env python3
"""
Script to extract transcripts from YouTube videos listed in videos.csv
Saves transcripts in the transcripts folder
"""

import os
import csv
import argparse
import time
from pathlib import Path
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, VideoUnavailable

# Try to import proxy config (may not be available in older versions)
try:
    from youtube_transcript_api.proxies import WebshareProxyConfig
    PROXY_SUPPORT = True
except ImportError:
    PROXY_SUPPORT = False
    WebshareProxyConfig = None


def read_videos_from_csv(file_path):
    """
    Read video information from videos.csv.
    
    Args:
        file_path: Path to the videos.csv file
        
    Returns:
        List of dicts with keys: 'channel_name', 'video_title', 'video_id', 'channel_language'
    """
    videos = []
    
    if not os.path.exists(file_path):
        print(f"Warning: {file_path} does not exist")
        return videos
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Normalize row keys by stripping whitespace
                normalized_row = {k.strip(): v for k, v in row.items()}
                
                video_id = normalized_row.get('video_id', '').strip()
                channel_name = normalized_row.get('channel_name', '').strip()
                video_title = normalized_row.get('video_title', '').strip()
                channel_language = normalized_row.get('channel_language', '').strip()
                
                if video_id:
                    videos.append({
                        'video_id': video_id,
                        'channel_name': channel_name,
                        'video_title': video_title,
                        'channel_language': channel_language
                    })
    except Exception as e:
        print(f"Error reading {file_path}: {str(e)}")
        return []
    
    return videos


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


def get_transcript_for_video(video_id, transcripts_dir, api, channel_language=None, max_retries=3, delay=2):
    """
    Fetch and save transcript for a single video.
    Tries the channel language first, then falls back to other languages.
    Includes retry logic for rate limiting and IP blocking.
    
    Args:
        video_id: YouTube video ID
        transcripts_dir: Directory to save the transcript file
        api: YouTubeTranscriptApi instance (with proxy config if needed)
        channel_language: Preferred language code from CSV (e.g., 'fr', 'en', 'zh')
        max_retries: Maximum number of retry attempts (default: 3)
        delay: Delay in seconds between retries (default: 2)
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    
    # Build language list with channel language first, then common fallbacks
    languages_to_try = []
    
    # Add channel language first if provided
    if channel_language:
        # Normalize language code (e.g., "English" -> "en")
        lang_map = {
            'english': 'en',
            'anglais': 'en',
            'french': 'fr',
            'français': 'fr',
            'chinese': 'zh',
            'chinois': 'zh',
        }
        lang_code = lang_map.get(channel_language.lower(), channel_language.lower())
        if lang_code not in languages_to_try:
            languages_to_try.append(lang_code)
    
    # Add common fallback languages
    fallback_languages = ['en', 'fr', 'zh', 'zh-CN', 'zh-TW', 'es', 'de', 'ja', 'ko', 'ru', 'it', 'pt', 'nl', 'pl', 'sv', 'da', 'fi', 'no', 'cs', 'hu', 'ro', 'sk', 'sl', 'bg', 'hr', 'sr', 'uk', 'el', 'tr', 'ar', 'he', 'th', 'vi', 'id', 'ms', 'hi', 'bn', 'ta', 'te', 'ml', 'kn', 'gu', 'pa', 'mr', 'ne', 'ur']
    
    # Add fallbacks, avoiding duplicates
    for lang in fallback_languages:
        if lang not in languages_to_try:
            languages_to_try.append(lang)
    
    # Retry logic for rate limiting and IP blocking
    for attempt in range(max_retries):
        try:
            # First, try to list available transcripts to check availability
            try:
                transcript_list = api.list(video_id)
                if not transcript_list:
                    return False, "No transcripts available for this video"
            except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable) as e:
                if isinstance(e, TranscriptsDisabled):
                    return False, "Transcripts are disabled for this video"
                elif isinstance(e, VideoUnavailable):
                    return False, "Video is unavailable"
                # If list fails, we'll still try to fetch directly
            
            transcript = None
            language_used = None
            
            try:
                # Try to fetch with language priority (channel language first)
                transcript = api.fetch(video_id, languages=languages_to_try)
                language_used = transcript.language_code if hasattr(transcript, 'language_code') else 'unknown'
            except (NoTranscriptFound, TranscriptsDisabled):
                # If that fails, try without specifying languages (auto-detect)
                try:
                    transcript = api.fetch(video_id)
                    language_used = transcript.language_code if hasattr(transcript, 'language_code') else 'auto'
                except (NoTranscriptFound, TranscriptsDisabled) as e:
                    if isinstance(e, TranscriptsDisabled):
                        return False, "Transcripts are disabled for this video"
                    return False, "No transcript found for this video"
            
            if transcript is None:
                return False, "No transcript found for this video (tried multiple languages)"
            
            # Extract text from transcript snippets
            transcript_text = '\n'.join([snippet.text for snippet in transcript.snippets])
            
            # Save the transcript to a text file
            output_file = transcripts_dir / f"{video_id}.txt"
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(transcript_text)
            
            lang_info = f" ({language_used})" if language_used else ""
            return True, f"Saved transcript to {output_file.name}{lang_info}"
            
        except TranscriptsDisabled:
            return False, "Transcripts are disabled for this video"
        except NoTranscriptFound:
            return False, "No transcript found for this video"
        except VideoUnavailable:
            return False, "Video is unavailable"
        except Exception as e:
            # Check for rate limiting/IP blocking errors by examining the error message
            error_str = str(e).lower()
            error_type = type(e).__name__
            
            # Check if it's a rate limiting or blocking error
            is_rate_limit = (
                'too many' in error_str or 
                'rate limit' in error_str or
                '429' in error_str or
                'blocked' in error_str or
                'ip' in error_str and 'block' in error_str or
                'request' in error_str and 'block' in error_str
            )
            
            if is_rate_limit and attempt < max_retries - 1:
                wait_time = delay * (attempt + 1)  # Exponential backoff
                print(f"     ⚠️  Rate limiting or IP blocking detected. Retrying in {wait_time} seconds... (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
                continue
            elif is_rate_limit:
                return False, f"Rate limited or IP blocked after {max_retries} attempts. Consider using proxies or waiting longer."
            else:
                # For other errors, don't retry (likely permanent issue)
                return False, f"Error: {str(e)}"
    
    return False, f"Failed after {max_retries} attempts"


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Extract transcripts from YouTube videos listed in a CSV file',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python get_transcripts.py                    # Use videos.csv (default)
  python get_transcripts.py -f videos_sample.csv  # Use a different CSV file
  python get_transcripts.py --file custom.csv     # Use a custom CSV file
        """
    )
    parser.add_argument(
        '--file', '-f',
        type=str,
        default=None,
        metavar='FILE',
        help='Path to the CSV file containing video information (default: videos.csv)'
    )
    
    args = parser.parse_args()
    
    # Get the directory where this script is located
    script_dir = Path(__file__).parent
    
    # Try to load proxy credentials from .env file (in parent directory)
    proxy_config = None
    if PROXY_SUPPORT:
        env_file = script_dir.parent / '.env'
        if env_file.exists():
            env_vars = load_env_file(env_file)
            proxy_username = env_vars.get('WEBSHARE_PROXY_USERNAME', '').strip()
            proxy_password = env_vars.get('WEBSHARE_PROXY_PASSWORD', '').strip()
            
            if proxy_username and proxy_password:
                proxy_config = WebshareProxyConfig(
                    proxy_username=proxy_username,
                    proxy_password=proxy_password
                )
                print("📝 Using Webshare rotating residential proxies (loaded from .env)")
            else:
                print("ℹ️  No Webshare proxy credentials found in .env (optional)")
        else:
            print("ℹ️  No .env file found - running without proxies (optional)")
    else:
        print("ℹ️  Proxy support not available in this version of youtube-transcript-api")
    
    # Create API instance with proxy config if available
    api = YouTubeTranscriptApi(proxy_config=proxy_config) if proxy_config else YouTubeTranscriptApi()
    
    # Determine the videos file path
    if args.file:
        # If a path is provided, use it (handle both absolute and relative paths)
        videos_file = Path(args.file)
        if not videos_file.is_absolute():
            videos_file = script_dir / videos_file
    else:
        # Default to videos.csv in the script directory
        videos_file = script_dir / 'videos.csv'
    
    transcripts_dir = script_dir / 'transcripts'
    
    # Create transcripts directory if it doesn't exist
    transcripts_dir.mkdir(exist_ok=True)
    
    # Read videos from CSV
    print(f"Reading videos from {videos_file.name}...")
    videos = read_videos_from_csv(videos_file)
    
    if not videos:
        print(f"No videos found in {videos_file}")
        return
    
    print(f"Found {len(videos)} video(s) to process\n")
    
    # Check for existing transcripts
    existing_transcripts = set()
    if transcripts_dir.exists():
        for transcript_file in transcripts_dir.glob("*.txt"):
            # Extract video ID from filename (filename is {video_id}.txt)
            video_id_from_file = transcript_file.stem
            existing_transcripts.add(video_id_from_file)
    
    if existing_transcripts:
        print(f"📋 Found {len(existing_transcripts)} existing transcripts")
        print(f"   Skipping these to avoid re-downloading\n")
    else:
        print(f"📋 No existing transcripts found - starting fresh\n")
    
    # Process each video
    results = {
        'success': 0,
        'skipped': 0,
        'failed': 0,
        'total': len(videos)
    }
    
    for i, video_info in enumerate(videos, 1):
        video_id = video_info['video_id']
        channel_name = video_info['channel_name']
        video_title = video_info['video_title']
        channel_language = video_info.get('channel_language', '')
        
        print(f"[{i}/{len(videos)}] Processing: {video_id}")
        if channel_name:
            print(f"  Channel: {channel_name}")
        if video_title:
            print(f"  Title: {video_title[:60]}{'...' if len(video_title) > 60 else ''}")
        if channel_language:
            print(f"  Language: {channel_language}")
        
        # Check if transcript already exists
        transcript_file = transcripts_dir / f"{video_id}.txt"
        if transcript_file.exists():
            print(f"  ⏭️  Transcript already exists - skipping download")
            print(f"     File: {transcript_file.name}\n")
            results['skipped'] += 1
            continue
        
        success, message = get_transcript_for_video(video_id, transcripts_dir, api, channel_language if channel_language else None)
        
        if success:
            print(f"  ✅ {message}\n")
            results['success'] += 1
        else:
            print(f"  ❌ {message}\n")
            results['failed'] += 1
        
        # Add a small delay between requests to avoid rate limiting
        # Only delay if this wasn't the last video
        if i < len(videos):
            time.sleep(5)  # 5 second delay between requests
    
    # Print summary
    print("=" * 50)
    print(f"📊 Summary:")
    print(f"  Total videos: {results['total']}")
    print(f"  Successfully downloaded: {results['success']}")
    if results['skipped'] > 0:
        print(f"  Skipped (already exist): {results['skipped']}")
    print(f"  Failed: {results['failed']}")
    if existing_transcripts:
        total_transcripts = len(existing_transcripts) + results['success']
        print(f"  Total transcripts in folder: {total_transcripts}")
    print(f"  Transcripts saved in: {transcripts_dir}")
    print("=" * 50)


if __name__ == '__main__':
    main()