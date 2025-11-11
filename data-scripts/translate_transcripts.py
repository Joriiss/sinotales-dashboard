#!/usr/bin/env python3
"""
Script to translate transcripts based on channel_language from videos.csv
Translates non-English transcripts to English by default
"""

import os
import csv
import argparse
import time
from pathlib import Path
from deep_translator import GoogleTranslator

def read_videos_from_csv(file_path):
    """
    Read video information from videos.csv.
    
    Args:
        file_path: Path to the videos.csv file
        
    Returns:
        List of dicts with video information
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
                channel_language = normalized_row.get('channel_language', '').strip().lower()
                
                if video_id:
                    videos.append({
                        'video_id': video_id,
                        'channel_name': channel_name,
                        'channel_language': channel_language
                    })
    except Exception as e:
        print(f"Error reading {file_path}: {str(e)}")
        return []
    
    return videos


def format_transcript_for_translation(text):
    """
    Format transcript text by removing unnecessary line breaks.
    Keeps line breaks only after sentence endings (. ! ?) to improve translation quality.
    
    Args:
        text: Raw transcript text with many line breaks
        
    Returns:
        Formatted text with better sentence flow
    """
    if not text:
        return text
    
    lines = text.split('\n')
    formatted_lines = []
    current_sentence = ''
    
    for line in lines:
        line = line.strip()
        if not line:
            # Empty line - preserve as paragraph break if we have content
            if current_sentence:
                formatted_lines.append(current_sentence.strip())
                current_sentence = ''
            formatted_lines.append('')
            continue
        
        # Check if line ends with sentence punctuation
        if line and line[-1] in '.!?。！？':
            # This is the end of a sentence
            current_sentence += (' ' if current_sentence else '') + line
            formatted_lines.append(current_sentence.strip())
            current_sentence = ''
        else:
            # Continue building the sentence
            current_sentence += (' ' if current_sentence else '') + line
    
    # Add any remaining text
    if current_sentence:
        formatted_lines.append(current_sentence.strip())
    
    # Join lines, preserving paragraph breaks (double newlines)
    result = []
    prev_empty = False
    for line in formatted_lines:
        if line == '':
            if not prev_empty:
                result.append('')
            prev_empty = True
        else:
            result.append(line)
            prev_empty = False
    
    return '\n\n'.join(result).strip()


def split_text_into_chunks(text, max_length=4500):
    """
    Split text into chunks that are safe for translation.
    Tries to split at sentence boundaries first, then at line breaks.
    
    Args:
        text: Text to split
        max_length: Maximum length per chunk (default: 4500 to be safe under 5000)
        
    Returns:
        List of text chunks
    """
    chunks = []
    
    # First try to split by double newlines (paragraph breaks)
    paragraphs = text.split('\n\n')
    current_chunk = ''
    
    for paragraph in paragraphs:
        # If adding this paragraph would exceed limit
        if len(current_chunk) + len(paragraph) + 2 > max_length:
            if current_chunk:
                # Save current chunk
                chunks.append(current_chunk.strip())
                current_chunk = ''
            
            # If paragraph itself is too long, split it further
            if len(paragraph) > max_length:
                # Split by single newlines
                lines = paragraph.split('\n')
                for line in lines:
                    if len(current_chunk) + len(line) + 1 > max_length:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                            current_chunk = ''
                    current_chunk += line + '\n' if current_chunk else line
            else:
                current_chunk = paragraph
        else:
            current_chunk += ('\n\n' if current_chunk else '') + paragraph
    
    # Add remaining chunk
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    # If still no chunks (text was empty), return empty list
    if not chunks and text.strip():
        # Fallback: split by fixed length
        chunks = [text[i:i+max_length] for i in range(0, len(text), max_length)]
    
    return chunks


def translate_text(text, source_lang='auto', target_lang='en'):
    """
    Translate text using Google Translator.
    
    Args:
        text: Text to translate
        source_lang: Source language code (default: 'auto' for auto-detect)
        target_lang: Target language code (default: 'en' for English)
        
    Returns:
        Translated text or None if translation fails
    """
    try:
        if source_lang == 'auto':
            translator = GoogleTranslator(source='auto', target=target_lang)
        else:
            translator = GoogleTranslator(source=source_lang, target=target_lang)
        
        # Split text into chunks if too long (Google Translate has 5000 character limit)
        max_length = 4500  # Use 4500 to be safe under the 5000 limit
        if len(text) > max_length:
            chunks = split_text_into_chunks(text, max_length)
            translated_chunks = []
            
            for i, chunk in enumerate(chunks, 1):
                if not chunk.strip():
                    continue
                    
                try:
                    print(f"     Translating chunk {i}/{len(chunks)} ({len(chunk)} chars)...")
                    translated_chunk = translator.translate(chunk)
                    translated_chunks.append(translated_chunk)
                    # Small delay between chunks to avoid rate limiting
                    if i < len(chunks):
                        time.sleep(0.5)
                except Exception as e:
                    print(f"     Error translating chunk {i}: {str(e)}")
                    # Try with smaller chunk if failed
                    if len(chunk) > 1000:
                        # Split further and retry
                        subchunks = split_text_into_chunks(chunk, max_length=1000)
                        for subchunk in subchunks:
                            try:
                                translated_chunk = translator.translate(subchunk)
                                translated_chunks.append(translated_chunk)
                                time.sleep(0.5)
                            except Exception as e2:
                                print(f"     Error with subchunk: {str(e2)}")
                                return None
                    else:
                        return None
            
            return '\n\n'.join(translated_chunks)
        else:
            return translator.translate(text)
    except Exception as e:
        print(f"  Translation error: {str(e)}")
        return None


def translate_transcript_for_video(video_id, transcript_file, target_lang='en', source_lang='auto'):
    """
    Translate a transcript file.
    
    Args:
        video_id: Video ID
        transcript_file: Path to the transcript file
        target_lang: Target language code
        source_lang: Source language code ('auto' for auto-detect)
        
    Returns:
        Tuple of (success: bool, message: str, translated_text: str or None)
    """
    try:
        # Read the transcript
        with open(transcript_file, 'r', encoding='utf-8') as f:
            transcript_text = f.read()
        
        if not transcript_text.strip():
            return False, "Transcript is empty", None
        
        # Format the transcript before translation (remove unnecessary line breaks)
        formatted_text = format_transcript_for_translation(transcript_text)
        
        # Translate the formatted transcript
        translated_text = translate_text(formatted_text, source_lang, target_lang)
        
        if translated_text is None:
            return False, "Translation failed", None
        
        return True, "Translation successful", translated_text
        
    except FileNotFoundError:
        return False, "Transcript file not found", None
    except Exception as e:
        return False, f"Error: {str(e)}", None


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Translate transcripts based on channel language',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python translate_transcripts.py                    # Translate non-English transcripts to English (default)
  python translate_transcripts.py -f videos.csv      # Use a specific CSV file
  python translate_transcripts.py --target fr        # Translate to French instead
        """
    )
    parser.add_argument(
        '--file', '-f',
        type=str,
        default=None,
        metavar='FILE',
        help='Path to the videos.csv file (default: videos.csv)'
    )
    parser.add_argument(
        '--target', '-t',
        type=str,
        default='en',
        metavar='LANG',
        help='Target language code (default: en for English)'
    )
    parser.add_argument(
        '--source', '-s',
        type=str,
        default='auto',
        metavar='LANG',
        help='Source language code (default: auto for auto-detect)'
    )
    parser.add_argument(
        '--skip-french',
        action='store_true',
        help='Skip videos that are already in French (default: False, translates anyway)'
    )
    
    args = parser.parse_args()
    
    # Get the directory where this script is located
    script_dir = Path(__file__).parent
    
    # Determine the videos file path
    if args.file:
        videos_file = Path(args.file)
        if not videos_file.is_absolute():
            videos_file = script_dir / videos_file
    else:
        videos_file = script_dir / 'videos.csv'
    
    transcripts_dir = script_dir / 'transcripts'
    
    # Read videos from CSV
    print(f"Reading videos from {videos_file.name}...")
    videos = read_videos_from_csv(videos_file)
    
    if not videos:
        print("No videos found in CSV file")
        return
    
    print(f"Found {len(videos)} video(s) in CSV\n")
    
    # Process each video
    results = {
        'success': 0,
        'skipped': 0,
        'failed': 0,
        'not_found': 0,
        'total': len(videos)
    }
    
    for i, video_info in enumerate(videos, 1):
        video_id = video_info['video_id']
        channel_name = video_info['channel_name']
        channel_language = video_info['channel_language']
        
        print(f"[{i}/{len(videos)}] Processing: {video_id}")
        if channel_name:
            print(f"  Channel: {channel_name}")
        if channel_language:
            print(f"  Channel language: {channel_language}")
        
        # Find transcript file
        transcript_file = transcripts_dir / f"{video_id}.txt"
        
        if not transcript_file.exists():
            print(f"  ❌ Transcript file not found: {transcript_file.name}\n")
            results['not_found'] += 1
            continue
        
        # Check if we should skip this video (already in target language)
        channel_lang_lower = channel_language.lower() if channel_language else ''
        target_is_english = args.target == 'en'
        target_is_french = args.target == 'fr'
        
        # Skip if already in target language
        if target_is_english and channel_lang_lower in ['en', 'english', 'anglais']:
            print(f"  ℹ️  Already in English, skipping translation\n")
            results['skipped'] += 1
            continue
        
        if target_is_french and channel_lang_lower in ['fr', 'french', 'français']:
            print(f"  ℹ️  Already in French, skipping translation\n")
            results['skipped'] += 1
            continue
        
        if args.skip_french and channel_lang_lower in ['fr', 'french', 'français']:
            print(f"  ⏭️  Skipping (already in French)\n")
            results['skipped'] += 1
            continue
        
        # Determine source language
        source_lang = args.source
        if source_lang == 'auto' and channel_language:
            # Map common language names/codes to language codes
            lang_map = {
                'fr': 'fr',
                'french': 'fr',
                'français': 'fr',
                'en': 'en',
                'english': 'en',
                'anglais': 'en',
                'zh': 'zh',
                'chinese': 'zh',
                'chinois': 'zh',
            }
            source_lang = lang_map.get(channel_lang_lower, 'auto')
        
        # Translate the transcript
        success, message, translated_text = translate_transcript_for_video(
            video_id, transcript_file, args.target, source_lang
        )
        
        if success and translated_text:
            # Save translated transcript over the original file
            with open(transcript_file, 'w', encoding='utf-8') as f:
                f.write(translated_text)
            
            print(f"  ✅ {message}")
            print(f"     Saved to: {transcript_file.name}\n")
            results['success'] += 1
        else:
            print(f"  ❌ {message}\n")
            results['failed'] += 1
    
    # Print summary
    print("=" * 50)
    print(f"Summary:")
    print(f"  Total videos: {results['total']}")
    print(f"  Successfully translated: {results['success']}")
    print(f"  Skipped: {results['skipped']}")
    print(f"  Failed: {results['failed']}")
    print(f"  Transcripts not found: {results['not_found']}")
    print(f"  Translations saved in: {transcripts_dir}")
    print("=" * 50)


if __name__ == '__main__':
    main()

