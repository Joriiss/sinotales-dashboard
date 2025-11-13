#!/usr/bin/env python3
"""
Script to find RSS feeds and sitemaps for blogs listed in blogs.csv and extract posts.
If an RSS feed is found, it adds the feed URL to blogs.csv. If a sitemap is found,
it parses the sitemap to extract additional post URLs and fetches their metadata.
Creates posts.csv with the list of articles (title, link, date, tags).
"""

import os
import csv
import argparse
import time
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
import feedparser

# Common RSS feed paths to try
COMMON_FEED_PATHS = [
    '/feed',
    '/rss',
    '/rss.xml',
    '/feed.xml',
    '/atom.xml',
    '/atom',
    '/index.xml',
    '/blog/feed',
    '/blog/rss',
    '/blog/feed.xml',
    '/blog/rss.xml',
]

# Common sitemap paths to try
COMMON_SITEMAP_PATHS = [
    '/sitemap.xml',
    '/sitemap_index.xml',
    '/sitemaps.xml',
    '/sitemap/sitemap.xml',
]

# Headers to use for requests (some sites block requests without proper User-Agent)
# Base headers that will be used for all requests
BASE_REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml,application/atom+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Cache-Control': 'max-age=0',
}

# Legacy name for backward compatibility
REQUEST_HEADERS = BASE_REQUEST_HEADERS


def get_request_headers(base_url=None, minimal=False):
    """
    Get request headers with optional Referer header based on base_url.
    Some sites require a Referer header to allow access.
    
    Args:
        base_url: Optional base URL to use as Referer
        minimal: If True, return only essential headers (helps bypass some blocks)
        
    Returns:
        Dict of headers
    """
    if minimal:
        # Minimal headers - some sites block requests with too many headers
        headers = {
            'User-Agent': BASE_REQUEST_HEADERS['User-Agent'],
            'Accept': 'application/xml, text/xml, */*',
        }
    else:
        headers = BASE_REQUEST_HEADERS.copy()
    
    if base_url:
        # Add Referer header pointing to the base domain
        parsed = urlparse(base_url)
        headers['Referer'] = f"{parsed.scheme}://{parsed.netloc}/"
        if not minimal:
            headers['Origin'] = f"{parsed.scheme}://{parsed.netloc}"
    return headers


def is_english_version(url):
    """
    Check if a URL is the English version by looking for language codes in the path.
    
    Args:
        url: URL to check
        
    Returns:
        True if it's English (no language code or /en/), False otherwise
    """
    url_lower = url.lower()
    
    # Common language codes to exclude (non-English)
    # Format: /lang-code/ or /lang-code-XX/ where lang-code is 2-3 letters
    non_english_codes = [
        '/da/', '/de/', '/fr/', '/es/', '/it/', '/pt/', '/ru/', '/ja/', '/ko/',
        '/zh/', '/ar/', '/hi/', '/nl/', '/sv/', '/no/', '/fi/', '/pl/', '/tr/',
        '/cs/', '/hu/', '/ro/', '/bg/', '/hr/', '/sk/', '/sl/', '/et/', '/lv/',
        '/lt/', '/el/', '/he/', '/th/', '/vi/', '/id/', '/ms/', '/uk/', '/be/',
        '/sr/', '/mk/', '/sq/', '/is/', '/ga/', '/mt/', '/cy/', '/eu/', '/ca/',
        '/gl/', '/af/', '/sw/', '/zu/', '/xh/', '/am/', '/bn/', '/gu/', '/kn/',
        '/ml/', '/mr/', '/ne/', '/pa/', '/si/', '/ta/', '/te/', '/ur/', '/my/',
        '/km/', '/lo/', '/ka/', '/hy/', '/az/', '/kk/', '/ky/', '/mn/', '/uz/',
        '/tg/', '/ps/', '/fa/', '/ku/', '/yi/', '/yi/', '/yi/'
    ]
    
    # Check if URL contains a non-English language code
    for code in non_english_codes:
        if code in url_lower:
            return False
    
    # If it has /en/ or /en-US/, /en-GB/, etc., it's explicitly English
    if '/en' in url_lower:
        return True
    
    # If no language code is found, assume it's English (default language)
    # We've already checked for non-English codes above, so if we get here,
    # it's either English or doesn't have a language code (defaults to English)
    return True


def is_job_posting(post):
    """
    Check if a post is a job posting based on title and tags.
    
    Args:
        post: Post dict with 'title' and 'tags' fields
        
    Returns:
        True if it appears to be a job posting, False otherwise
    """
    title = post.get('title', '').lower()
    tags = post.get('tags', '').lower()
    combined = f"{title} {tags}"
    
    # Job-related keywords
    job_keywords = [
        'career', 'careers', 'job', 'jobs', 'hiring', 'position', 'vacancy',
        'vacancies', 'recruit', 'recruitment', 'intern', 'internship', 'internships',
        'manager', 'director', 'coordinator', 'specialist', 'associate', 'executive',
        'applicant', 'application', 'apply now', 'join our team', 'we are hiring',
        'open position', 'full-time', 'part-time', 'remote position'
    ]
    
    # Check if any job keyword appears
    for keyword in job_keywords:
        if keyword in combined:
            return True
    
    return False


def is_non_travel_content(post):
    """
    Check if a post is non-travel content (legal pages, business pages, etc.).
    
    Args:
        post: Post dict with 'title', 'link', and 'tags' fields
        
    Returns:
        True if it appears to be non-travel content, False otherwise
    """
    title = post.get('title', '').lower()
    link = post.get('link', '').lower()
    tags = post.get('tags', '').lower()
    combined = f"{title} {link} {tags}"
    
    # Legal/Business page patterns in URL
    legal_url_patterns = [
        '/aboutus/', '/about-us/', '/terms', '/disclaimer', '/privacy',
        '/contact', '/partner/', '/partners/', '/affiliate', '/sitemap',
        '/legal/', '/policy/', '/policies/'
    ]
    
    for pattern in legal_url_patterns:
        if pattern in link:
            return True
    
    # Legal/Business keywords in title
    legal_keywords = [
        'terms and conditions', 'privacy policy', 'disclaimer',
        'contact us', 'about us', 'sitemap'
    ]
    
    for keyword in legal_keywords:
        if keyword in title:
            return True
    
    # Partnership/Business development keywords
    business_keywords = [
        'partnership opportunities', 'travel partner', 'become a partner',
        'affiliate program', 'agent opportunities', 'advisors', 'b2b'
    ]
    
    for keyword in business_keywords:
        if keyword in combined:
            return True
    
    # Company news/announcements (more aggressive filtering)
    company_news_keywords = [
        'wins award', 'won award', 'receives award', 'award winner',
        'company news', 'announcement', 'announces',
        'expands partnership', 'new partnership', 'partners with'
    ]
    
    # Only filter if it's clearly company-focused, not travel-focused
    if any(keyword in combined for keyword in company_news_keywords):
        # But keep it if it's about travel destinations or experiences
        travel_indicators = ['tour', 'travel', 'destination', 'visit', 'guide', 'trip', 
                           'place', 'places', 'location', 'region', 'city', 'town',
                           'attraction', 'attractions', 'sightseeing', 'explore', 'exploring']
        # Also check for common travel-related patterns
        has_travel_content = any(indicator in combined for indicator in travel_indicators)
        # Check if it mentions a specific location (likely travel-related)
        # Common patterns: "in [place]", "at [place]", "[place] update", etc.
        location_patterns = [
            r'\b(in|at|to|from|near|around)\s+[A-Z][a-z]+',  # "in Yunnan", "at Beijing"
            r'[A-Z][a-z]+\s+(update|news|guide|tour|travel)',  # "Yunnan update", "Beijing guide"
        ]
        has_location = any(re.search(pattern, combined) for pattern in location_patterns)
        
        if not (has_travel_content or has_location):
            return True
    
    # Special case: "in the news" tag - only filter if it's clearly company news without travel context
    if 'in the news' in combined:
        # Keep if it mentions locations, travel, or destinations
        travel_indicators = ['tour', 'travel', 'destination', 'visit', 'guide', 'trip',
                           'place', 'places', 'location', 'region', 'city', 'town',
                           'attraction', 'attractions', 'earthquake', 'weather', 'festival',
                           'culture', 'food', 'cuisine', 'restaurant', 'hotel']
        # Check for location names (capitalized words that might be places)
        has_location = bool(re.search(r'\b[A-Z][a-z]+\s+(update|news|guide|tour|travel|earthquake|weather)', combined))
        if not (any(indicator in combined for indicator in travel_indicators) or has_location):
            # Only filter if it's clearly company-focused (awards, partnerships, etc.)
            company_focused = any(word in combined for word in ['award', 'partnership', 'nominated', 'winner', 'selected'])
            if company_focused:
                return True
    
    return False


def generate_id_from_url(url):
    """
    Generate a unique ID from URL using hash.
    
    Args:
        url: Post URL
        
    Returns:
        Unique ID string (12-character hex)
    """
    # Use hash of normalized URL to generate ID
    url_normalized = url.rstrip('/').lower()
    # Generate a positive hash and convert to hex for shorter ID
    url_hash = abs(hash(url_normalized))
    # Use 12-character hex representation
    return f"{url_hash:012x}"


def is_china_related(url):
    """
    Check if a URL is related to China based on keywords in the URL.
    Excludes Chinatowns and Chinese-related content in other countries.
    
    Args:
        url: URL to check
        
    Returns:
        True if URL contains China-related keywords, False otherwise
    """
    url_lower = url.lower()
    
    # Exclude URLs that are clearly about other countries
    # Check for country codes in the path (common in Lonely Planet URLs)
    exclude_countries = [
        '/usa/', '/united-states/', '/america/', '/american/',
        '/australia/', '/canada/', '/uk/', '/united-kingdom/', '/britain/',
        '/france/', '/germany/', '/italy/', '/spain/', '/japan/', '/korea/',
        '/thailand/', '/vietnam/', '/singapore/', '/malaysia/', '/indonesia/',
        '/philippines/', '/india/', '/brazil/', '/mexico/', '/argentina/',
        '/new-zealand/', '/south-africa/', '/egypt/', '/turkey/', '/greece/'
    ]
    
    # If URL contains a non-China country code, it's likely not about China
    for country in exclude_countries:
        if country in url_lower:
            # Exception: if it also explicitly mentions China in the path, it might be relevant
            if '/china/' not in url_lower:
                return False
    
    # Strong indicators that it's about China (these take priority)
    strong_indicators = [
        '/china/',  # Explicit China path (e.g., /china/beijing/)
        '/taiwan/', '/taipei/',  # Taiwan is part of China context
        '/hong-kong/', '/hongkong/', '/macau/', '/macao/',  # Special regions
    ]
    
    for indicator in strong_indicators:
        if indicator in url_lower:
            return True
    
    # China-related keywords (but be careful with "chinatown" and "chinese" in other contexts)
    china_keywords = [
        # Major cities (only if not in excluded countries)
        'beijing', 'peking', 'shanghai', 'guangzhou', 'canton', 'shenzhen', 
        'chengdu', 'xian', 'xi\'an', 'hangzhou', 'nanjing', 'wuhan', 
        'chongqing', 'tianjin', 'suzhou', 'dalian', 'qingdao', 'xiamen',
        'foshan', 'dongguan', 'zhengzhou', 'changsha', 'kunming', 'fuzhou',
        'wuxi', 'hefei', 'nanning', 'shijiazhuang', 'haerbin', 'harbin',
        'jinan', 'taiyuan', 'changchun', 'nanchang', 'guiyang', 'lanzhou',
        # Provinces and regions
        'guangdong', 'jiangsu', 'shandong', 'zhejiang', 'henan', 'sichuan',
        'hubei', 'hunan', 'anhui', 'hebei', 'jiangxi', 'shanxi', 'liaoning',
        'fujian', 'yunnan', 'guangxi', 'heilongjiang', 'jilin', 'shaanxi',
        'guizhou', 'xinjiang', 'tibet', 'qinghai', 'gansu', 'inner-mongolia',
        'ningxia',
        # Regions and areas
        'yangtze', 'yellow-river', 'pearl-river', 'tibetan',
        'manchuria', 'dongbei', 'northeast-china',
        # Other related terms
        'great-wall', 'terracotta', 'forbidden-city', 'panda', 'silk-road'
    ]
    
    # Check for China keywords, but exclude "chinatown" and "chinese" unless in China context
    for keyword in china_keywords:
        if keyword in url_lower:
            return True
    
    # Check for "china" or "chinese" but only if not in excluded country context
    # and not just "chinatown" in other countries
    if 'china' in url_lower or 'chinese' in url_lower:
        # If it's just "chinatown" without other China indicators, be cautious
        if 'chinatown' in url_lower:
            # Only accept if there are other strong China indicators
            if any(indicator in url_lower for indicator in strong_indicators):
                return True
            # Or if it's in a China city/province context
            if any(city in url_lower for city in ['beijing', 'shanghai', 'guangzhou', 'chengdu', 'xian']):
                return True
            return False
        # For "china" or "chinese" (not chinatown), check if it's in excluded country
        # If we got here, we already checked exclude_countries above
        return True
    
    return False


def read_blogs_from_csv(file_path):
    """
    Read blog information from blogs.csv.
    
    Args:
        file_path: Path to the blogs.csv file
        
    Returns:
        List of dicts with keys: 'name', 'url', 'language', 'rss_feed', 'sitemap', 'filter_china', 'blog_only'
    """
    blogs = []
    
    if not os.path.exists(file_path):
        print(f"Warning: {file_path} does not exist")
        return blogs
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Normalize row keys by stripping whitespace
                normalized_row = {k.strip(): v.strip() if v else '' for k, v in row.items()}
                
                blog_url = normalized_row.get('url', '').strip()
                if blog_url:
                    # Parse filter_china field (default to False if not present or empty)
                    filter_china_str = normalized_row.get('filter_china', '').strip().lower()
                    filter_china = filter_china_str in ('true', '1', 'yes', 'y')
                    
                    # Parse blog_only field (default to True if not present or empty, for backward compatibility)
                    blog_only_str = normalized_row.get('blog_only', '').strip().lower()
                    blog_only = blog_only_str not in ('false', '0', 'no', 'n')  # Default to True
                    
                    # Handle both 'sitemap' and 'sitemaps' fields for backward compatibility
                    sitemap = normalized_row.get('sitemaps', '').strip() or normalized_row.get('sitemap', '').strip()
                    
                    blogs.append({
                        'name': normalized_row.get('name', '').strip(),
                        'url': blog_url,
                        'language': normalized_row.get('language', '').strip(),
                        'rss_feed': normalized_row.get('rss_feed', '').strip(),
                        'sitemap': sitemap,
                        'filter_china': filter_china,
                        'blog_only': blog_only
                    })
    except Exception as e:
        print(f"Error reading {file_path}: {str(e)}")
        return []
    
    return blogs


def find_rss_feed_in_html(html_content, base_url):
    """
    Parse HTML content to find RSS feed links.
    
    Args:
        html_content: HTML content as string
        base_url: Base URL for resolving relative links
        
    Returns:
        RSS feed URL if found, None otherwise
    """
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Look for RSS feed links in <link> tags
        # Common types: application/rss+xml, application/atom+xml, text/xml
        feed_types = ['application/rss+xml', 'application/atom+xml', 'text/xml', 'application/xml']
        
        for link in soup.find_all('link'):
            rel = link.get('rel', [])
            href = link.get('href', '')
            type_attr = link.get('type', '')
            
            # Check if it's a feed link
            if 'alternate' in rel and any(ft in type_attr for ft in feed_types):
                if href:
                    return urljoin(base_url, href)
            
            # Also check for rel="feed" or rel="rss"
            if any(r in ['feed', 'rss', 'alternate'] for r in rel) and href:
                if any(ext in href.lower() for ext in ['.xml', 'rss', 'feed', 'atom']):
                    return urljoin(base_url, href)
        
        # Look for <a> tags with RSS/feed in href or text
        for link in soup.find_all('a', href=True):
            href = link.get('href', '').lower()
            text = link.get_text().lower()
            if any(keyword in href or keyword in text for keyword in ['rss', 'feed', 'atom', '.xml']):
                full_url = urljoin(base_url, link.get('href'))
                if full_url.endswith(('.xml', '/feed', '/rss', '/atom')):
                    return full_url
                    
    except Exception as e:
        print(f"     ⚠️  Error parsing HTML: {str(e)}")
    
    return None


def is_valid_rss_feed(response_content, content_type=None, debug=False):
    """
    Validate if the response content is a valid RSS/Atom feed.
    
    Args:
        response_content: The response content (bytes or string)
        content_type: Optional Content-Type header value
        debug: If True, print debug information
        
    Returns:
        True if valid feed, False otherwise
    """
    # First check Content-Type header if provided
    if content_type:
        content_type_lower = content_type.lower()
        if debug:
            print(f"         Content-Type: {content_type}")
        if any(ct in content_type_lower for ct in ['application/rss+xml', 'application/atom+xml', 
                                                    'application/xml', 'text/xml', 'application/rdf+xml']):
            # Content-Type suggests it's a feed, now verify by parsing
            pass
        elif 'html' in content_type_lower:
            # If it's HTML, it's definitely not a feed
            if debug:
                print(f"         Rejected: Content-Type is HTML")
            return False
    
    # Try to parse as feed
    try:
        feed = feedparser.parse(response_content)
        
        # Check for feed-like content in the raw response first
        content_str = response_content.decode('utf-8', errors='ignore') if isinstance(response_content, bytes) else str(response_content)
        has_rss_tags = any(tag in content_str.lower() for tag in ['<rss', '<feed', '<channel', '<item', '<entry'])
        
        if debug:
            print(f"         Has RSS tags: {has_rss_tags}")
            print(f"         Content preview (first 200 chars): {content_str[:200]}")
        
        # If it doesn't have RSS/Atom tags at all, it's not a feed
        if not has_rss_tags:
            if debug:
                print(f"         Rejected: No RSS/Atom tags found")
            return False
        
        # Check if feedparser successfully parsed it as a feed structure
        # feed.feed is a dict with feed metadata (title, link, etc.)
        # feed.entries is a list of feed entries
        has_entries = bool(feed.entries)
        has_feed_metadata = bool(feed.feed and (feed.feed.get('title') or feed.feed.get('link')))
        
        if debug:
            print(f"         Has entries: {has_entries} (count: {len(feed.entries) if feed.entries else 0})")
            print(f"         Has feed metadata: {has_feed_metadata}")
            if feed.feed:
                print(f"         Feed title: {feed.feed.get('title', 'N/A')}")
                print(f"         Feed link: {feed.feed.get('link', 'N/A')}")
            print(f"         Bozo: {feed.bozo}")
            if feed.bozo and feed.bozo_exception:
                print(f"         Bozo exception: {feed.bozo_exception}")
        
        # Valid feed if:
        # 1. Has entries (actual posts) - definitely a valid feed, OR
        # 2. Has feed metadata (title/link) - valid feed structure even if empty, OR
        # 3. Has RSS/Atom tags and no major parsing errors
        if has_entries or has_feed_metadata:
            # If bozo is True, check if it's a minor issue or major problem
            if feed.bozo and feed.bozo_exception:
                # Some minor parsing issues are OK (like missing namespaces)
                # But major issues (not XML at all) should be rejected
                error_str = str(feed.bozo_exception).lower()
                if 'not well-formed' in error_str or 'not xml' in error_str or 'syntax error' in error_str:
                    if debug:
                        print(f"         Rejected: Major parsing error - {error_str}")
                    return False
            if debug:
                print(f"         ✅ Valid feed (has entries or metadata)")
            return True
        elif has_rss_tags:
            # Has RSS tags but feedparser didn't parse it well
            # Check if it's a major parsing error
            if feed.bozo and feed.bozo_exception:
                error_str = str(feed.bozo_exception).lower()
                if 'not well-formed' in error_str or 'not xml' in error_str or 'syntax error' in error_str:
                    if debug:
                        print(f"         Rejected: Major parsing error - {error_str}")
                    return False
            # If it has RSS tags and no major errors, accept it
            if debug:
                print(f"         ✅ Valid feed (has RSS tags, no major errors)")
            return True
        else:
            if debug:
                print(f"         Rejected: No entries, no metadata, or major errors")
            
    except Exception as e:
        # If parsing completely fails, it's not a valid feed
        if debug:
            print(f"         Exception during parsing: {str(e)}")
        pass
    
    return False


def try_common_feed_paths(base_url):
    """
    Try common RSS feed paths for a given blog URL.
    
    Args:
        base_url: Base URL of the blog
        
    Returns:
        RSS feed URL if found, None otherwise
    """
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    
    # Use headers with Referer to help bypass 403 errors
    headers = get_request_headers(base_url)
    
    for path in COMMON_FEED_PATHS:
        feed_url = urljoin(base, path)
        try:
            print(f"     Trying: {feed_url}")
            response = requests.get(feed_url, headers=headers, timeout=10, allow_redirects=True)
            print(f"       Status: {response.status_code}, Content-Type: {response.headers.get('Content-Type', 'N/A')}")
            # Accept 200 (OK) and 202 (Accepted) - some servers return 202 even with valid content
            if response.status_code in (200, 202):
                # Validate that it's actually an RSS/Atom feed
                content_type = response.headers.get('Content-Type', '')
                is_valid = is_valid_rss_feed(response.content, content_type, debug=True)
                print(f"       Valid feed: {is_valid}")
                if is_valid:
                    return feed_url
                else:
                    # Debug: try with trailing slash if path doesn't have one
                    if not feed_url.endswith('/'):
                        feed_url_with_slash = feed_url + '/'
                        try:
                            print(f"     Trying with trailing slash: {feed_url_with_slash}")
                            response_slash = requests.get(feed_url_with_slash, headers=headers, timeout=10, allow_redirects=True)
                            print(f"       Status: {response_slash.status_code}, Content-Type: {response_slash.headers.get('Content-Type', 'N/A')}")
                            if response_slash.status_code in (200, 202):
                                content_type_slash = response_slash.headers.get('Content-Type', '')
                                is_valid_slash = is_valid_rss_feed(response_slash.content, content_type_slash, debug=True)
                                print(f"       Valid feed: {is_valid_slash}")
                                if is_valid_slash:
                                    return feed_url_with_slash
                        except Exception as e:
                            print(f"       Error with trailing slash: {str(e)}")
            elif response.status_code not in (200, 202):
                print(f"       Skipping (status {response.status_code})")
        except requests.RequestException as e:
            print(f"       Request error: {str(e)}")
            continue
        except Exception as e:
            print(f"       Error: {str(e)}")
            continue
    
    return None


def find_rss_feed(blog_url):
    """
    Find RSS feed for a given blog URL.
    Tries multiple methods: checking HTML for feed links, trying common paths.
    
    Args:
        blog_url: URL of the blog
        
    Returns:
        RSS feed URL if found, None otherwise
    """
    print(f"  🔍 Searching for RSS feed...")
    
    # Use headers with Referer to help bypass 403 errors
    headers = get_request_headers(blog_url)
    
    # Method 1: Try to fetch the blog page and look for RSS links in HTML
    try:
        response = requests.get(blog_url, headers=headers, timeout=10, allow_redirects=True)
        if response.status_code in (200, 202):
            feed_url = find_rss_feed_in_html(response.text, response.url)
            if feed_url:
                # Validate that the URL is actually a feed
                try:
                    feed_response = requests.get(feed_url, headers=headers, timeout=10, allow_redirects=True)
                    if feed_response.status_code in (200, 202):
                        content_type = feed_response.headers.get('Content-Type', '')
                        if is_valid_rss_feed(feed_response.content, content_type):
                            print(f"     ✅ Found feed in HTML: {feed_url}")
                            return feed_url
                        else:
                            print(f"     ⚠️  URL found in HTML but not a valid feed: {feed_url}")
                except requests.RequestException as e:
                    print(f"     ⚠️  Could not fetch feed from HTML link: {str(e)}")
    except requests.RequestException as e:
        print(f"     ⚠️  Could not fetch blog page: {str(e)}")
    except Exception as e:
        print(f"     ⚠️  Error processing blog page: {str(e)}")
    
    # Method 2: Try common feed paths
    print(f"     Trying common feed paths...")
    feed_url = try_common_feed_paths(blog_url)
    if feed_url:
        print(f"     ✅ Found feed at common path: {feed_url}")
        return feed_url
    
    print(f"     ❌ No RSS feed found")
    return None


def is_valid_sitemap(response_content, content_type=None):
    """
    Validate if the response content is a valid sitemap XML.
    
    Args:
        response_content: The response content (bytes or string)
        content_type: Optional Content-Type header value
        
    Returns:
        True if valid sitemap, False otherwise
    """
    # Check Content-Type header if provided
    if content_type:
        content_type_lower = content_type.lower()
        if 'html' in content_type_lower:
            return False
    
    # Check for sitemap XML tags
    try:
        content_str = response_content.decode('utf-8', errors='ignore') if isinstance(response_content, bytes) else str(response_content)
        has_sitemap_tags = any(tag in content_str.lower() for tag in ['<urlset', '<sitemapindex', '<url>', '<sitemap>'])
        return has_sitemap_tags
    except Exception:
        return False


def find_sitemap(blog_url):
    """
    Find sitemap.xml for a given blog URL.
    Tries multiple methods: checking robots.txt, trying common paths.
    
    Args:
        blog_url: URL of the blog
        
    Returns:
        Sitemap URL if found, None otherwise
    """
    print(f"  🔍 Searching for sitemap.xml...")
    
    parsed = urlparse(blog_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    
    # Method 1: Check robots.txt for Sitemap directive
    try:
        robots_url = urljoin(base, '/robots.txt')
        headers = get_request_headers(blog_url)
        response = requests.get(robots_url, headers=headers, timeout=10, allow_redirects=True)
        if response.status_code == 200:
            for line in response.text.split('\n'):
                line = line.strip()
                if line.lower().startswith('sitemap:'):
                    sitemap_url = line.split(':', 1)[1].strip()
                    # Validate the sitemap
                    try:
                        sitemap_response = requests.get(sitemap_url, headers=headers, timeout=10, allow_redirects=True)
                        if sitemap_response.status_code == 200:
                            content_type = sitemap_response.headers.get('Content-Type', '')
                            if is_valid_sitemap(sitemap_response.content, content_type):
                                print(f"     ✅ Found sitemap in robots.txt: {sitemap_url}")
                                return sitemap_url
                    except:
                        pass
    except:
        pass
    
    # Method 2: Try common sitemap paths
    headers = get_request_headers(blog_url)
    for path in COMMON_SITEMAP_PATHS:
        sitemap_url = urljoin(base, path)
        try:
            response = requests.get(sitemap_url, headers=headers, timeout=10, allow_redirects=True)
            if response.status_code == 200:
                content_type = response.headers.get('Content-Type', '')
                if is_valid_sitemap(response.content, content_type):
                    print(f"     ✅ Found sitemap at: {sitemap_url}")
                    return sitemap_url
        except:
            continue
    
    print(f"     ❌ No sitemap.xml found")
    return None


def parse_sitemap(sitemap_url, filter_posts_only=True, base_url=None):
    """
    Parse sitemap.xml and extract URLs.
    
    Args:
        sitemap_url: URL of the sitemap
        filter_posts_only: If True, only process post-related sitemaps (post-sitemap*.xml)
        base_url: Optional base URL to use for Referer header (helps bypass 403 errors)
        
    Returns:
        List of URLs found in the sitemap
    """
    urls = []
    
    try:
        # If base_url is not provided, extract it from sitemap_url for Referer header
        if not base_url:
            parsed = urlparse(sitemap_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
        
        # Try multiple approaches to bypass 403 errors
        response = None
        
        # Approach 1: Full headers with session (visit homepage first)
        try:
            headers = get_request_headers(base_url)
            session = requests.Session()
            session.headers.update(headers)
            
            # Try to visit homepage first to establish session/cookies
            try:
                homepage_response = session.get(base_url, timeout=10, allow_redirects=True)
                time.sleep(1.0)  # Delay to mimic human behavior
            except:
                pass
            
            response = session.get(sitemap_url, timeout=30, allow_redirects=True)
            if response.status_code in (200, 202):
                # Success! (202 Accepted is also valid - some servers return it with content)
                pass
        except Exception as e:
            pass
        
        # Approach 2: If first approach failed, try with minimal headers
        if not response or response.status_code not in (200, 202):
            try:
                minimal_headers = get_request_headers(base_url, minimal=True)
                response = requests.get(sitemap_url, headers=minimal_headers, timeout=30, allow_redirects=True)
                if response.status_code in (200, 202):
                    # Success with minimal headers! (202 Accepted is also valid)
                    pass
            except Exception as e:
                pass
        
        # Approach 3: If still failing, try with just User-Agent and Referer
        if not response or response.status_code not in (200, 202):
            try:
                simple_headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Referer': f"{base_url}/",
                }
                response = requests.get(sitemap_url, headers=simple_headers, timeout=30, allow_redirects=True)
            except Exception as e:
                pass
        
        # Final check - accept 200 (OK) and 202 (Accepted)
        if not response or response.status_code not in (200, 202):
            status_code = response.status_code if response else 'No response'
            print(f"     ⚠️  Could not fetch sitemap: HTTP {status_code}")
            
            # Debug info for various error codes
            if response and response.status_code == 403:
                if hasattr(response, 'text') and response.text:
                    response_preview = response.text[:200].lower()
                    if 'cloudflare' in response_preview or 'challenge' in response_preview:
                        print(f"     ℹ️  Site appears to be using Cloudflare protection")
                        print(f"     💡 Tip: This site may require JavaScript or browser automation to access")
                    elif 'access denied' in response_preview or 'forbidden' in response_preview:
                        print(f"     ℹ️  Access denied by server - may require authentication or whitelisting")
            elif response and response.status_code == 202:
                # 202 Accepted - check if content is actually valid
                if hasattr(response, 'content') and response.content:
                    # Try to parse as XML to see if it's valid
                    try:
                        test_soup = BeautifulSoup(response.content, 'xml')
                        if test_soup.find('urlset') or test_soup.find('sitemapindex'):
                            print(f"     ℹ️  Received 202 Accepted but content appears valid - attempting to parse")
                            # Continue processing with this response (don't return)
                        else:
                            print(f"     ℹ️  Received 202 Accepted but content doesn't appear to be a sitemap")
                            return urls
                    except Exception as e:
                        print(f"     ℹ️  Received 202 Accepted but content is not valid XML: {str(e)}")
                        return urls
                else:
                    return urls
            else:
                return urls
        
        soup = BeautifulSoup(response.content, 'xml')
        
        # Check if it's a sitemap index (contains other sitemaps)
        sitemapindex = soup.find('sitemapindex')
        if sitemapindex:
            # It's a sitemap index, extract sitemap URLs
            sitemap_list = []
            for sitemap_tag in soup.find_all('sitemap'):
                loc = sitemap_tag.find('loc')
                if loc and loc.text:
                    sitemap_list.append(loc.text.strip())
            
            # Process each sitemap with error handling
            for sitemap_url_to_check in sitemap_list:
                # Filter to only process post-related sitemaps
                if filter_posts_only:
                    # Process sitemaps that contain "post-sitemap", "article", or "articles" in the URL
                    # Skip page-sitemap, guide-sitemap, category-sitemap, etc.
                    sitemap_lower = sitemap_url_to_check.lower()
                    if ('post-sitemap' in sitemap_lower or 
                        'article' in sitemap_lower):
                        print(f"     📄 Processing post sitemap: {sitemap_url_to_check}")
                        try:
                            nested_urls = parse_sitemap(sitemap_url_to_check, filter_posts_only=False, base_url=base_url)
                            urls.extend(nested_urls)
                            # Small delay between sitemap requests
                            time.sleep(0.5)
                        except Exception as e:
                            print(f"     ⚠️  Failed to process sitemap {sitemap_url_to_check}: {str(e)}")
                            continue
                    else:
                        print(f"     ⏭️  Skipping non-post sitemap: {sitemap_url_to_check}")
                else:
                    # If filter_posts_only is False, process all sitemaps (for nested calls)
                    try:
                        nested_urls = parse_sitemap(sitemap_url_to_check, filter_posts_only=False, base_url=base_url)
                        urls.extend(nested_urls)
                        # Small delay between sitemap requests
                        time.sleep(0.5)
                    except Exception as e:
                        print(f"     ⚠️  Failed to process sitemap {sitemap_url_to_check}: {str(e)}")
                        continue
        else:
            # Regular sitemap with URLs
            for url_tag in soup.find_all('url'):
                loc = url_tag.find('loc')
                if loc and loc.text:
                    urls.append(loc.text.strip())
        
        print(f"     📝 Found {len(urls)} URLs in sitemap")
        
    except requests.exceptions.Timeout:
        print(f"     ⚠️  Timeout fetching sitemap: {sitemap_url}")
        return urls
    except requests.exceptions.RequestException as e:
        print(f"     ⚠️  Request error fetching sitemap {sitemap_url}: {str(e)}")
        return urls
    except Exception as e:
        print(f"     ❌ Error parsing sitemap {sitemap_url}: {str(e)}")
        return urls
    
    return urls


def extract_post_metadata_from_html(url, base_url=None):
    """
    Extract post metadata (title, date, tags) from an HTML page.
    
    Args:
        url: URL of the post page
        base_url: Optional base URL to use for Referer header (helps bypass 403 errors)
        
    Returns:
        Dict with 'title', 'link', 'date', 'tags' or None if extraction fails
    """
    try:
        # Use headers with Referer if base_url is provided
        headers = get_request_headers(base_url) if base_url else REQUEST_HEADERS
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        if response.status_code != 200:
            return None
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract title
        title = ''
        # Try various methods to find title
        if soup.find('title'):
            title = soup.find('title').get_text().strip()
        elif soup.find('h1'):
            title = soup.find('h1').get_text().strip()
        elif soup.find('meta', property='og:title'):
            title = soup.find('meta', property='og:title').get('content', '').strip()
        
        # Extract date
        date_str = ''
        # Try various date meta tags
        date_selectors = [
            ('meta', {'property': 'article:published_time'}),
            ('meta', {'property': 'article:modified_time'}),
            ('meta', {'name': 'date'}),
            ('meta', {'name': 'pubdate'}),
            ('time', {'datetime': True}),
        ]
        
        for tag_name, attrs in date_selectors:
            tag = soup.find(tag_name, attrs)
            if tag:
                if tag_name == 'time':
                    date_str = tag.get('datetime', '').strip()
                else:
                    date_str = tag.get('content', '').strip()
                if date_str:
                    break
        
        # Extract tags/categories
        tags_list = []
        # Try various methods to find tags
        tag_selectors = [
            ('meta', {'property': 'article:tag'}),
            ('meta', {'name': 'keywords'}),
            ('a', {'rel': 'tag'}),
            ('a', {'class': lambda x: x and 'tag' in x.lower()}),
        ]
        
        for tag_name, attrs in tag_selectors:
            tags = soup.find_all(tag_name, attrs)
            for tag in tags:
                if tag_name == 'meta':
                    tag_text = tag.get('content', '').strip()
                else:
                    tag_text = tag.get_text().strip()
                if tag_text and tag_text not in tags_list:
                    tags_list.append(tag_text)
        
        # Also check for common WordPress/SEO plugin patterns
        keywords_meta = soup.find('meta', {'name': 'keywords'})
        if keywords_meta:
            keywords = keywords_meta.get('content', '').strip()
            if keywords:
                for kw in keywords.split(','):
                    kw = kw.strip()
                    if kw and kw not in tags_list:
                        tags_list.append(kw)
        
        tags_str = ', '.join(tags_list) if tags_list else ''
        
        if title:
            return {
                'title': title,
                'link': url,
                'date': date_str,
                'tags': tags_str
            }
        
    except Exception as e:
        pass
    
    return None


def parse_feed(feed_url):
    """
    Parse RSS feed and extract posts.
    
    Args:
        feed_url: URL of the RSS feed
        
    Returns:
        List of dicts with keys: 'title', 'link', 'date', 'tags'
    """
    posts = []
    
    try:
        feed = feedparser.parse(feed_url)
        
        if feed.bozo and feed.bozo_exception:
            print(f"     ⚠️  Feed parsing warning: {feed.bozo_exception}")
        
        for entry in feed.entries:
            title = entry.get('title', '').strip()
            link = entry.get('link', '').strip()
            
            # Try to get date from various fields
            date_str = ''
            if hasattr(entry, 'published'):
                date_str = entry.published
            elif hasattr(entry, 'updated'):
                date_str = entry.updated
            elif hasattr(entry, 'published_parsed') and entry.published_parsed:
                from time import struct_time
                date_str = time.strftime('%Y-%m-%d %H:%M:%S', entry.published_parsed)
            elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                date_str = time.strftime('%Y-%m-%d %H:%M:%S', entry.updated_parsed)
            
            # Extract categories/tags
            tags_list = []
            # feedparser stores categories in entry.tags (list of dicts with 'term' key)
            if hasattr(entry, 'tags') and entry.tags:
                for tag in entry.tags:
                    if isinstance(tag, dict) and 'term' in tag:
                        tags_list.append(tag['term'])
                    elif isinstance(tag, str):
                        tags_list.append(tag)
            # Also check for category field (some feeds use this)
            if hasattr(entry, 'category'):
                if isinstance(entry.category, list):
                    tags_list.extend([str(c) for c in entry.category])
                else:
                    tags_list.append(str(entry.category))
            
            # Join tags with comma and space
            tags_str = ', '.join(tags_list) if tags_list else ''
            
            if title and link:
                posts.append({
                    'title': title,
                    'link': link,
                    'date': date_str,
                    'tags': tags_str
                })
        
        print(f"     📝 Found {len(posts)} posts in feed")
        
    except Exception as e:
        print(f"     ❌ Error parsing feed: {str(e)}")
    
    return posts


def write_blogs_to_csv(blogs, file_path):
    """
    Write blogs back to CSV with RSS feed and sitemap information.
    
    Args:
        blogs: List of blog dicts
        file_path: Path to the CSV file
    """
    fieldnames = ['name', 'url', 'language', 'rss_feed', 'sitemaps', 'filter_china', 'blog_only']
    
    try:
        with open(file_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for blog in blogs:
                writer.writerow({
                    'name': blog.get('name', ''),
                    'url': blog.get('url', ''),
                    'language': blog.get('language', ''),
                    'rss_feed': blog.get('rss_feed', ''),
                    'sitemaps': blog.get('sitemap', ''),  # Write as 'sitemaps' to match CSV header
                    'filter_china': 'True' if blog.get('filter_china', False) else 'False',
                    'blog_only': 'True' if blog.get('blog_only', True) else 'False'  # Default to True
                })
    except Exception as e:
        print(f"Error writing {file_path}: {str(e)}")


def read_existing_posts_from_csv(file_path):
    """
    Read existing posts from CSV file and return a set of existing URLs.
    
    Args:
        file_path: Path to the CSV file
        
    Returns:
        Set of existing post URLs (normalized by stripping trailing slashes)
    """
    existing_urls = set()
    
    if not os.path.exists(file_path):
        return existing_urls
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Normalize row keys by stripping whitespace (CSV may have trailing spaces in headers)
                normalized_row = {k.strip(): v.strip() if v else '' for k, v in row.items()}
                link = normalized_row.get('link', '').strip()
                if link:
                    # Normalize URL by stripping trailing slash for comparison
                    existing_urls.add(link.rstrip('/'))
    except Exception as e:
        print(f"Warning: Could not read existing posts from {file_path}: {str(e)}")
    
    return existing_urls


def read_all_existing_posts_from_csv(file_path):
    """
    Read all existing posts from CSV file.
    
    Args:
        file_path: Path to the CSV file
        
    Returns:
        List of post dicts
    """
    existing_posts = []
    
    if not os.path.exists(file_path):
        return existing_posts
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Normalize row keys and values by stripping whitespace (CSV may have trailing spaces)
                normalized_row = {k.strip(): v.strip() if v else '' for k, v in row.items()}
                existing_posts.append(normalized_row)
    except Exception as e:
        print(f"Warning: Could not read existing posts from {file_path}: {str(e)}")
    
    return existing_posts


def write_posts_to_csv(posts, file_path):
    """
    Write posts to CSV file (overwrites existing file).
    
    Args:
        posts: List of post dicts
        file_path: Path to the CSV file
    """
    # Always include id, title, link, date, tags
    fieldnames = ['id', 'title', 'link', 'date', 'tags']
    # Check if source field exists in any post
    has_source = any('source' in post for post in posts) if posts else False
    if has_source:
        fieldnames.append('source')
    
    # Generate IDs for posts that don't have them
    for post in posts:
        if not post.get('id', '').strip():
            url = post.get('link', '').strip()
            if url:
                post['id'] = generate_id_from_url(url)
            else:
                # Fallback: use hash of title
                title = post.get('title', '').strip()
                if title:
                    post['id'] = f"{abs(hash(title)):012x}"
                else:
                    # Last resort: generate from row index
                    post['id'] = f"{abs(hash(str(len(posts)))):012x}"
    
    try:
        with open(file_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for post in posts:
                # Only write fields that exist in fieldnames
                row = {k: v for k, v in post.items() if k in fieldnames}
                writer.writerow(row)
    except Exception as e:
        print(f"Error writing {file_path}: {str(e)}")


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Find RSS feeds for blogs and extract posts',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python get_posts_list.py                              # Process all blogs from blogs.csv
  python get_posts_list.py -f blogs_sample.csv          # Use a different CSV file
  python get_posts_list.py --blog "China Journey"       # Process only "China Journey" blog
  python get_posts_list.py --blog 3                     # Process only the 3rd blog in the CSV
        """
    )
    parser.add_argument(
        '--file', '-f',
        type=str,
        default=None,
        metavar='FILE',
        help='Path to the CSV file containing blog information (default: blogs.csv)'
    )
    parser.add_argument(
        '--blog', '-b',
        type=str,
        default=None,
        metavar='NAME_OR_LINE',
        help='Process only a specific blog. Can be a blog name (partial match) or line number (1-indexed). Example: --blog "China Journey" or --blog 3'
    )
    
    args = parser.parse_args()
    
    # Get the directory where this script is located
    script_dir = Path(__file__).parent
    
    # Determine the blogs file path
    if args.file:
        blogs_file = Path(args.file)
        if not blogs_file.is_absolute():
            blogs_file = script_dir / blogs_file
    else:
        blogs_file = script_dir / 'blogs.csv'
    
    posts_file = script_dir / 'posts.csv'
    
    # Read existing posts to avoid re-processing
    print(f"Reading existing posts from {posts_file.name}...")
    existing_urls = read_existing_posts_from_csv(posts_file)
    existing_posts_count = len(existing_urls)
    if existing_posts_count > 0:
        print(f"  Found {existing_posts_count} existing posts")
    print()
    
    # Read blogs from CSV
    print(f"Reading blogs from {blogs_file.name}...")
    all_blogs = read_blogs_from_csv(blogs_file)
    
    if not all_blogs:
        print(f"No blogs found in {blogs_file}")
        return
    
    # Filter to specific blog if requested
    blogs_to_process = all_blogs.copy()  # Start with all blogs
    if args.blog:
        try:
            # Try to parse as line number (1-indexed)
            line_num = int(args.blog)
            if 1 <= line_num <= len(all_blogs):
                blogs_to_process = [all_blogs[line_num - 1]]
                print(f"Filtering to blog at line {line_num}: {blogs_to_process[0].get('name', 'Unknown')}")
            else:
                print(f"Error: Line number {line_num} is out of range (1-{len(all_blogs)})")
                return
        except ValueError:
            # Not a number, treat as name (case-insensitive partial match)
            blog_name_lower = args.blog.lower()
            matching_blogs = [b for b in all_blogs if blog_name_lower in b.get('name', '').lower()]
            if matching_blogs:
                blogs_to_process = matching_blogs
                if len(blogs_to_process) == 1:
                    print(f"Filtering to blog: {blogs_to_process[0].get('name', 'Unknown')}")
                else:
                    print(f"Found {len(blogs_to_process)} blog(s) matching '{args.blog}':")
                    for b in blogs_to_process:
                        print(f"  - {b.get('name', 'Unknown')}")
            else:
                print(f"Error: No blog found matching '{args.blog}'")
                print(f"Available blogs:")
                for i, b in enumerate(all_blogs, 1):
                    print(f"  {i}. {b.get('name', 'Unknown')}")
                return
    
    print(f"Found {len(blogs_to_process)} blog(s) to process\n")
    
    # Process each blog
    all_posts = []
    updated_blogs_dict = {}  # Use dict to track updated blogs by name/URL for merging
    
    for i, blog in enumerate(blogs_to_process, 1):
        blog_name = blog.get('name', 'Unknown')
        blog_url = blog.get('url', '')
        existing_feed = blog.get('rss_feed', '')
        
        print(f"[{i}/{len(blogs_to_process)}] Processing: {blog_name}")
        print(f"  URL: {blog_url}")
        
        # If RSS feed already exists, use it; otherwise try to find it
        if existing_feed:
            print(f"  ℹ️  Using existing RSS feed: {existing_feed}")
            feed_url = existing_feed
        else:
            feed_url = find_rss_feed(blog_url)
            if feed_url:
                blog['rss_feed'] = feed_url
        
        # Track URLs we've already seen from RSS feed to avoid duplicates
        # Start with existing URLs from posts.csv
        seen_urls = existing_urls.copy()
        
        # Check if we need to filter for China-related content
        filter_china = blog.get('filter_china', False)
        
        # If we have a feed URL, parse it
        if feed_url:
            posts = parse_feed(feed_url)
            # Filter for China-related posts if needed
            if filter_china:
                original_count = len(posts)
                posts = [post for post in posts if is_china_related(post.get('link', ''))]
                filtered_count = original_count - len(posts)
                if filtered_count > 0:
                    print(f"  ⏭️  Filtered out {filtered_count} non-China-related posts from RSS feed")
            
            # Filter out posts that already exist, job postings, and non-English versions
            new_posts = []
            skipped_rss_count = 0
            skipped_job_count = 0
            skipped_lang_count = 0
            for post in posts:
                url_normalized = post.get('link', '').rstrip('/')
                if url_normalized in seen_urls:
                    skipped_rss_count += 1
                    continue
                # Skip non-English versions
                if not is_english_version(url_normalized):
                    skipped_lang_count += 1
                    continue
                # Skip job postings
                if is_job_posting(post):
                    skipped_job_count += 1
                    continue
                # Add blog name and source to each post
                post['blog_name'] = blog_name
                post['source'] = blog_name
                seen_urls.add(url_normalized)
                new_posts.append(post)
            
            if skipped_rss_count > 0:
                print(f"  ⏭️  Skipped {skipped_rss_count} posts from RSS feed (already in list)")
            if skipped_lang_count > 0:
                print(f"  ⏭️  Skipped {skipped_lang_count} non-English posts from RSS feed")
            if skipped_job_count > 0:
                print(f"  ⏭️  Skipped {skipped_job_count} job postings from RSS feed")
            
            all_posts.extend(new_posts)
        else:
            print(f"  ⚠️  No RSS feed found, will try sitemap instead")
        
        # Search for sitemap.xml (whether or not we have an RSS feed)
        # If we have RSS feed, sitemap provides additional posts
        # If we don't have RSS feed, sitemap is the primary source
        existing_sitemap = blog.get('sitemap', '')
        if existing_sitemap:
            print(f"  ℹ️  Using existing sitemap: {existing_sitemap}")
            sitemap_url = existing_sitemap
        else:
            sitemap_url = find_sitemap(blog_url)
            if sitemap_url:
                blog['sitemap'] = sitemap_url
        
        # Parse sitemap and extract posts
        if sitemap_url:
            blog_only = blog.get('blog_only', True)  # Default to True for backward compatibility
            
            if feed_url:
                print(f"  📄 Parsing sitemap for additional posts...")
            else:
                print(f"  📄 Parsing sitemap for posts...")
            
            # If blog_only is True, only process post/article sitemaps
            # If blog_only is False, process all sitemaps (then filter by China if needed)
            filter_posts_only = blog_only
            if not blog_only:
                print(f"  ℹ️  Processing all sitemaps (blog_only=False)...")
            
            # Pass blog_url as base_url to include Referer header (helps bypass 403 errors)
            sitemap_urls = parse_sitemap(sitemap_url, filter_posts_only=filter_posts_only, base_url=blog_url)
            
            # Apply China filter if enabled
            if filter_china:
                print(f"  🔍 Filtering for China-related articles only...")
            
            # Filter URLs to only include blog post URLs (exclude homepage, categories, etc.)
            # This is a simple heuristic - you might want to adjust based on your blog structure
            blog_base = urlparse(blog_url).netloc
            post_urls = []
            filtered_out_count = 0
            filtered_lang_count = 0
            for url in sitemap_urls:
                url_parsed = urlparse(url)
                # Only include URLs from the same domain and exclude common non-post paths
                if (url_parsed.netloc == blog_base and 
                    not any(exclude in url.lower() for exclude in ['/category/', '/tag/', '/author/', '/page/', '/?', '/#']) and
                    url.rstrip('/') not in seen_urls):
                    # Only keep English versions
                    if not is_english_version(url):
                        filtered_lang_count += 1
                        continue
                    # If filter_china is enabled, check if URL is China-related
                    if filter_china and not is_china_related(url):
                        filtered_out_count += 1
                        continue
                    post_urls.append(url)
            
            if filtered_lang_count > 0:
                print(f"  ⏭️  Filtered out {filtered_lang_count} non-English URLs")
            if filter_china and filtered_out_count > 0:
                print(f"  ⏭️  Filtered out {filtered_out_count} non-China-related URLs")
            
            print(f"  🔍 Extracting metadata from {len(post_urls)} URLs...")
            sitemap_posts = []
            skipped_count = 0
            
            # Function to save progress periodically
            def save_progress():
                """Save current progress to posts.csv"""
                # Merge current sitemap posts with all_posts (includes RSS feed posts)
                temp_all_posts = all_posts + sitemap_posts
                if not temp_all_posts:
                    return
                
                # Read existing posts
                existing_posts = read_all_existing_posts_from_csv(posts_file)
                # Prepare posts for CSV
                posts_for_csv = []
                for p in temp_all_posts:
                    post_dict = {
                        'title': p.get('title', ''),
                        'link': p.get('link', ''),
                        'date': p.get('date', ''),
                        'tags': p.get('tags', '')
                    }
                    # Generate ID if not present
                    if not p.get('id', '').strip():
                        url = p.get('link', '').strip()
                        if url:
                            post_dict['id'] = generate_id_from_url(url)
                        else:
                            title = p.get('title', '').strip()
                            if title:
                                post_dict['id'] = f"{abs(hash(title)):012x}"
                            else:
                                post_dict['id'] = f"{abs(hash(str(len(posts_for_csv)))):012x}"
                    else:
                        post_dict['id'] = p.get('id', '').strip()
                    if 'source' in p:
                        post_dict['source'] = p['source']
                    posts_for_csv.append(post_dict)
                # Merge with existing
                merged_posts = []
                existing_urls_in_merged = set()
                for post in posts_for_csv:
                    url_normalized = post.get('link', '').rstrip('/')
                    if url_normalized and url_normalized not in existing_urls_in_merged:
                        merged_posts.append(post)
                        existing_urls_in_merged.add(url_normalized)
                for post in existing_posts:
                    url_normalized = post.get('link', '').rstrip('/')
                    if url_normalized and url_normalized not in existing_urls_in_merged:
                        # Ensure existing posts have IDs too
                        if not post.get('id', '').strip():
                            url = post.get('link', '').strip()
                            if url:
                                post['id'] = generate_id_from_url(url)
                            else:
                                title = post.get('title', '').strip()
                                if title:
                                    post['id'] = f"{abs(hash(title)):012x}"
                                else:
                                    post['id'] = f"{abs(hash(str(len(merged_posts)))):012x}"
                        merged_posts.append(post)
                        existing_urls_in_merged.add(url_normalized)
                # Write to file
                write_posts_to_csv(merged_posts, posts_file)
                print(f"     💾 Progress saved: {len(merged_posts)} total posts ({len(posts_for_csv)} new in this session)")
            
            for j, url in enumerate(post_urls, 1):
                if j % 10 == 0:
                    print(f"     Processing {j}/{len(post_urls)}... (skipped {skipped_count} already in list)")
                
                # Check if URL is already in the existing posts list
                url_normalized = url.rstrip('/')
                if url_normalized in seen_urls:
                    skipped_count += 1
                    continue
                
                post_data = extract_post_metadata_from_html(url, base_url=blog_url)
                if post_data:
                    # Skip job postings
                    if is_job_posting(post_data):
                        continue
                    post_data['blog_name'] = blog_name
                    post_data['source'] = blog_name
                    sitemap_posts.append(post_data)
                    seen_urls.add(url_normalized)
                
                # Save progress every 100 URLs
                if j % 100 == 0:
                    save_progress()
                
                # Small delay to avoid overwhelming the server
                if j < len(post_urls):
                    time.sleep(0.5)
            
            if skipped_count > 0:
                print(f"     ⏭️  Skipped {skipped_count} URLs already in posts list")
            
            if sitemap_posts:
                if feed_url:
                    print(f"  ✅ Extracted {len(sitemap_posts)} additional posts from sitemap")
                else:
                    print(f"  ✅ Extracted {len(sitemap_posts)} posts from sitemap")
                all_posts.extend(sitemap_posts)
            else:
                if feed_url:
                    print(f"  ⚠️  No additional posts extracted from sitemap")
                else:
                    print(f"  ⚠️  No posts extracted from sitemap")
        
        # Store updated blog (use URL as key for deduplication)
        blog_key = blog.get('url', '')
        updated_blogs_dict[blog_key] = blog
        print()
        
        # Add a small delay between requests
        if i < len(blogs_to_process):
            time.sleep(2)
    
    # Merge updated blogs with all blogs (preserve unprocessed blogs)
    final_blogs = []
    for blog in all_blogs:
        blog_key = blog.get('url', '')
        if blog_key in updated_blogs_dict:
            # Use the updated version
            final_blogs.append(updated_blogs_dict[blog_key])
        else:
            # Keep the original version
            final_blogs.append(blog)
    
    # Write updated blogs CSV with RSS feed URLs
    print("Writing updated blogs.csv...")
    write_blogs_to_csv(final_blogs, blogs_file)
    print(f"✅ Updated {blogs_file.name}\n")
    
    # Write posts CSV - merge new posts with existing ones
    if all_posts or existing_posts_count > 0:
        # Read all existing posts to merge with new ones
        existing_posts = read_all_existing_posts_from_csv(posts_file)
        
        # Create a set of URLs from new posts for deduplication
        new_post_urls = {p['link'].rstrip('/') for p in all_posts if p.get('link')}
        
        # Prepare new posts for CSV (remove blog_name, keep source, add ID)
        posts_for_csv = []
        for p in all_posts:
            post_dict = {
                'title': p.get('title', ''),
                'link': p.get('link', ''),
                'date': p.get('date', ''),
                'tags': p.get('tags', '')
            }
            # Generate ID if not present
            if not p.get('id', '').strip():
                url = p.get('link', '').strip()
                if url:
                    post_dict['id'] = generate_id_from_url(url)
                else:
                    title = p.get('title', '').strip()
                    if title:
                        post_dict['id'] = f"{abs(hash(title)):012x}"
                    else:
                        post_dict['id'] = f"{abs(hash(str(len(posts_for_csv)))):012x}"
            else:
                post_dict['id'] = p.get('id', '').strip()
            # Add source field if it exists in the post (should always exist for new posts)
            if 'source' in p:
                post_dict['source'] = p['source']
            posts_for_csv.append(post_dict)
        
        # Merge: keep existing posts that aren't in new posts, add all new posts
        merged_posts = []
        existing_urls_in_merged = set()
        
        # First, add all new posts
        for post in posts_for_csv:
            url_normalized = post.get('link', '').rstrip('/')
            if url_normalized and url_normalized not in existing_urls_in_merged:
                merged_posts.append(post)
                existing_urls_in_merged.add(url_normalized)
        
        # Then, add existing posts that aren't duplicates
        for post in existing_posts:
            url_normalized = post.get('link', '').rstrip('/')
            if url_normalized and url_normalized not in existing_urls_in_merged:
                # Ensure existing posts have IDs too
                if not post.get('id', '').strip():
                    url = post.get('link', '').strip()
                    if url:
                        post['id'] = generate_id_from_url(url)
                    else:
                        title = post.get('title', '').strip()
                        if title:
                            post['id'] = f"{abs(hash(title)):012x}"
                        else:
                            post['id'] = f"{abs(hash(str(len(merged_posts)))):012x}"
                merged_posts.append(post)
                existing_urls_in_merged.add(url_normalized)
        
        print(f"Writing {len(merged_posts)} posts to {posts_file.name}...")
        print(f"  ({len(posts_for_csv)} new, {len(merged_posts) - len(posts_for_csv)} existing)")
        write_posts_to_csv(merged_posts, posts_file)
        print(f"✅ Updated {posts_file.name}")
    else:
        print("⚠️  No posts found to write")
    
    # Print summary
    print("\n" + "=" * 50)
    print(f"📊 Summary:")
    print(f"  Total blogs in CSV: {len(all_blogs)}")
    print(f"  Blogs processed: {len(blogs_to_process)}")
    feeds_found = sum(1 for b in updated_blogs_dict.values() if b.get('rss_feed'))
    print(f"  RSS feeds found: {feeds_found}")
    print(f"  Total posts extracted: {len(all_posts)}")
    print("=" * 50)


if __name__ == '__main__':
    main()

