#!/usr/bin/env python3
"""
Script to clean up posts.csv by removing false positives (non-China related posts).
"""

import csv
import sys
from pathlib import Path

# Import the is_china_related function from get_posts_list
sys.path.insert(0, str(Path(__file__).parent))
from get_posts_list import is_china_related

def clean_posts_csv(input_file, output_file=None):
    """
    Clean posts.csv by removing non-China related posts.
    
    Args:
        input_file: Path to input posts.csv
        output_file: Path to output file (default: overwrites input_file)
    """
    
    script_dir = Path(__file__).parent
    input_path = script_dir / input_file
    
    # If no output file specified, overwrite the input file
    if output_file is None:
        output_file = input_file
    
    output_path = script_dir / output_file
    
    # Read blogs.csv to get filter_china settings
    blogs_path = script_dir / 'blogs.csv'
    blog_filter_china = {}
    if blogs_path.exists():
        with open(blogs_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                blog_name = row.get('name', '').strip()
                filter_china = row.get('filter_china', '').strip()
                # Store True if filter_china is "True" (case-insensitive), False otherwise
                blog_filter_china[blog_name] = filter_china.lower() == 'true'
    
    # Read all posts
    posts = []
    with open(input_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        # Normalize field names
        fieldnames = [k.strip() for k in reader.fieldnames] if reader.fieldnames else []
        for row in reader:
            normalized_row = {k.strip(): v.strip() if v else '' for k, v in row.items()}
            posts.append(normalized_row)
    
    # Import helper functions
    from get_posts_list import is_job_posting, is_non_travel_content
    
    # Filter posts - keep only China-related ones, exclude job postings and non-travel content
    china_posts = []
    removed_posts = []
    
    for post in posts:
        link = post.get('link', '').strip()
        source = post.get('source', '').strip()
        
        # Skip job postings
        if is_job_posting(post):
            removed_posts.append(post)
            continue
        
        # Skip non-travel content (legal pages, business pages, etc.)
        if is_non_travel_content(post):
            removed_posts.append(post)
            continue
        
        # If the blog has filter_china = False, keep all posts from that blog
        if source in blog_filter_china and not blog_filter_china[source]:
            china_posts.append(post)
        # For other sources, check if URL is China-related
        elif link and is_china_related(link):
            china_posts.append(post)
        else:
            removed_posts.append(post)
    
    # Write cleaned posts
    if china_posts:
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for post in china_posts:
                writer.writerow(post)
    
    # Write removed posts to a separate CSV for review
    removed_output_path = script_dir / 'removed_posts.csv'
    if removed_posts:
        with open(removed_output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for post in removed_posts:
                writer.writerow(post)
    
    print(f"Cleaned posts.csv -> {output_file}")
    print(f"   Kept: {len(china_posts)} posts")
    print(f"   Removed: {len(removed_posts)} false positives")
    if removed_posts:
        print(f"   Removed posts saved to: removed_posts.csv")
    
    if removed_posts:
        print(f"\n   Sample removed posts:")
        for post in removed_posts[:10]:
            print(f"     - {post.get('title', 'N/A')[:60]}...")
        if len(removed_posts) > 10:
            print(f"     ... and {len(removed_posts) - 10} more")

if __name__ == '__main__':
    clean_posts_csv('posts.csv')

