#!/usr/bin/env python3
"""
Script to split ebook text files into smaller chunks of approximately 5000 characters.
Splits occur at sentence boundaries (preferred) or word boundaries (fallback).
"""

import re
from pathlib import Path


def find_sentence_boundary(text, start_pos, target_length, max_search=500):
    """
    Find the best sentence boundary near the target position.
    
    Args:
        text: Full text to search
        start_pos: Starting position in text
        target_length: Target chunk length
        max_search: Maximum characters to search forward/backward
        
    Returns:
        int: Position to split at (sentence boundary)
    """
    # Calculate target end position
    target_end = start_pos + target_length
    
    # Look for sentence boundaries (period, exclamation, question mark)
    # followed by space, newline, or end of text
    sentence_pattern = r'[.!?][\s\n]|\.$'
    
    # Search backward from target position
    search_start = max(start_pos, target_end - max_search)
    search_end = min(len(text), target_end + max_search)
    search_text = text[search_start:search_end]
    
    # Find all sentence boundaries in the search range
    matches = list(re.finditer(sentence_pattern, search_text))
    
    if matches:
        # Find the match closest to target_end
        best_match = None
        best_distance = float('inf')
        target_relative = target_end - search_start
        
        for match in matches:
            match_pos = search_start + match.end()
            distance = abs(match_pos - target_end)
            if distance < best_distance and match_pos > start_pos:
                best_distance = distance
                best_match = match_pos
        
        if best_match:
            return best_match
    
    # If no sentence boundary found, try word boundary
    return find_word_boundary(text, start_pos, target_length, max_search)


def find_word_boundary(text, start_pos, target_length, max_search=200):
    """
    Find the best word boundary near the target position.
    
    Args:
        text: Full text to search
        start_pos: Starting position in text
        target_length: Target chunk length
        max_search: Maximum characters to search forward/backward
        
    Returns:
        int: Position to split at (word boundary)
    """
    target_end = start_pos + target_length
    
    # Look for word boundaries (whitespace)
    search_start = max(start_pos, target_end - max_search)
    search_end = min(len(text), target_end + max_search)
    search_text = text[search_start:search_end]
    
    # Find whitespace boundaries
    word_pattern = r'\s+'
    matches = list(re.finditer(word_pattern, search_text))
    
    if matches:
        # Find the match closest to target_end
        best_match = None
        best_distance = float('inf')
        target_relative = target_end - search_start
        
        for match in matches:
            match_pos = search_start + match.start()
            distance = abs(match_pos - target_end)
            if distance < best_distance and match_pos > start_pos:
                best_distance = distance
                best_match = match_pos
        
        if best_match:
            return best_match
    
    # Fallback: split at target position (may cut mid-word)
    return target_end


def split_text_into_chunks(text, chunk_size=5000):
    """
    Split text into chunks of approximately chunk_size characters.
    Splits occur at sentence boundaries when possible.
    
    Args:
        text: Text to split
        chunk_size: Target chunk size in characters
        
    Returns:
        List of text chunks
    """
    chunks = []
    current_pos = 0
    text_length = len(text)
    
    while current_pos < text_length:
        # Calculate remaining text
        remaining = text_length - current_pos
        
        # If remaining is less than chunk_size, take all remaining
        if remaining <= chunk_size:
            chunks.append(text[current_pos:])
            break
        
        # Find best split position
        split_pos = find_sentence_boundary(text, current_pos, chunk_size)
        
        # Extract chunk (include the sentence ending)
        chunk = text[current_pos:split_pos].strip()
        
        if chunk:
            chunks.append(chunk)
        
        # Move to next position (skip whitespace after sentence boundary)
        current_pos = split_pos
        # Skip leading whitespace
        while current_pos < text_length and text[current_pos].isspace():
            current_pos += 1
    
    return chunks


def split_ebook_file(input_file, output_dir=None, chunk_size=5000):
    """
    Split a single ebook file into smaller chunks.
    
    Args:
        input_file: Path to input text file
        output_dir: Directory to save chunks (default: same as input file)
        chunk_size: Target chunk size in characters
        
    Returns:
        int: Number of chunks created
    """
    input_path = Path(input_file)
    
    if not input_path.exists():
        print(f"[X] File not found: {input_path}")
        return 0
    
    # Determine output directory
    if output_dir is None:
        output_dir = input_path.parent
    else:
        output_dir = Path(output_dir)
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Read input file
    print(f"\n[*] Reading: {input_path.name}")
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except Exception as e:
        print(f"    [X] Error reading file: {e}")
        return 0
    
    if not text.strip():
        print(f"    [!] File is empty, skipping")
        return 0
    
    print(f"    File size: {len(text):,} characters")
    
    # Split into chunks
    print(f"    Splitting into ~{chunk_size} character chunks...")
    chunks = split_text_into_chunks(text, chunk_size)
    
    if not chunks:
        print(f"    [!] No chunks created")
        return 0
    
    print(f"    Created {len(chunks)} chunks")
    
    # Generate base filename (without extension)
    base_name = input_path.stem
    
    # Save chunks
    saved_count = 0
    for i, chunk in enumerate(chunks, 1):
        # Generate output filename: original_name_part001.txt
        output_filename = f"{base_name}_part{i:03d}.txt"
        output_path = output_dir / output_filename
        
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(chunk)
            saved_count += 1
            print(f"    [+] Saved: {output_filename} ({len(chunk):,} chars)")
        except Exception as e:
            print(f"    [X] Error saving {output_filename}: {e}")
    
    return saved_count


def split_all_ebooks(txt_dir='txt', output_dir=None, chunk_size=5000):
    """
    Split all text files in the ebooks/txt directory.
    
    Args:
        txt_dir: Directory containing text files (relative to script location)
        output_dir: Directory to save chunks (default: txt_dir/chunks)
        chunk_size: Target chunk size in characters
    """
    script_dir = Path(__file__).parent
    txt_path = script_dir / txt_dir
    
    if not txt_path.exists():
        print(f"[X] Directory not found: {txt_path}")
        return
    
    # Find all .txt files
    txt_files = sorted(txt_path.glob('*.txt'))
    
    if not txt_files:
        print(f"[!] No .txt files found in {txt_path}")
        return
    
    print("=" * 60)
    print("SPLITTING EBOOK FILES")
    print("=" * 60)
    print(f"Source directory: {txt_path}")
    print(f"Target chunk size: {chunk_size:,} characters")
    print(f"Found {len(txt_files)} text file(s)")
    
    # Determine output directory
    if output_dir is None:
        output_dir = txt_path / 'chunks'
    else:
        output_dir = Path(output_dir)
    
    print(f"Output directory: {output_dir}")
    print("=" * 60)
    
    total_chunks = 0
    processed_files = 0
    
    for txt_file in txt_files:
        chunks_created = split_ebook_file(txt_file, output_dir, chunk_size)
        if chunks_created > 0:
            total_chunks += chunks_created
            processed_files += 1
    
    print("\n" + "=" * 60)
    print(f"[SUMMARY]")
    print(f"   Files processed: {processed_files}/{len(txt_files)}")
    print(f"   Total chunks created: {total_chunks}")
    print(f"   Output directory: {output_dir}")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Split ebook text files into smaller chunks'
    )
    parser.add_argument(
        '-i', '--input-dir',
        default='txt',
        help='Directory containing text files (default: txt)'
    )
    parser.add_argument(
        '-o', '--output-dir',
        default=None,
        help='Directory to save chunks (default: input_dir/chunks)'
    )
    parser.add_argument(
        '-s', '--chunk-size',
        type=int,
        default=5000,
        help='Target chunk size in characters (default: 5000)'
    )
    parser.add_argument(
        '-f', '--file',
        default=None,
        help='Process a single file instead of all files in directory'
    )
    
    args = parser.parse_args()
    
    if args.file:
        # Process single file
        split_ebook_file(args.file, args.output_dir, args.chunk_size)
    else:
        # Process all files in directory
        split_all_ebooks(args.input_dir, args.output_dir, args.chunk_size)

