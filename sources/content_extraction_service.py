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
import subprocess
import time


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


def extract_article_content(url: str, base_url: Optional[str] = None, proxies: Optional[Dict[str, str]] = None) -> Optional[Dict[str, str]]:
    """
    Extract main article content from an HTML page using multiple methods.
    
    Args:
        url: URL of the post page
        base_url: Optional base URL for Referer header
        proxies: Optional proxy dict with 'http' and 'https' keys for requests library
        
    Returns:
        Dict with 'content' (main text), 'excerpt' (first paragraph), and 'date' (ISO format) or None
    """
    try:
        print(f"  [EXTRACT] Fetching URL: {url}", flush=True)
        if proxies:
            print(f"  [EXTRACT] Using proxy: Yes", flush=True)
        else:
            print(f"  [EXTRACT] Using proxy: No", flush=True)
        
        response = None
        
        # Approach 1: Use curl first (most reliable, especially with proxies)
        try:
            print(f"  [EXTRACT] Approach 1: Using curl...", flush=True)
            curl_cmd = ['curl', '-s', '-L', '--max-time', '30']
            
            # Add proxy if available
            if proxies and proxies.get('http'):
                proxy_url = proxies['http']
                curl_cmd.extend(['--proxy', proxy_url])
                # Skip SSL verification for proxy connections (common with proxies)
                curl_cmd.extend(['-k', '--proxy-insecure'])
            
            curl_cmd.append(url)
            
            result = subprocess.run(
                curl_cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',  # Replace invalid UTF-8 sequences instead of failing
                timeout=35
            )
            
            if result.returncode != 0:
                if result.stderr:
                    print(f"  [EXTRACT] Curl stderr: {result.stderr[:300]}", flush=True)
            
            if result.returncode == 0 and result.stdout:
                # Validate it's HTML, not a challenge page
                content_str = result.stdout[:200].lower()
                if 'cloudflare' in content_str or 'challenge' in content_str:
                    print(f"  [EXTRACT] ✗ Curl returned Cloudflare challenge, trying next approach...", flush=True)
                elif '<html' in content_str or '<!doctype' in content_str:
                    print(f"  [EXTRACT] ✓ Success with curl (got {len(result.stdout)} bytes, valid HTML)", flush=True)
                    class MockResponse:
                        def __init__(self, content, status_code=200):
                            self.content = content.encode('utf-8') if isinstance(content, str) else content
                            self.text = content if isinstance(content, str) else content.decode('utf-8', errors='ignore')
                            self.status_code = status_code
                            self.headers = {'Content-Type': 'text/html'}
                    response = MockResponse(result.stdout, 200)
                else:
                    print(f"  [EXTRACT] ✗ Curl content doesn't appear to be HTML, trying next approach...", flush=True)
            else:
                if result.stderr:
                    print(f"  [EXTRACT] ✗ Curl failed: {result.stderr[:200]}", flush=True)
                else:
                    print(f"  [EXTRACT] ✗ Curl failed: return code {result.returncode}, no output", flush=True)
        except FileNotFoundError:
            print(f"  [EXTRACT] ✗ Curl not found in PATH, trying next approach...", flush=True)
        except subprocess.TimeoutExpired:
            print(f"  [EXTRACT] ✗ Curl timeout, trying next approach...", flush=True)
        except Exception as e:
            print(f"  [EXTRACT] ✗ Curl failed: {type(e).__name__}: {str(e)}, trying next approach...", flush=True)
        
        # Approach 2: Full headers with session (visit homepage first)
        if not response:
            try:
                print(f"  [EXTRACT] Approach 2: Full headers with session...", flush=True)
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Referer': f"{base_url}/" if base_url else None,
                }
                if not headers['Referer']:
                    del headers['Referer']
                
                session = requests.Session()
                session.headers.update(headers)
                
                # Visit homepage first to establish session/cookies
                if base_url:
                    try:
                        print(f"  [EXTRACT]   Visiting homepage to establish session...", flush=True)
                        session.get(base_url, timeout=10, allow_redirects=True, proxies=proxies)
                        time.sleep(1.0)
                    except Exception as e:
                        print(f"  [EXTRACT]   Homepage visit failed: {str(e)}", flush=True)
                
                response = session.get(url, timeout=30, allow_redirects=True, proxies=proxies)
                print(f"  [EXTRACT]   Approach 2 response: HTTP {response.status_code}", flush=True)
                
                if response.status_code == 200:
                    content_str = response.text[:200].lower() if hasattr(response, 'text') else ''
                    if 'cloudflare' in content_str or 'challenge' in content_str:
                        print(f"  [EXTRACT] ✗ Approach 2 returned Cloudflare challenge, trying next approach...", flush=True)
                        response = None
                    elif '<html' in content_str or '<!doctype' in content_str:
                        print(f"  [EXTRACT] ✓ Success with Approach 2 (content: {len(response.content)} bytes, valid HTML)", flush=True)
                    else:
                        print(f"  [EXTRACT] ✗ Approach 2 content doesn't appear to be HTML, trying next approach...", flush=True)
                        response = None
                else:
                    print(f"  [EXTRACT] ✗ Approach 2 failed: HTTP {response.status_code}", flush=True)
                    response = None
            except Exception as e:
                print(f"  [EXTRACT] ✗ Approach 2 failed: {type(e).__name__}: {str(e)}", flush=True)
                response = None
        
        # Approach 3: Minimal headers
        if not response:
            try:
                print(f"  [EXTRACT] Approach 3: Minimal headers...", flush=True)
                minimal_headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                }
                if base_url:
                    minimal_headers['Referer'] = f"{base_url}/"
                
                response = requests.get(url, headers=minimal_headers, timeout=30, allow_redirects=True, proxies=proxies)
                print(f"  [EXTRACT]   Approach 3 response: HTTP {response.status_code}", flush=True)
                
                if response.status_code == 200:
                    content_str = response.text[:200].lower() if hasattr(response, 'text') else ''
                    if 'cloudflare' in content_str or 'challenge' in content_str:
                        print(f"  [EXTRACT] ✗ Approach 3 returned Cloudflare challenge", flush=True)
                        response = None
                    elif '<html' in content_str or '<!doctype' in content_str:
                        print(f"  [EXTRACT] ✓ Success with Approach 3 (content: {len(response.content)} bytes, valid HTML)", flush=True)
                    else:
                        print(f"  [EXTRACT] ✗ Approach 3 content doesn't appear to be HTML", flush=True)
                        response = None
                else:
                    print(f"  [EXTRACT] ✗ Approach 3 failed: HTTP {response.status_code}", flush=True)
                    response = None
            except Exception as e:
                print(f"  [EXTRACT] ✗ Approach 3 failed: {type(e).__name__}: {str(e)}", flush=True)
                response = None
        
        # Final check - if all approaches failed
        if not response or (hasattr(response, 'status_code') and response.status_code != 200):
            status_code = response.status_code if (response and hasattr(response, 'status_code')) else 'No response'
            print(f"  [EXTRACT] ✗ All approaches failed: HTTP {status_code}", flush=True)
            
            if response and hasattr(response, 'text'):
                content_preview = response.text[:500].lower()
                if 'cloudflare' in content_preview or 'challenge' in content_preview:
                    return {'error': f'Cloudflare challenge (HTTP {status_code})', 'status_code': status_code}
                elif '<html' in content_preview:
                    return {'error': f'HTML error page (HTTP {status_code})', 'status_code': status_code}
            
            return {'error': f'HTTP {status_code}', 'status_code': status_code}
        
        # Check if response content is valid HTML
        if not response.content or len(response.content) == 0:
            print(f"  [EXTRACT] ✗ Error: Empty response content", flush=True)
            return {'error': 'Empty response content'}
        
        content_preview = response.text[:200].lower() if hasattr(response, 'text') else ''
        if 'cloudflare' in content_preview or 'challenge' in content_preview:
            print(f"  [EXTRACT] ✗ Error: Cloudflare challenge detected in content", flush=True)
            return {'error': 'Cloudflare challenge in content'}
        
        print(f"  [EXTRACT] Response size: {len(response.content)} bytes", flush=True)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract date from content (before removing elements)
        extracted_date = extract_date_from_content(soup)
        if extracted_date:
            print(f"  [EXTRACT] Found date: {extracted_date}", flush=True)
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "header", "footer", "aside", "advertisement"]):
            script.decompose()
        
        # Method 1: Try common article/content selectors
        print(f"  [EXTRACT] Method 1: Trying common article/content selectors...", flush=True)
        article_selectors = [
            {'tag': 'article'},
            {'class': re.compile(r'article|content|post-content|entry-content|post-body|article-body', re.I)},
            {'id': re.compile(r'article|content|post-content|entry-content|post-body|article-body', re.I)},
            {'itemprop': 'articleBody'},
            {'class': re.compile(r'main-content|main-content|body-content', re.I)},
        ]
        
        content_element = None
        for i, selector in enumerate(article_selectors, 1):
            if 'tag' in selector:
                content_element = soup.find(selector['tag'])
                selector_desc = f"tag: {selector['tag']}"
            elif 'class' in selector:
                content_element = soup.find(class_=selector['class'])
                selector_desc = f"class: {selector['class'].pattern}"
            elif 'id' in selector:
                content_element = soup.find(id=selector['id'])
                selector_desc = f"id: {selector['id'].pattern}"
            elif 'itemprop' in selector:
                content_element = soup.find(itemprop=selector['itemprop'])
                selector_desc = f"itemprop: {selector['itemprop']}"
            
            if content_element:
                print(f"  [EXTRACT] ✓ Method 1: Found content element using {selector_desc}", flush=True)
                break
            else:
                print(f"  [EXTRACT]   Method 1.{i}: No match for {selector_desc}", flush=True)
        
        # Method 2: Try JSON-LD structured data
        if not content_element:
            print(f"  [EXTRACT] Method 2: Trying JSON-LD structured data...", flush=True)
            json_scripts = soup.find_all('script', type='application/ld+json')
            print(f"  [EXTRACT]   Found {len(json_scripts)} JSON-LD scripts", flush=True)
            for script in json_scripts:
                try:
                    data = json.loads(script.string)
                    if isinstance(data, list):
                        data = data[0] if data else {}
                    
                    if isinstance(data, dict):
                        article_body = data.get('articleBody') or data.get('description') or data.get('text')
                        if article_body:
                            print(f"  [EXTRACT] ✓ Method 2: Found content in JSON-LD structured data", flush=True)
                            return {
                                'content': clean_text(article_body),
                                'excerpt': clean_text(article_body[:500]),
                                'date': extracted_date
                            }
                except (json.JSONDecodeError, AttributeError, IndexError):
                    continue
            print(f"  [EXTRACT]   Method 2: No content found in JSON-LD structured data", flush=True)
        
        # Method 3: Try to find main content area
        if not content_element:
            print(f"  [EXTRACT] Method 3: Trying main content area...", flush=True)
            main_content = soup.find('main') or soup.find('div', role='main')
            if main_content:
                content_element = main_content
                print(f"  [EXTRACT] ✓ Method 3: Found main content area", flush=True)
            else:
                print(f"  [EXTRACT]   Method 3: No main content area found", flush=True)
        
        # Method 4: Try WordPress/Common CMS patterns
        if not content_element:
            print(f"  [EXTRACT] Method 4: Trying WordPress/CMS patterns...", flush=True)
            wp_patterns = [
                'entry-content', 'post-content', 'article-content',
                'post-body', 'entry-body', 'content-area'
            ]
            for pattern in wp_patterns:
                content_element = soup.find(class_=re.compile(pattern, re.I))
                if content_element:
                    print(f"  [EXTRACT] ✓ Method 4: Found content using pattern: {pattern}", flush=True)
                    break
            if not content_element:
                print(f"  [EXTRACT]   Method 4: No WordPress/CMS patterns matched", flush=True)
        
        # Method 4.5: Look for content before footer (common pattern)
        if not content_element:
            print(f"  [EXTRACT] Method 4.5: Looking for content before footer...", flush=True)
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
                                print(f"  [EXTRACT] ✓ Method 4.5: Found content before footer", flush=True)
                                break
            if not content_element:
                print(f"  [EXTRACT]   Method 4.5: No content found before footer", flush=True)
        
        # Method 5: Fallback - find largest text container with better filtering
        if not content_element:
            print(f"  [EXTRACT] Method 5: Finding largest text container...", flush=True)
            divs = soup.find_all('div')
            print(f"  [EXTRACT]   Found {len(divs)} div elements to evaluate", flush=True)
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
                    top_score, top_words, _ = scored_divs[0]
                    print(f"  [EXTRACT] ✓ Method 5: Found best content element (score: {top_score}, words: {top_words})", flush=True)
                else:
                    print(f"  [EXTRACT]   Method 5: No suitable div elements found", flush=True)
        
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
                    
                    print(f"  [EXTRACT] ✓ Successfully extracted content ({len(full_content)} chars, {len(cleaned_lines)} lines)", flush=True)
                    return {
                        'content': full_content,
                        'excerpt': clean_text(excerpt),
                        'date': extracted_date
                    }
                else:
                    content_len = len(full_content.strip()) if full_content else 0
                    print(f"  [EXTRACT]   Content too short: {content_len} chars (minimum: 100)", flush=True)
        
        # Method 6: Last resort - extract all paragraph text and list items with better filtering
        if not content_element:
            print(f"  [EXTRACT] Method 6: Last resort - extracting all paragraphs/list items...", flush=True)
        content_elements = soup.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'])
        if content_elements:
            print(f"  [EXTRACT]   Found {len(content_elements)} content elements", flush=True)
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
                    
                    print(f"  [EXTRACT] ✓ Method 6: Successfully extracted content ({len(full_content)} chars, {len(cleaned_lines)} lines)", flush=True)
                    return {
                        'content': full_content,
                        'excerpt': clean_text(excerpt),
                        'date': extracted_date
                    }
                else:
                    content_len = len(full_content.strip()) if full_content else 0
                    print(f"  [EXTRACT]   Method 6: Content too short: {content_len} chars (minimum: 100)", flush=True)
        else:
            print(f"  [EXTRACT]   Method 6: No content elements found", flush=True)
        
        # If we get here, no content was extracted
        print(f"  [EXTRACT] ✗ Failed: No content could be extracted using any method", flush=True)
        return {'error': 'No content could be extracted - all extraction methods failed'}
        
    except requests.exceptions.Timeout:
        error_msg = 'Request timeout'
        print(f"  [EXTRACT] ✗ Error: {error_msg}", flush=True)
        return {'error': error_msg}
    except requests.exceptions.ConnectionError as e:
        error_msg = f'Connection error: {str(e)}'
        print(f"  [EXTRACT] ✗ Error: {error_msg}", flush=True)
        return {'error': error_msg}
    except Exception as e:
        error_msg = f'Exception: {type(e).__name__}: {str(e)}'
        print(f"  [EXTRACT] ✗ Error: {error_msg}", flush=True)
        import traceback
        print(f"  [EXTRACT] Traceback: {traceback.format_exc()[:500]}", flush=True)
        return {'error': error_msg}
    
    return {'error': 'Unknown error'}

