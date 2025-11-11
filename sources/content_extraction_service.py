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
        
        # Method 4.5: Look for content before footer (common pattern)
        if not content_element:
            # Find footer first
            footer = soup.find('footer') or soup.find(class_=re.compile(r'footer', re.I)) or soup.find(id=re.compile(r'footer', re.I))
            if footer:
                # Find the main content container that comes before footer
                # Look for divs that contain substantial text and are before the footer
                all_divs = soup.find_all('div')
                for div in all_divs:
                    # Check if this div comes before footer in the DOM
                    if footer in div.find_all():
                        continue  # Skip if footer is inside this div
                    
                    # Check if div has substantial content
                    classes = ' '.join(div.get('class', [])).lower()
                    if any(skip in classes for skip in ['nav', 'menu', 'sidebar', 'footer', 'header', 'ad', 'widget']):
                        continue
                    
                    text = div.get_text()
                    word_count = len(text.split())
                    if word_count > 200:  # Substantial content
                        paragraphs = div.find_all('p')
                        if len(paragraphs) >= 3:  # Has multiple paragraphs
                            # Check if it's likely main content (has headings)
                            headings = div.find_all(['h1', 'h2', 'h3'])
                            if headings:
                                content_element = div
                                break
        
        # Method 5: Fallback - find largest text container with better filtering
        if not content_element:
            divs = soup.find_all('div')
            if divs:
                scored_divs = []
                for div in divs:
                    classes = ' '.join(div.get('class', [])).lower()
                    # Skip navigation, footer, header, ads, widgets
                    if any(skip in classes for skip in ['nav', 'menu', 'sidebar', 'footer', 'header', 'ad', 'widget', 'cookie', 'popup', 'modal']):
                        continue
                    
                    # Skip if it's clearly a footer/header by ID
                    div_id = div.get('id', '').lower()
                    if any(skip in div_id for skip in ['footer', 'header', 'nav', 'menu', 'sidebar', 'cookie', 'popup']):
                        continue
                    
                    text = div.get_text()
                    word_count = len(text.split())
                    
                    # Skip if text is too short
                    if word_count < 100:
                        continue
                    
                    # Penalize divs with repeated phrases (common in footers)
                    text_lower = text.lower()
                    repeated_phrases = [
                        'need help?', 'request a custom', 'create your trip', 'contact us',
                        'follow us', 'about us', 'terms and conditions', 'privacy policy',
                        'copyright', 'all rights reserved', 'what our customers are saying'
                    ]
                    repeat_count = sum(1 for phrase in repeated_phrases if phrase in text_lower)
                    if repeat_count >= 2:
                        # Likely a footer, skip it
                        continue
                    
                    # Score by word count, but prefer divs with more paragraphs
                    paragraphs = div.find_all('p')
                    paragraph_count = len([p for p in paragraphs if len(p.get_text().strip()) > 50])
                    
                    # Bonus for having headings (h1-h6)
                    headings = div.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                    heading_count = len(headings)
                    
                    # Calculate score: word count + paragraph bonus + heading bonus
                    score = word_count + (paragraph_count * 10) + (heading_count * 5)
                    
                    scored_divs.append((score, word_count, div))
                
                if scored_divs:
                    # Sort by score (highest first)
                    scored_divs.sort(reverse=True, key=lambda x: x[0])
                    content_element = scored_divs[0][2]  # Get the div element
        
        # Extract text from found element
        if content_element:
            # Remove unwanted elements from content
            for unwanted in content_element.find_all(['script', 'style', 'nav', 'aside', 'advertisement', 'ad', 'footer', 'header']):
                unwanted.decompose()
            
            # Remove elements with footer-like classes/IDs
            for unwanted in content_element.find_all(class_=re.compile(r'footer|header|nav|menu|sidebar|widget|cookie|popup|modal', re.I)):
                unwanted.decompose()
            
            for unwanted in content_element.find_all(id=re.compile(r'footer|header|nav|menu|sidebar|widget|cookie|popup|modal', re.I)):
                unwanted.decompose()
            
            # Get all content elements
            content_elements = content_element.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'])
            text_parts = []
            seen_texts = set()  # Track seen text to avoid duplicates
            
            for elem in content_elements:
                # Skip if parent is footer/header/nav
                parent = elem.parent
                if parent:
                    parent_classes = ' '.join(parent.get('class', [])).lower()
                    parent_id = parent.get('id', '').lower()
                    if any(skip in parent_classes or skip in parent_id for skip in ['footer', 'header', 'nav', 'menu', 'sidebar']):
                        continue
                
                if elem.name == 'li':
                    text = elem.get_text().strip()
                    if text and len(text) > 5:
                        # Check for repeated footer phrases
                        text_lower = text.lower()
                        if any(phrase in text_lower for phrase in ['need help?', 'request a custom', 'create your trip', 'contact us', 'follow us', 'copyright']):
                            continue
                        # Avoid duplicates
                        if text not in seen_texts:
                            text_parts.append(f"• {text}")
                            seen_texts.add(text)
                else:
                    text = elem.get_text().strip()
                    if text and len(text) > 20:
                        # Check for repeated footer phrases
                        text_lower = text.lower()
                        if any(phrase in text_lower for phrase in ['need help?', 'request a custom', 'create your trip', 'contact us', 'follow us', 'copyright', 'all rights reserved']):
                            continue
                        # Avoid duplicates
                        if text not in seen_texts:
                            text_parts.append(text)
                            seen_texts.add(text)
            
            if text_parts:
                full_content = '\n\n'.join(text_parts)
                full_content = clean_text(full_content)
                
                # Additional cleanup: remove any remaining footer-like content
                lines = full_content.split('\n')
                cleaned_lines = []
                for line in lines:
                    line_lower = line.lower().strip()
                    # Skip lines that are clearly footer content
                    if any(phrase in line_lower for phrase in [
                        'need help?', 'request a custom', 'create your trip', 
                        'contact us', 'follow us', 'copyright', 'all rights reserved',
                        'terms and conditions', 'privacy policy', 'what our customers are saying'
                    ]):
                        continue
                    # Skip very short lines that are likely navigation
                    if len(line.strip()) < 10:
                        continue
                    cleaned_lines.append(line)
                
                full_content = '\n\n'.join(cleaned_lines)
                full_content = clean_text(full_content)
                
                if full_content and len(full_content.strip()) > 100:  # Ensure we have substantial content
                    excerpt = cleaned_lines[0] if cleaned_lines else full_content[:500]
                    if len(excerpt) > 500:
                        excerpt = excerpt[:500] + '...'
                    
                    return {
                        'content': full_content,
                        'excerpt': clean_text(excerpt),
                        'date': extracted_date
                    }
        
        # Method 6: Last resort - extract all paragraph text and list items with better filtering
        content_elements = soup.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'])
        if content_elements:
            text_parts = []
            seen_texts = set()
            for elem in content_elements:
                # Skip if in footer/header/nav
                parent = elem.parent
                if parent:
                    parent_classes = ' '.join(parent.get('class', [])).lower()
                    parent_id = parent.get('id', '').lower()
                    if any(skip in parent_classes or skip in parent_id for skip in ['nav', 'menu', 'sidebar', 'footer', 'header']):
                        continue
                
                # Skip if element itself has footer-like classes
                elem_classes = ' '.join(elem.get('class', [])).lower()
                elem_id = elem.get('id', '').lower()
                if any(skip in elem_classes or skip in elem_id for skip in ['footer', 'header', 'nav', 'menu', 'sidebar', 'widget']):
                    continue
                
                if elem.name == 'li':
                    text = elem.get_text().strip()
                    if text and len(text) > 5:
                        text_lower = text.lower()
                        if any(phrase in text_lower for phrase in ['need help?', 'request a custom', 'create your trip', 'contact us', 'follow us', 'copyright']):
                            continue
                        if text not in seen_texts:
                            text_parts.append(f"• {text}")
                            seen_texts.add(text)
                else:
                    text = elem.get_text().strip()
                    if text and len(text) > 20:
                        text_lower = text.lower()
                        if any(phrase in text_lower for phrase in ['need help?', 'request a custom', 'create your trip', 'contact us', 'follow us', 'copyright', 'all rights reserved']):
                            continue
                        if text not in seen_texts:
                            text_parts.append(text)
                            seen_texts.add(text)
            
            if text_parts:
                full_content = '\n\n'.join(text_parts)
                full_content = clean_text(full_content)
                
                # Additional cleanup: remove any remaining footer-like content
                lines = full_content.split('\n')
                cleaned_lines = []
                for line in lines:
                    line_lower = line.lower().strip()
                    if any(phrase in line_lower for phrase in [
                        'need help?', 'request a custom', 'create your trip', 
                        'contact us', 'follow us', 'copyright', 'all rights reserved',
                        'terms and conditions', 'privacy policy', 'what our customers are saying'
                    ]):
                        continue
                    if len(line.strip()) < 10:
                        continue
                    cleaned_lines.append(line)
                
                full_content = '\n\n'.join(cleaned_lines)
                full_content = clean_text(full_content)
                
                if full_content and len(full_content.strip()) > 100:  # Ensure we have substantial content
                    excerpt = cleaned_lines[0] if cleaned_lines else full_content[:500]
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

