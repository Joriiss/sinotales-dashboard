#!/usr/bin/env python3
"""
Script to extract article content from post URLs.
Supports test mode to try on a small sample first.
"""

import csv
import sys
import argparse
import time
import re
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import json

# Headers to use for requests
REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}


def clean_text(text):
    """
    Clean extracted text by removing extra whitespace and normalizing.
    
    Args:
        text: Raw text string
        
    Returns:
        Cleaned text string
    """
    if not text:
        return ''
    
    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text)
    # Remove leading/trailing whitespace
    text = text.strip()
    # Remove multiple newlines
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    
    return text


def parse_date_string(date_str):
    """
    Parse various date string formats and return ISO format date.
    
    Args:
        date_str: Date string in various formats (e.g., "Updated Nov. 5, 2025", "Nov 5, 2025")
        
    Returns:
        ISO format date string (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS) or None
    """
    if not date_str:
        return None
    
    # Remove common prefixes like "Updated", "Published", etc.
    date_str = re.sub(r'^(updated|published|posted|modified|created):?\s*', '', date_str, flags=re.I)
    date_str = date_str.strip()
    
    # Common date formats to try
    date_formats = [
        '%Y-%m-%dT%H:%M:%S%z',  # ISO 8601 with timezone
        '%Y-%m-%dT%H:%M:%S',     # ISO 8601 without timezone
        '%Y-%m-%d %H:%M:%S',     # Space-separated
        '%Y-%m-%d',              # Simple date
        '%B %d, %Y',             # January 15, 2024
        '%b %d, %Y',             # Jan 15, 2024
        '%B. %d, %Y',            # Jan. 15, 2024
        '%b. %d, %Y',            # Jan. 15, 2024
        '%d %B %Y',              # 15 January 2024
        '%d %b %Y',              # 15 Jan 2024
        '%d/%m/%Y',              # DD/MM/YYYY
        '%m/%d/%Y',              # MM/DD/YYYY
        '%Y/%m/%d',              # YYYY/MM/DD
    ]
    
    # Remove timezone info if present (e.g., +08:00, +0800)
    date_str_clean = re.sub(r'[+\-]\d{2}:?\d{2}$', '', date_str.strip())
    
    for fmt in date_formats:
        try:
            dt = datetime.strptime(date_str_clean, fmt)
            # Return in ISO format
            return dt.strftime('%Y-%m-%dT%H:%M:%S')
        except ValueError:
            continue
    
    # Try parsing with dateutil if available (more flexible)
    try:
        from dateutil import parser
        dt = parser.parse(date_str)
        return dt.strftime('%Y-%m-%dT%H:%M:%S')
    except (ImportError, ValueError):
        pass
    
    return None


def extract_date_from_content(soup):
    """
    Extract date from HTML content, looking for common patterns like "Updated Nov. 5, 2025".
    
    Args:
        soup: BeautifulSoup object
        
    Returns:
        ISO format date string or None
    """
    # Method 1: Look for authorupdate or similar classes
    date_selectors = [
        {'class': re.compile(r'authorupdate|author-update|post-date|article-date|published-date|updated-date', re.I)},
        {'class': re.compile(r'date|pubdate|publishdate', re.I)},
    ]
    
    for selector in date_selectors:
        elements = soup.find_all(class_=selector['class'])
        for elem in elements:
            text = elem.get_text()
            # Look for date patterns in the text
            # Pattern: "Updated Nov. 5, 2025" or "Nov 5, 2025" or "2025-11-05"
            date_patterns = [
                r'(?:updated|published|posted|modified):?\s*([A-Z][a-z]+\.?\s+\d{1,2},?\s+\d{4})',
                r'([A-Z][a-z]+\.?\s+\d{1,2},?\s+\d{4})',
                r'(\d{4}-\d{2}-\d{2})',
                r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            ]
            
            for pattern in date_patterns:
                match = re.search(pattern, text, re.I)
                if match:
                    date_str = match.group(1) if match.groups() else match.group(0)
                    parsed_date = parse_date_string(date_str)
                    if parsed_date:
                        return parsed_date
    
    # Method 2: Look for time elements with datetime
    time_elements = soup.find_all('time', datetime=True)
    for time_elem in time_elements:
        date_str = time_elem.get('datetime', '').strip()
        if date_str:
            parsed_date = parse_date_string(date_str)
            if parsed_date:
                return parsed_date
    
    # Method 3: Look for meta tags with dates
    date_meta_selectors = [
        {'property': 'article:published_time'},
        {'property': 'article:modified_time'},
        {'name': 'date'},
        {'name': 'pubdate'},
    ]
    
    for selector in date_meta_selectors:
        meta = soup.find('meta', selector)
        if meta:
            date_str = meta.get('content', '').strip()
            if date_str:
                parsed_date = parse_date_string(date_str)
                if parsed_date:
                    return parsed_date
    
    return None


def extract_article_content(url, base_url=None):
    """
    Extract main article content from an HTML page using multiple methods.
    
    Args:
        url: URL of the post page
        base_url: Optional base URL for Referer header
        
    Returns:
        Dict with 'content' (main text), 'excerpt' (first paragraph), and 'date' (ISO format) or None
    """
    try:
        headers = REQUEST_HEADERS.copy()
        if base_url:
            headers['Referer'] = base_url
        
        response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        if response.status_code != 200:
            return None
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract date from content (before removing elements)
        extracted_date = extract_date_from_content(soup)
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "header", "footer", "aside", "advertisement"]):
            script.decompose()
        
        # Method 1: Try common article/content selectors
        article_selectors = [
            {'tag': 'article'},
            {'class': re.compile(r'article|content|post-content|entry-content|post-body|article-body', re.I)},
            {'id': re.compile(r'article|content|post-content|entry-content|post-body|article-body', re.I)},
            {'itemprop': 'articleBody'},
            {'class': re.compile(r'main-content|main-content|body-content', re.I)},
        ]
        
        content_element = None
        for selector in article_selectors:
            if 'tag' in selector:
                content_element = soup.find(selector['tag'])
            elif 'class' in selector:
                content_element = soup.find(class_=selector['class'])
            elif 'id' in selector:
                content_element = soup.find(id=selector['id'])
            elif 'itemprop' in selector:
                content_element = soup.find(itemprop=selector['itemprop'])
            
            if content_element:
                break
        
        # Method 2: Try JSON-LD structured data
        if not content_element:
            json_scripts = soup.find_all('script', type='application/ld+json')
            for script in json_scripts:
                try:
                    data = json.loads(script.string)
                    if isinstance(data, list):
                        data = data[0] if data else {}
                    
                    # Check for articleBody in JSON-LD
                    if isinstance(data, dict):
                        article_body = data.get('articleBody') or data.get('description') or data.get('text')
                        if article_body:
                            return {
                                'content': clean_text(article_body),
                                'excerpt': clean_text(article_body[:500]),
                                'date': extracted_date
                            }
                except (json.JSONDecodeError, AttributeError, IndexError):
                    continue
        
        # Method 3: Try to find main content area by common patterns
        if not content_element:
            # Look for divs with high text content ratio
            main_content = soup.find('main') or soup.find('div', role='main')
            if main_content:
                content_element = main_content
        
        # Method 4: Try WordPress/Common CMS patterns
        if not content_element:
            wp_patterns = [
                'entry-content', 'post-content', 'article-content',
                'post-body', 'entry-body', 'content-area'
            ]
            for pattern in wp_patterns:
                content_element = soup.find(class_=re.compile(pattern, re.I))
                if content_element:
                    break
        
        # Method 5: Fallback - find largest text container
        if not content_element:
            # Find all divs and pick the one with most text
            divs = soup.find_all('div')
            if divs:
                # Score divs by text length (excluding navigation, footer, etc.)
                scored_divs = []
                for div in divs:
                    # Skip if it's likely navigation/footer/sidebar
                    classes = ' '.join(div.get('class', [])).lower()
                    if any(skip in classes for skip in ['nav', 'menu', 'sidebar', 'footer', 'header', 'ad', 'widget']):
                        continue
                    
                    text = div.get_text()
                    # Count words
                    word_count = len(text.split())
                    if word_count > 100:  # Only consider substantial content
                        scored_divs.append((word_count, div))
                
                if scored_divs:
                    # Sort by word count and take the largest
                    scored_divs.sort(reverse=True, key=lambda x: x[0])
                    content_element = scored_divs[0][1]
        
        # Extract text from found element
        if content_element:
            # Remove unwanted elements from content
            for unwanted in content_element.find_all(['script', 'style', 'nav', 'aside', 'advertisement', 'ad']):
                unwanted.decompose()
            
            # Get all content elements: paragraphs, headings, and list items
            content_elements = content_element.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'])
            text_parts = []
            
            for elem in content_elements:
                # For list items, add bullet point indicator
                if elem.name == 'li':
                    text = elem.get_text().strip()
                    if text and len(text) > 5:  # List items can be shorter
                        # Check if parent is ordered list (ol) or unordered (ul)
                        parent = elem.parent
                        if parent and parent.name == 'ol':
                            # For ordered lists, we'll just add the text (numbering handled by context)
                            text_parts.append(f"• {text}")
                        else:
                            # For unordered lists
                            text_parts.append(f"• {text}")
                else:
                    # For paragraphs, headings, and divs
                    text = elem.get_text().strip()
                    if text and len(text) > 20:  # Only include substantial paragraphs
                        text_parts.append(text)
            
            if text_parts:
                full_content = '\n\n'.join(text_parts)
                full_content = clean_text(full_content)
                
                # Create excerpt (first paragraph or first 500 chars)
                excerpt = text_parts[0] if text_parts else full_content[:500]
                if len(excerpt) > 500:
                    excerpt = excerpt[:500] + '...'
                
                return {
                    'content': full_content,
                    'excerpt': clean_text(excerpt),
                    'date': extracted_date
                }
        
        # Method 6: Last resort - extract all paragraph text and list items
        content_elements = soup.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'])
        if content_elements:
            text_parts = []
            for elem in content_elements:
                # Skip if it looks like navigation or footer
                parent_classes = ' '.join(elem.parent.get('class', [])).lower() if elem.parent else ''
                if any(skip in parent_classes for skip in ['nav', 'menu', 'sidebar', 'footer', 'header']):
                    continue
                
                # Handle list items
                if elem.name == 'li':
                    text = elem.get_text().strip()
                    if text and len(text) > 5:
                        text_parts.append(f"• {text}")
                else:
                    # Handle paragraphs, headings, divs
                    text = elem.get_text().strip()
                    if text and len(text) > 20:
                        text_parts.append(text)
            
            if text_parts:
                full_content = '\n\n'.join(text_parts)
                full_content = clean_text(full_content)
                
                excerpt = text_parts[0] if text_parts else full_content[:500]
                if len(excerpt) > 500:
                    excerpt = excerpt[:500] + '...'
                
                return {
                    'content': full_content,
                    'excerpt': clean_text(excerpt),
                    'date': extracted_date
                }
        
    except Exception as e:
        return None
    
    return None


def generate_filename_from_id(post_id, source=None):
    """
    Generate filename from post ID and source.
    
    Args:
        post_id: Post ID
        source: Optional source name (e.g., "China Highlights")
        
    Returns:
        Safe filename string
    """
    # Extract source slug from source name
    source_slug = ''
    if source:
        # Clean source name to create slug
        source_slug = re.sub(r'[^\w\s-]', '', source.lower())
        source_slug = re.sub(r'\s+', '-', source_slug).strip('-')
    
    # Combine source + ID
    if source_slug:
        filename = f"{source_slug}_{post_id}.txt"
    else:
        filename = f"{post_id}.txt"
    
    return filename


def extract_content_from_posts(input_file, output_file=None, test_mode=False, test_count=10, delay=1.0, content_dir='content', reverse=False):
    """
    Extract content from posts in posts.csv and save each to a separate .txt file.
    
    Args:
        input_file: Path to input posts.csv
        output_file: Path to output file (default: overwrites input_file)
        test_mode: If True, only process a small sample
        test_count: Number of posts to process in test mode
        delay: Delay between requests in seconds
        content_dir: Directory name to store content files (default: 'content')
    """
    script_dir = Path(__file__).parent
    input_path = script_dir / input_file
    
    if output_file is None:
        output_file = input_file
    output_path = script_dir / output_file
    
    # Create content directory
    content_dir_path = script_dir / content_dir
    content_dir_path.mkdir(exist_ok=True)
    
    # Read all posts
    posts = []
    with open(input_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        
        # Check if id column exists (required)
        if 'id' not in fieldnames:
            print("❌ Error: 'id' column not found in posts.csv")
            print("   Please run add_ids.py first to add IDs to all posts")
            return
        
        # Add content_file column if it doesn't exist
        if 'content_file' not in fieldnames:
            fieldnames.append('content_file')
        
        for row in reader:
            # Verify post has an ID
            if not row.get('id', '').strip():
                print(f"⚠️  Warning: Post without ID found: {row.get('title', 'N/A')[:50]}")
                print("   Skipping this post. Please run add_ids.py to fix.")
                continue
            posts.append(row)
    
    # Find posts without content files
    # Check both CSV field and actual file existence using ID
    posts_without_content = []
    posts_with_content_count = 0
    
    for p in posts:
        post_id = p.get('id', '').strip()
        content_file = p.get('content_file', '').strip()
        source = p.get('source', '').strip()
        file_exists = False
        
        # Ensure post has an ID (should already exist from add_ids.py)
        if not post_id:
            # Skip posts without IDs (they should have been added by add_ids.py)
            print(f"⚠️  Warning: Post without ID found, skipping: {p.get('title', 'N/A')[:50]}")
            continue
        
        # If CSV has content_file field, check if file exists
        if content_file:
            content_file_path = content_dir_path / content_file
            if content_file_path.exists():
                # File exists, skip this post
                file_exists = True
                posts_with_content_count += 1
            else:
                # CSV says there's a file but it doesn't exist, need to extract
                posts_without_content.append(p)
        else:
            # No content_file in CSV, generate expected filename from ID and check
            expected_filename = generate_filename_from_id(post_id, source)
            expected_file_path = content_dir_path / expected_filename
            
            if expected_file_path.exists():
                # File exists, update CSV with filename and skip
                p['content_file'] = expected_filename
                file_exists = True
                posts_with_content_count += 1
            else:
                # No file found, need to extract
                posts_without_content.append(p)
    
    print(f"Found {len(posts)} total posts")
    print(f"Found {posts_with_content_count} posts with existing content files")
    print(f"Found {len(posts_without_content)} posts without content")
    
    if test_mode:
        print(f"\n🧪 TEST MODE: Processing only {min(test_count, len(posts_without_content))} posts")
        posts_to_process = posts_without_content[:test_count]
    else:
        posts_to_process = posts_without_content
    
    # Reverse the list if --reverse option is enabled
    if reverse:
        posts_to_process = list(reversed(posts_to_process))
        print(f"\n🔄 REVERSE MODE: Processing from the end of the list")
    
    print(f"\nProcessing {len(posts_to_process)} posts...")
    print("=" * 60)
    
    updated_count = 0
    failed_count = 0
    
    for i, post in enumerate(posts_to_process, 1):
        url = post.get('link', '').strip()
        title = post.get('title', '').strip()[:60]
        
        if not url:
            print(f"[{i}/{len(posts_to_process)}] ⚠️  Skipping: No URL")
            continue
        
        print(f"[{i}/{len(posts_to_process)}] Extracting content from: {title}...")
        print(f"    URL: {url}")
        
        # Extract base URL for Referer header
        parsed_url = urlparse(url)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
        
        result = extract_article_content(url, base_url)
        
        if result and result.get('content'):
            content = result['content']
            excerpt = result.get('excerpt', content[:500])
            extracted_date = result.get('date')
            
            # Update date in post if missing and we found one
            if extracted_date and not post.get('date', '').strip():
                post['date'] = extracted_date
                print(f"    📅 Found and added date: {extracted_date}")
            
            # Get post ID (should already exist from add_ids.py)
            post_id = post.get('id', '').strip()
            if not post_id:
                print(f"    ❌ Error: Post missing ID, skipping")
                continue
            
            source = post.get('source', '').strip()
            filename = generate_filename_from_id(post_id, source)
            content_file_path = content_dir_path / filename
            
            # ID-based filenames should be unique, but check anyway
            if content_file_path.exists():
                # This shouldn't happen with IDs, but handle it
                print(f"    ⚠️  Warning: File {filename} already exists, skipping")
                continue
            
            # Save content to file
            try:
                with open(content_file_path, 'w', encoding='utf-8') as f:
                    # Write metadata header
                    f.write(f"ID: {post_id}\n")
                    f.write(f"Title: {post.get('title', 'N/A')}\n")
                    f.write(f"URL: {url}\n")
                    f.write(f"Source: {post.get('source', 'N/A')}\n")
                    if post.get('date'):
                        f.write(f"Date: {post.get('date')}\n")
                    if post.get('tags'):
                        f.write(f"Tags: {post.get('tags')}\n")
                    f.write("=" * 80 + "\n\n")
                    # Write content
                    f.write(content)
                
                # Update post with content file reference
                post['content_file'] = filename
                
                updated_count += 1
                word_count = len(content.split())
                print(f"    ✅ Extracted {word_count} words")
                print(f"    Saved to: {content_dir}/{filename}")
                print(f"    Preview: {excerpt[:100]}...")
            except Exception as e:
                failed_count += 1
                print(f"    ❌ Error saving file: {str(e)}")
        else:
            failed_count += 1
            print(f"    ❌ Could not extract content")
        
        # Delay between requests to avoid overwhelming servers
        if i < len(posts_to_process):
            time.sleep(delay)
        
        print()
    
    # Update the posts list with extracted content
    posts_dict = {p.get('link', '').rstrip('/'): p for p in posts}
    for post in posts_to_process:
        url_key = post.get('link', '').rstrip('/')
        if url_key in posts_dict:
            posts_dict[url_key] = post
    
    # Write updated posts
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for post in posts_dict.values():
            # Ensure all required fields exist
            row = {field: post.get(field, '') for field in fieldnames}
            writer.writerow(row)
    
    print("=" * 60)
    print(f"✅ Extraction complete!")
    print(f"   Updated: {updated_count} posts")
    print(f"   Failed: {failed_count} posts")
    print(f"   Content files saved to: {content_dir}/")
    print(f"   CSV updated: {output_file}")
    
    if test_mode:
        print(f"\n💡 This was a test run. Remove --test flag to process all posts.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Extract article content from post URLs')
    parser.add_argument('input_file', nargs='?', default='posts.csv', help='Input CSV file (default: posts.csv)')
    parser.add_argument('-o', '--output', help='Output CSV file (default: overwrites input)')
    parser.add_argument('-t', '--test', action='store_true', help='Test mode: process only a small sample')
    parser.add_argument('-n', '--test-count', type=int, default=10, help='Number of posts to process in test mode (default: 10)')
    parser.add_argument('-d', '--delay', type=float, default=1.0, help='Delay between requests in seconds (default: 1.0)')
    parser.add_argument('-c', '--content-dir', default='content', help='Directory to store content files (default: content)')
    parser.add_argument('-r', '--reverse', action='store_true', help='Process posts from the end of the list (useful for parallel processing)')
    
    args = parser.parse_args()
    
    extract_content_from_posts(
        args.input_file,
        args.output,
        test_mode=args.test,
        test_count=args.test_count,
        delay=args.delay,
        content_dir=args.content_dir,
        reverse=args.reverse
    )

