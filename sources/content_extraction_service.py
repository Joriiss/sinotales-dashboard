"""
Service for extracting article content from URLs
Based on extract_content.py script
"""
import re
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from typing import Dict, Optional
from datetime import datetime


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
        date_str: Date string in various formats
        
    Returns:
        ISO format date string (YYYY-MM-DDTHH:MM:SS) or None
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
    
    # Remove timezone info if present
    date_str_clean = re.sub(r'[+\-]\d{2}:?\d{2}$', '', date_str.strip())
    
    for fmt in date_formats:
        try:
            dt = datetime.strptime(date_str_clean, fmt)
            return dt.strftime('%Y-%m-%dT%H:%M:%S')
        except ValueError:
            continue
    
    # Try parsing with dateutil if available
    try:
        from dateutil import parser
        dt = parser.parse(date_str)
        return dt.strftime('%Y-%m-%dT%H:%M:%S')
    except (ImportError, ValueError):
        pass
    
    return None


def extract_date_from_content(soup):
    """
    Extract date from HTML content.
    
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


def extract_article_content(url: str, base_url: Optional[str] = None) -> Optional[Dict[str, str]]:
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
        
        # Method 3: Try to find main content area
        if not content_element:
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
            divs = soup.find_all('div')
            if divs:
                scored_divs = []
                for div in divs:
                    classes = ' '.join(div.get('class', [])).lower()
                    if any(skip in classes for skip in ['nav', 'menu', 'sidebar', 'footer', 'header', 'ad', 'widget']):
                        continue
                    
                    text = div.get_text()
                    word_count = len(text.split())
                    if word_count > 100:
                        scored_divs.append((word_count, div))
                
                if scored_divs:
                    scored_divs.sort(reverse=True, key=lambda x: x[0])
                    content_element = scored_divs[0][1]
        
        # Extract text from found element
        if content_element:
            # Remove unwanted elements from content
            for unwanted in content_element.find_all(['script', 'style', 'nav', 'aside', 'advertisement', 'ad']):
                unwanted.decompose()
            
            # Get all content elements
            content_elements = content_element.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'])
            text_parts = []
            
            for elem in content_elements:
                if elem.name == 'li':
                    text = elem.get_text().strip()
                    if text and len(text) > 5:
                        text_parts.append(f"• {text}")
                else:
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
        
        # Method 6: Last resort - extract all paragraph text
        content_elements = soup.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'])
        if content_elements:
            text_parts = []
            for elem in content_elements:
                parent_classes = ' '.join(elem.parent.get('class', [])).lower() if elem.parent else ''
                if any(skip in parent_classes for skip in ['nav', 'menu', 'sidebar', 'footer', 'header']):
                    continue
                
                if elem.name == 'li':
                    text = elem.get_text().strip()
                    if text and len(text) > 5:
                        text_parts.append(f"• {text}")
                else:
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

