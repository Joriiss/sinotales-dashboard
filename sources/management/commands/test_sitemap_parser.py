#!/usr/bin/env python3
"""
Management command to test parsing a sitemap and extracting blog posts.
Takes a sitemap URL as input and displays all posts found.
"""

from django.core.management.base import BaseCommand
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
import time
import subprocess
import os
import re
import json
from pathlib import Path
from django.conf import settings
from sources.models import Source, Content
from typing import Dict, Optional, Tuple


class Command(BaseCommand):
    help = 'Test parsing a sitemap URL and extract blog posts'

    def add_arguments(self, parser):
        parser.add_argument(
            'sitemap_url',
            type=str,
            help='URL of the sitemap to parse'
        )
        parser.add_argument(
            '--base-url',
            type=str,
            default=None,
            help='Base URL for Referer header (helps bypass 403 errors)'
        )
        parser.add_argument(
            '--use-proxy',
            action='store_true',
            help='Use Webshare proxies for requests (helps bypass Cloudflare)'
        )
        parser.add_argument(
            '--source',
            type=str,
            default=None,
            help='Source name or ID to check for existing posts (only show new posts)'
        )
        parser.add_argument(
            '--filter-china',
            action='store_true',
            help='Filter to only show China-related posts (overrides source setting)'
        )
        parser.add_argument(
            '--no-filter-china',
            action='store_true',
            help='Disable China filter even if source has it enabled'
        )
        parser.add_argument(
            '--use-ollama',
            action='store_true',
            help='Use Ollama AI to filter posts (job postings, non-travel content, China relevance)'
        )
        parser.add_argument(
            '--ollama-model',
            type=str,
            default=None,
            help='Ollama model to use (default: from settings or llama3.2:latest)'
        )

    def has_language_code(self, url):
        """
        Check if a URL contains a language code in the path (e.g., /es/, /pt/, /ja/).
        Returns True if the URL contains a language code, False otherwise.
        English URLs typically don't have a language code.
        """
        parsed = urlparse(url)
        path = parsed.path.lower()
        
        # Common language codes (ISO 639-1 two-letter codes)
        # These appear in URLs like /es/page, /pt/page, /ja/page, etc.
        language_codes = [
            '/es/', '/pt/', '/ja/', '/ko/', '/de/', '/fr/', '/it/', '/ru/', '/zh/',
            '/ar/', '/hi/', '/nl/', '/sv/', '/pl/', '/tr/', '/vi/', '/th/', '/id/',
            '/cs/', '/hu/', '/ro/', '/fi/', '/da/', '/no/', '/he/', '/uk/', '/el/',
            '/bg/', '/hr/', '/sk/', '/sl/', '/et/', '/lv/', '/lt/', '/mt/', '/ga/',
            '/cy/', '/is/', '/mk/', '/sq/', '/sr/', '/bs/', '/ca/', '/eu/', '/gl/',
            # Also check for language codes at the start of path (without leading slash)
            'es/', 'pt/', 'ja/', 'ko/', 'de/', 'fr/', 'it/', 'ru/', 'zh/',
            'ar/', 'hi/', 'nl/', 'sv/', 'pl/', 'tr/', 'vi/', 'th/', 'id/',
        ]
        
        # Check if any language code appears in the path
        for code in language_codes:
            if code in path:
                return True
        
        return False

    def is_china_related(self, url):
        """
        Check if a URL is related to China based on keywords in the URL.
        Based on the logic from get_posts_list.py
        """
        url_lower = url.lower()
        
        # Exclude URLs that are clearly about other countries
        exclude_countries = [
            '/usa/', '/united-states/', '/america/', '/american/',
            '/australia/', '/canada/', '/uk/', '/united-kingdom/', '/britain/',
            '/france/', '/germany/', '/italy/', '/spain/', '/japan/', '/korea/',
            '/thailand/', '/vietnam/', '/singapore/', '/malaysia/', '/indonesia/',
            '/philippines/', '/india/', '/brazil/', '/mexico/', '/argentina/',
            '/new-zealand/', '/south-africa/', '/egypt/', '/turkey/', '/greece/'
        ]
        
        for country in exclude_countries:
            if country in url_lower:
                if '/china/' not in url_lower:
                    return False
        
        # Strong indicators
        strong_indicators = [
            '/china/', '/taiwan/', '/taipei/',
            '/hong-kong/', '/hongkong/', '/macau/', '/macao/',
        ]
        
        for indicator in strong_indicators:
            if indicator in url_lower:
                return True
        
        # China-related keywords
        china_keywords = [
            'beijing', 'peking', 'shanghai', 'guangzhou', 'canton', 'shenzhen', 
            'chengdu', 'xian', 'xi\'an', 'hangzhou', 'nanjing', 'wuhan', 
            'chongqing', 'tianjin', 'suzhou', 'dalian', 'qingdao', 'xiamen',
            'foshan', 'dongguan', 'zhengzhou', 'changsha', 'kunming', 'fuzhou',
            'wuxi', 'hefei', 'nanning', 'shijiazhuang', 'haerbin', 'harbin',
            'jinan', 'taiyuan', 'changchun', 'nanchang', 'guiyang', 'lanzhou',
            'guangdong', 'jiangsu', 'shandong', 'zhejiang', 'henan', 'sichuan',
            'hubei', 'hunan', 'anhui', 'hebei', 'jiangxi', 'shanxi', 'liaoning',
            'fujian', 'yunnan', 'guangxi', 'heilongjiang', 'jilin', 'shaanxi',
            'guizhou', 'xinjiang', 'tibet', 'qinghai', 'gansu', 'inner-mongolia',
            'ningxia', 'yangtze', 'yellow-river', 'pearl-river', 'tibetan',
            'manchuria', 'dongbei', 'northeast-china',
            'great-wall', 'terracotta', 'forbidden-city', 'panda', 'silk-road'
        ]
        
        for keyword in china_keywords:
            if keyword in url_lower:
                return True
        
        if 'china' in url_lower or 'chinese' in url_lower:
            if 'chinatown' in url_lower:
                if any(indicator in url_lower for indicator in strong_indicators):
                    return True
                if any(city in url_lower for city in ['beijing', 'shanghai', 'guangzhou', 'chengdu', 'xian']):
                    return True
                return False
            return True
        
        return False

    def is_job_posting(self, post):
        """
        Check if a post is a job posting based on title and tags.
        Based on the logic from get_posts_list.py
        """
        title = post.get('title', '').lower()
        tags = post.get('tags', '').lower() if post.get('tags') else ''
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

    def is_non_travel_content(self, post):
        """
        Check if a post is non-travel content (legal pages, business pages, etc.).
        Based on the logic from get_posts_list.py
        """
        title = post.get('title', '').lower()
        link = post.get('url', post.get('link', '')).lower()
        tags = post.get('tags', '').lower() if post.get('tags') else ''
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

    def _filter_post_with_ollama(
        self, 
        post: Dict, 
        model: str, 
        check_china: bool = False
    ) -> Tuple[bool, bool, bool, Optional[str]]:
        """
        Use Ollama to filter a post based on multiple criteria.
        
        Args:
            post: Post dict with 'title', 'url'/'link', and optionally 'tags'
            model: Ollama model name
            check_china: Whether to check China relevance
            
        Returns:
            Tuple of (is_job_posting: bool, is_non_travel: bool, is_china_related: bool, reasoning: str or None)
        """
        try:
            import requests
        except ImportError:
            raise ImportError("requests library required for Ollama. Install with: pip install requests")
        
        title = post.get('title', '')
        url = post.get('url', post.get('link', ''))
        tags = post.get('tags', '')
        
        # Prepare the content for analysis
        tags_str = tags if isinstance(tags, str) else ', '.join(tags) if tags else 'None'
        
        # Create prompt
        prompt = f"""Analyze the following blog post and determine:
1. Is this a job posting or job advertisement?
2. Is this non-travel content (legal pages, business pages, company announcements, etc.)?
{f"3. Is this content related to China, Chinese culture, Chinese geography, or Chinese topics?" if check_china else ""}

Title: {title}
URL: {url}
Tags: {tags_str}

Instructions:
- A job posting includes: job openings, hiring announcements, career opportunities, internships, positions available, etc.
- Non-travel content includes: legal pages (terms, privacy, disclaimer), business pages (about us, contact, partners), company announcements (awards, partnerships) that are NOT about travel destinations or experiences
- Keep travel-related company news (e.g., "New tour in Yunnan", "Award for best China travel guide")
- {"China-related content includes: Chinese cities, provinces, culture, history, geography, food, traditions, travel in China, etc. Exclude Chinatowns in other countries unless they're specifically about China." if check_china else ""}

Respond in the following JSON format:
{{
    "is_job_posting": true or false,
    "is_non_travel": true or false,
    {"\"is_china_related\": true or false," if check_china else ""}
    "reasoning": "Brief explanation for each determination (2-3 sentences)"
}}

Response:"""

        # Call Ollama
        ollama_url = getattr(settings, 'OLLAMA_URL', 'http://localhost:11434')
        url_api = f"{ollama_url}/api/generate"
        
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,  # Lower temperature for more consistent results
                "top_p": 0.9,
            }
        }
        
        try:
            response = requests.post(url_api, json=payload, timeout=30)
            response.raise_for_status()
            result = response.json()
            response_text = result.get('response', '').strip()
            
            # Parse JSON response
            # Try to extract JSON from response (in case there's extra text)
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}')
            
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx + 1]
                try:
                    parsed = json.loads(json_str)
                    is_job = bool(parsed.get('is_job_posting', False))
                    is_non_travel = bool(parsed.get('is_non_travel', False))
                    is_china = bool(parsed.get('is_china_related', True)) if check_china else True
                    reasoning = parsed.get('reasoning', 'No reasoning provided')
                    return is_job, is_non_travel, is_china, reasoning
                except json.JSONDecodeError:
                    pass  # Fall through to try parsing whole response
            
            # Fallback: try to parse the whole response as JSON
            try:
                parsed = json.loads(response_text)
                is_job = bool(parsed.get('is_job_posting', False))
                is_non_travel = bool(parsed.get('is_non_travel', False))
                is_china = bool(parsed.get('is_china_related', True)) if check_china else True
                reasoning = parsed.get('reasoning', 'No reasoning provided')
                return is_job, is_non_travel, is_china, reasoning
            except json.JSONDecodeError:
                pass  # Will be caught by outer exception handler
                
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Could not connect to Ollama at {ollama_url}. "
                "Make sure Ollama is running: https://ollama.ai"
            )
        except json.JSONDecodeError as e:
            # If JSON parsing fails, try to infer from response text
            response_lower = response_text.lower()
            is_job = 'job' in response_lower and ('true' in response_lower or 'yes' in response_lower)
            is_non_travel = 'non-travel' in response_lower or 'non travel' in response_lower
            is_china = 'china' in response_lower and ('true' in response_lower or 'yes' in response_lower) if check_china else True
            return is_job, is_non_travel, is_china, f"Parsed from response (JSON parse failed): {response_text[:200]}"
        except Exception as e:
            raise Exception(f"Ollama API error: {str(e)}")
        
        # If we get here, parsing failed completely
        return False, False, True if not check_china else False, "Failed to parse Ollama response"

    def handle(self, *args, **options):
        sitemap_url = options['sitemap_url']
        base_url = options.get('base_url')
        use_proxy = options.get('use_proxy', False)
        source_arg = options.get('source')
        filter_china_flag = options.get('filter_china', False)
        no_filter_china_flag = options.get('no_filter_china', False)
        use_ollama = options.get('use_ollama', False)
        ollama_model = options.get('ollama_model')
        
        # Get Ollama model if using Ollama
        if use_ollama:
            if not ollama_model:
                # Try to get from settings
                try:
                    from sources.models import Settings
                    app_settings = Settings.get_settings()
                    ollama_model = app_settings.default_filtering_model
                except:
                    pass
                if not ollama_model:
                    ollama_model = 'gpt-oss:20b-cloud'
            self.stdout.write(self.style.SUCCESS(f'Using Ollama model: {ollama_model}'))
        
        # Load proxy configuration if requested
        proxies = None
        if use_proxy:
            proxies = self._load_proxy_config()
            if proxies:
                self.stdout.write(self.style.SUCCESS('✅ Proxy configuration loaded'))
            else:
                self.stdout.write(self.style.WARNING('⚠️  Proxy requested but configuration not found'))
        
        self.stdout.write(self.style.SUCCESS(f'\n{"="*60}'))
        self.stdout.write(self.style.SUCCESS(f'Testing Sitemap Parser'))
        self.stdout.write(self.style.SUCCESS(f'{"="*60}\n'))
        self.stdout.write(f'Sitemap URL: {sitemap_url}\n')
        if proxies:
            self.stdout.write(f'Using proxy: Yes\n')
        if source_arg:
            self.stdout.write(f'Source filter: {source_arg}\n')
        if use_ollama:
            self.stdout.write(f'Filtering method: Ollama AI\n')
        else:
            self.stdout.write(f'Filtering method: Keyword-based\n')
        
        # Parse the sitemap
        posts = self.parse_sitemap(sitemap_url, base_url=base_url, proxies=proxies)
        
        original_count = len(posts)
        
        # Check for existing posts if source is provided
        existing_external_ids = set()
        existing_urls_normalized = set()
        existing_count = 0
        source = None
        filter_china = False  # Will be determined from source or flag
        
        if source_arg:
            try:
                # Try to parse as ID first
                try:
                    source_id = int(source_arg)
                    source = Source.objects.get(pk=source_id)
                except ValueError:
                    # Not a number, try as name
                    source = Source.objects.filter(name__icontains=source_arg).first()
                
                if source:
                    # Check source's filter_china setting (unless overridden by flags)
                    if no_filter_china_flag:
                        filter_china = False
                        self.stdout.write(f'Found source: {source.name} (ID: {source.id})')
                        self.stdout.write(f'  Source has filter_china={source.filter_china}, but --no-filter-china flag overrides it\n')
                    elif filter_china_flag:
                        filter_china = True
                        self.stdout.write(f'Found source: {source.name} (ID: {source.id})')
                        self.stdout.write(f'  Source has filter_china={source.filter_china}, but --filter-china flag forces it enabled\n')
                    else:
                        filter_china = source.filter_china
                        self.stdout.write(f'Found source: {source.name} (ID: {source.id})')
                        if filter_china:
                            self.stdout.write(f'  Source has filter_china=True, China filter will be applied\n')
                    
                    # Get both external_id and link to check for existing posts
                    # (some posts might have URL in external_id, others in link)
                    existing_external_ids_raw = set(
                        Content.objects.filter(source=source)
                        .exclude(external_id__isnull=True)
                        .exclude(external_id='')
                        .values_list('external_id', flat=True)
                    )
                    existing_links_raw = set(
                        Content.objects.filter(source=source)
                        .exclude(link__isnull=True)
                        .exclude(link='')
                        .values_list('link', flat=True)
                    )
                    # Combine both sets (URLs might be in either field)
                    existing_urls_raw = existing_external_ids_raw | existing_links_raw
                    # Count actual Content objects, not unique URLs
                    existing_count = Content.objects.filter(source=source).count()
                    # Normalize existing URLs (remove trailing slashes for comparison)
                    existing_urls_normalized = {url.rstrip('/') for url in existing_urls_raw if url}
                    self.stdout.write(f'Existing posts in database: {existing_count}')
                    self.stdout.write(f'  (checked both external_id and link fields for {len(existing_urls_normalized)} unique URLs)\n')
                else:
                    self.stdout.write(self.style.WARNING(f'⚠️  Source not found: {source_arg}'))
                    self.stdout.write(self.style.WARNING('⚠️  Will show all posts (not filtering by existing)\n'))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'⚠️  Error looking up source: {str(e)}\n'))
        else:
            # No source provided, use flag if set
            if filter_china_flag:
                filter_china = True
                self.stdout.write(f'China filter: Enabled (via --filter-china flag)\n')
        
        # Filter out existing posts
        filtered_existing = 0
        if existing_urls_normalized:
            posts_before_existing = len(posts)
            # Check against normalized URLs
            posts = [post for post in posts if post['url'].rstrip('/') not in existing_urls_normalized]
            filtered_existing = posts_before_existing - len(posts)
            if filtered_existing > 0:
                self.stdout.write(self.style.WARNING(f'⏭️  Filtered out {filtered_existing} existing posts\n'))
        
        # Step 1: If filter_china is enabled, apply keyword-based URL filter first
        filtered_china_keyword = 0
        if filter_china:
            posts_before_china_keyword = len(posts)
            filtered_posts_china = []
            for post in posts:
                post_url = post.get('url', '')
                post_title = post.get('title', '')
                # Check URL first (most reliable), then title
                if self.is_china_related(post_url) or (post_title and self.is_china_related(post_title)):
                    filtered_posts_china.append(post)
            posts = filtered_posts_china
            filtered_china_keyword = posts_before_china_keyword - len(posts)
            if filtered_china_keyword > 0:
                self.stdout.write(self.style.WARNING(f'⏭️  Filtered out {filtered_china_keyword} non-China-related posts (keyword-based URL filter)\n'))
        
        # Step 2: Apply Ollama filtering (always, if enabled) for job postings, non-travel content, and China relevance
        filtered_jobs = 0
        filtered_non_travel = 0
        filtered_china_ollama = 0
        posts_before_content_filter = len(posts)
        filtered_posts = []
        filtered_out_posts = []  # Store filtered posts with reasons
        ollama_errors = 0
        
        if use_ollama:
            # Use Ollama for filtering
            self.stdout.write(f'\n🔍 Filtering posts with Ollama AI...\n')
            total_posts = len(posts)
            for i, post in enumerate(posts, 1):
                post_title = post.get('title', '')[:60]  # Truncate for display
                self.stdout.write(f'  [{i}/{total_posts}] Analyzing: {post_title}...', ending='')
                self.stdout.flush()
                
                try:
                    # Always check China relevance with Ollama if filter_china is enabled
                    # (even though we already did keyword filtering, Ollama can be more nuanced)
                    is_job, is_non_travel, is_china_related_ollama, reasoning = self._filter_post_with_ollama(
                        post, ollama_model, check_china=filter_china
                    )
                    
                    # Store filtering results
                    post['_ollama_is_job'] = is_job
                    post['_ollama_is_non_travel'] = is_non_travel
                    post['_ollama_is_china_related'] = is_china_related_ollama
                    post['_ollama_reasoning'] = reasoning
                    
                    # Determine filter reason
                    filter_reason = None
                    if is_job:
                        filtered_jobs += 1
                        filter_reason = "Job Posting"
                    elif is_non_travel:
                        filtered_non_travel += 1
                        filter_reason = "Non-Travel Content"
                    elif filter_china and not is_china_related_ollama:
                        filtered_china_ollama += 1
                        filter_reason = "Not China-Related"
                    
                    if filter_reason:
                        # Store filtered post with reason
                        post['_filter_reason'] = filter_reason
                        filtered_out_posts.append(post)
                        self.stdout.write(self.style.WARNING(f' ❌ FILTERED ({filter_reason})'))
                    else:
                        # Post passed all filters
                        filtered_posts.append(post)
                        self.stdout.write(self.style.SUCCESS(' ✓ PASSED'))
                    
                except Exception as e:
                    ollama_errors += 1
                    # Fall back to keyword-based filtering on error
                    self.stdout.write(self.style.WARNING(f' ⚠️  ERROR: {str(e)[:50]}'))
                    # Use keyword-based as fallback
                    filter_reason = None
                    if self.is_job_posting(post):
                        filtered_jobs += 1
                        filter_reason = "Job Posting (keyword-based fallback)"
                    elif self.is_non_travel_content(post):
                        filtered_non_travel += 1
                        filter_reason = "Non-Travel Content (keyword-based fallback)"
                    # If filter_china is enabled, we already filtered by keyword, so keep the post
                    # (unless Ollama explicitly says it's not China-related, but we can't know that here)
                    
                    if filter_reason:
                        post['_filter_reason'] = filter_reason
                        post['_ollama_reasoning'] = f"Ollama error: {str(e)[:200]}"
                        filtered_out_posts.append(post)
                    else:
                        filtered_posts.append(post)
            
            if ollama_errors > 0:
                self.stdout.write(self.style.WARNING(f'⚠️  {ollama_errors} posts processed with keyword-based fallback due to Ollama errors\n'))
        else:
            # Use keyword-based filtering (only for job postings and non-travel content)
            # China filter already applied above with keyword-based URL filter
            for post in posts:
                # Skip job postings
                if self.is_job_posting(post):
                    filtered_jobs += 1
                    continue
                # Skip non-travel content
                if self.is_non_travel_content(post):
                    filtered_non_travel += 1
                    continue
                filtered_posts.append(post)
        
        posts = filtered_posts
        
        # Display filtered out posts first (if using Ollama)
        if use_ollama and filtered_out_posts:
            self.stdout.write(self.style.WARNING(f'\n{"="*60}'))
            self.stdout.write(self.style.WARNING(f'❌ FILTERED OUT ({len(filtered_out_posts)} posts):\n'))
            self.stdout.write(self.style.WARNING(f'{"="*60}\n'))
            
            # Group by filter reason
            by_reason = {}
            for post in filtered_out_posts:
                reason = post.get('_filter_reason', 'Unknown')
                if reason not in by_reason:
                    by_reason[reason] = []
                by_reason[reason].append(post)
            
            # Display each group
            for reason, reason_posts in by_reason.items():
                self.stdout.write(self.style.WARNING(f'\n🔴 {reason} ({len(reason_posts)} posts):\n'))
                for i, post in enumerate(reason_posts, 1):
                    self.stdout.write(f'  {i}. {post["title"]}')
                    self.stdout.write(f'     URL: {post["url"]}')
                    if post.get('date'):
                        self.stdout.write(f'     Date: {post["date"]}')
                    # Show Ollama reasoning
                    reasoning = post.get('_ollama_reasoning', '')
                    if reasoning:
                        # Show full reasoning for filtered posts
                        self.stdout.write(self.style.WARNING(f'     Reason: {reasoning}'))
                    self.stdout.write('')
        
        # Display summary of filtered posts
        if not use_ollama:
            if filtered_jobs > 0:
                self.stdout.write(self.style.WARNING(f'⏭️  Filtered out {filtered_jobs} job postings (keyword-based)\n'))
            if filtered_non_travel > 0:
                self.stdout.write(self.style.WARNING(f'⏭️  Filtered out {filtered_non_travel} non-travel content posts (keyword-based)\n'))
        
        # Display results that passed filters
        if posts:
            self.stdout.write(self.style.SUCCESS(f'\n{"="*60}'))
            self.stdout.write(self.style.SUCCESS(f'✅ PASSED FILTERS ({len(posts)} posts):\n'))
            self.stdout.write(self.style.SUCCESS(f'{"="*60}\n'))
            for i, post in enumerate(posts, 1):
                self.stdout.write(f'{i}. {post["title"]}')
                self.stdout.write(f'   URL: {post["url"]}')
                if post.get('date'):
                    self.stdout.write(f'   Date: {post["date"]}')
                # Show filter status
                if use_ollama and post.get('_ollama_reasoning'):
                    # Show Ollama reasoning
                    reasoning = post.get('_ollama_reasoning', '')
                    if reasoning:
                        self.stdout.write(self.style.SUCCESS(f'   ✓ Passed: {reasoning}'))
                elif filter_china:
                    is_china = self.is_china_related(post.get('url', '')) or self.is_china_related(post.get('title', ''))
                    if is_china:
                        self.stdout.write(self.style.SUCCESS('   ✓ China-related'))
                    else:
                        self.stdout.write(self.style.WARNING('   ✗ Not China-related'))
                self.stdout.write('')
        else:
            self.stdout.write(self.style.WARNING('\n⚠️  No new posts found'))
            if existing_urls_normalized:
                self.stdout.write(self.style.WARNING('   (All posts already exist in database)'))
            if filter_china:
                self.stdout.write(self.style.WARNING('   (All posts filtered out by China filter)'))
        
        # Summary
        self.stdout.write(self.style.SUCCESS(f'\n{"="*60}'))
        self.stdout.write(f'Summary:')
        self.stdout.write(f'  Total posts in sitemap: {original_count}')
        if use_ollama:
            self.stdout.write(f'  Filtering method: Ollama AI ({ollama_model})')
            if ollama_errors > 0:
                self.stdout.write(f'    ⚠️  {ollama_errors} posts used keyword-based fallback')
        else:
            self.stdout.write(f'  Filtering method: Keyword-based')
        if existing_urls_normalized:
            # Calculate new posts count (before content filters)
            new_posts_count = original_count - filtered_existing
            self.stdout.write(f'  Existing posts in database: {existing_count}')
            self.stdout.write(f'  New posts (not in database): {new_posts_count}')
            if filtered_existing > 0:
                self.stdout.write(f'    → Filtered out {filtered_existing} existing posts')
        else:
            self.stdout.write(f'  New posts: {len(posts)}')
        if filter_china and filtered_china_keyword > 0:
            self.stdout.write(f'  China filter (keyword-based URL): Enabled')
            self.stdout.write(f'    → Filtered out {filtered_china_keyword} non-China-related posts')
        if filtered_jobs > 0 or filtered_non_travel > 0:
            self.stdout.write(f'  Content filters applied:')
            if filtered_jobs > 0:
                self.stdout.write(f'    → Filtered out {filtered_jobs} job postings')
            if filtered_non_travel > 0:
                self.stdout.write(f'    → Filtered out {filtered_non_travel} non-travel content posts')
        if filter_china and filtered_china_ollama > 0:
            self.stdout.write(f'  China filter (Ollama refinement):')
            self.stdout.write(f'    → Filtered out {filtered_china_ollama} non-China-related posts')
        self.stdout.write(f'  Final result: {len(posts)} posts to import')
        self.stdout.write(self.style.SUCCESS(f'{"="*60}\n'))

    def get_request_headers(self, base_url=None, minimal=False):
        """Get request headers with optional Referer header"""
        if minimal:
            # Minimal headers - some sites block requests with too many headers
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/xml, text/xml, */*',
            }
        else:
            headers = {
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
        
        if base_url:
            parsed = urlparse(base_url)
            headers['Referer'] = f"{parsed.scheme}://{parsed.netloc}/"
            if not minimal:
                headers['Origin'] = f"{parsed.scheme}://{parsed.netloc}"
        
        return headers

    def _load_proxy_config(self):
        """
        Load Webshare proxy configuration and fetch proxy list.
        
        Returns:
            Dict with 'http' and 'https' proxy URLs for requests library, or None
        """
        # Try to get from environment variables first
        # Check for API token first (Webshare API v2 uses token auth)
        api_token = os.environ.get('WEBSHARE_API_TOKEN', '').strip()
        proxy_username = os.environ.get('WEBSHARE_PROXY_USERNAME', '').strip()
        proxy_password = os.environ.get('WEBSHARE_PROXY_PASSWORD', '').strip()
        
        # If not in environment, try to load from .env file
        if not api_token and (not proxy_username or not proxy_password):
            base_dir = Path(settings.BASE_DIR)
            env_file = base_dir / '.env'
            
            if env_file.exists():
                try:
                    with open(env_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith('#'):
                                continue
                            
                            if '=' in line:
                                key, value = line.split('=', 1)
                                key = key.strip()
                                value = value.strip()
                                
                                # Remove quotes if present
                                if value.startswith('"') and value.endswith('"'):
                                    value = value[1:-1]
                                elif value.startswith("'") and value.endswith("'"):
                                    value = value[1:-1]
                                
                                if key == 'WEBSHARE_API_TOKEN':
                                    api_token = value
                                elif key == 'WEBSHARE_PROXY_USERNAME':
                                    proxy_username = value
                                elif key == 'WEBSHARE_PROXY_PASSWORD':
                                    proxy_password = value
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f'  ⚠️  Could not read .env file: {str(e)}'))
        
        # Debug: Show what credentials we found (without revealing values)
        self.stdout.write(f'  ℹ️  Credentials check: api_token={"set" if api_token else "not set"}, username={"set" if proxy_username else "not set"}, password={"set" if proxy_password else "not set"}')
        
        # Use API token if available, otherwise use username/password
        if not api_token and (not proxy_username or not proxy_password):
            self.stdout.write(self.style.WARNING('  ⚠️  Webshare credentials not found (need WEBSHARE_API_TOKEN or WEBSHARE_PROXY_USERNAME/PASSWORD)'))
            self.stdout.write('  ℹ️  Make sure your .env file contains one of:')
            self.stdout.write('      - WEBSHARE_API_TOKEN=your_token_here')
            self.stdout.write('      - OR WEBSHARE_PROXY_USERNAME=your_username AND WEBSHARE_PROXY_PASSWORD=your_password')
            return None
        
        # Use API token if available, otherwise username will be used as token
        token_to_use = api_token if api_token else proxy_username
        
        if not token_to_use or not token_to_use.strip():
            self.stdout.write(self.style.WARNING('  ⚠️  Token is empty or whitespace only'))
            return None
        
        # Debug: Show what we're using (without revealing the actual value)
        if api_token:
            self.stdout.write(f'  ℹ️  Using WEBSHARE_API_TOKEN (length: {len(token_to_use)})')
        elif proxy_username:
            self.stdout.write(f'  ℹ️  Using WEBSHARE_PROXY_USERNAME as token (length: {len(token_to_use)})')
        
        # Fetch proxy list from Webshare API
        try:
            self.stdout.write('  Fetching proxy list from Webshare API...')
            api_url = 'https://proxy.webshare.io/api/v2/proxy/list/'
            
            # Webshare API v2 uses token-based authentication
            # Use API token if available, otherwise use username as token
            headers = {
                'Authorization': f'Token {token_to_use}'
            }
            
            # Disable SSL warnings for cleaner output
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            # Try backbone mode first (as shown in working example), then fallback to other modes
            modes_to_try = ['backbone', None, 'backconnect', 'datacenter', 'direct']
            response = None
            
            for mode in modes_to_try:
                params = {
                    'page': 1,
                    'page_size': 25,  # Fetch multiple proxies
                }
                if mode:
                    params['mode'] = mode
                    self.stdout.write(f'  Trying mode: {mode}...')
                else:
                    self.stdout.write('  Trying without mode parameter (for residential proxies)...')
                
                # Try with SSL verification first
                try:
                    test_response = requests.get(api_url, headers=headers, params=params, timeout=10, verify=True)
                except requests.exceptions.SSLError as ssl_error:
                    # If SSL verification fails, try without verification
                    test_response = requests.get(api_url, headers=headers, params=params, timeout=10, verify=False)
                
                if test_response.status_code == 200:
                    response = test_response
                    if mode:
                        self.stdout.write(f'  ✅ Success with mode: {mode}')
                    else:
                        self.stdout.write('  ✅ Success (residential proxies)')
                    break
                elif test_response.status_code == 400:
                    # Try next mode
                    continue
                else:
                    # Other error, try next mode
                    continue
            
            # If all modes failed and we have username/password, try basic auth as fallback
            # (Only if we're not using an API token, since API tokens don't work with basic auth)
            if (not response or (hasattr(response, 'status_code') and response.status_code != 200)) and not api_token and proxy_username and proxy_password:
                self.stdout.write('  Token auth failed, trying basic auth...')
                params = {'page': 1, 'page_size': 25}  # Try without mode for basic auth
                try:
                    auth = (proxy_username, proxy_password)
                    response = requests.get(api_url, auth=auth, params=params, timeout=10, verify=False)
                except requests.exceptions.SSLError:
                    pass
            
            if response and hasattr(response, 'status_code') and response.status_code == 200:
                data = response.json()
                results = data.get('results', [])
                
                if results:
                    import random
                    # Select a random proxy from the list for better distribution
                    proxy = random.choice(results)
                    proxy_address = proxy.get('proxy_address')
                    port = proxy.get('port')
                    username = proxy.get('username')
                    password = proxy.get('password')
                    
                    # For backbone proxies, proxy_address can be null, use p.webshare.io as default
                    if not proxy_address:
                        proxy_address = 'p.webshare.io'
                    
                    if proxy_address and port and username and password:
                        # Format proxy URL for requests library
                        proxy_url = f'http://{username}:{password}@{proxy_address}:{port}'
                        proxies = {
                            'http': proxy_url,
                            'https': proxy_url
                        }
                        self.stdout.write(self.style.SUCCESS(f'  ✅ Loaded proxy: {proxy_address}:{port} (selected from {len(results)} available)'))
                        return proxies
                    else:
                        self.stdout.write(self.style.WARNING('  ⚠️  Proxy data incomplete'))
                else:
                    self.stdout.write(self.style.WARNING('  ⚠️  No proxies found in Webshare account'))
            else:
                self.stdout.write(self.style.WARNING(f'  ⚠️  Failed to fetch proxy list: HTTP {response.status_code}'))
                if response.status_code == 401:
                    self.stdout.write('  ℹ️  Authentication failed - check your Webshare API token')
                    self.stdout.write('  ℹ️  Make sure you have WEBSHARE_API_TOKEN or WEBSHARE_PROXY_USERNAME set correctly')
                    try:
                        error_detail = response.json()
                        if 'detail' in error_detail:
                            self.stdout.write(f'  ℹ️  API response: {error_detail["detail"]}')
                    except:
                        pass
                elif response.status_code == 403:
                    self.stdout.write('  ℹ️  Access forbidden - check your API token permissions')
                else:
                    try:
                        error_detail = response.text[:200]
                        self.stdout.write(f'  ℹ️  Response: {error_detail}')
                    except:
                        pass
        except requests.exceptions.SSLError as ssl_error:
            self.stdout.write(self.style.WARNING(f'  ⚠️  SSL error: {str(ssl_error)}'))
            self.stdout.write('  ℹ️  This might be due to network/firewall SSL interception')
        except requests.exceptions.ConnectionError as conn_error:
            self.stdout.write(self.style.WARNING(f'  ⚠️  Connection error: {str(conn_error)}'))
            self.stdout.write('  ℹ️  Check your internet connection and firewall settings')
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'  ⚠️  Error fetching proxy list: {str(e)}'))
            import traceback
            self.stdout.write(f'  ℹ️  Traceback: {traceback.format_exc()[:300]}')
        
        return None

    def is_valid_sitemap(self, content, content_type=None):
        """Check if content is a valid sitemap XML"""
        if content_type and 'html' in content_type.lower():
            return False
        
        try:
            content_str = content.decode('utf-8', errors='ignore') if isinstance(content, bytes) else str(content)
            has_sitemap_tags = any(tag in content_str.lower() for tag in ['<urlset', '<sitemapindex', '<url>', '<sitemap>'])
            return has_sitemap_tags
        except Exception:
            return False

    def is_post_sitemap(self, sitemap_url):
        """
        Check if a sitemap URL is for blog posts.
        Based on the logic from get_posts_list.py
        """
        sitemap_lower = sitemap_url.lower()
        
        # Process sitemaps that contain "post-sitemap", "article", or "articles" in the URL
        # Skip page-sitemap, guide-sitemap, category-sitemap, etc.
        if ('post-sitemap' in sitemap_lower or 
            'article' in sitemap_lower):
            return True
        
        return False

    def parse_sitemap(self, sitemap_url, base_url=None, proxies=None):
        """
        Parse sitemap and extract post URLs with dates.
        
        Returns:
            List of dicts with 'url', 'title', and 'date' keys
        """
        posts = []
        
        try:
            # Extract base URL from sitemap if not provided
            if not base_url:
                parsed = urlparse(sitemap_url)
                base_url = f"{parsed.scheme}://{parsed.netloc}"
            
            self.stdout.write(f'Fetching sitemap...')
            response = None
            
            # Approach 1: Full headers with session (visit homepage first)
            try:
                self.stdout.write('  Approach 1: Full headers with session...')
                headers = self.get_request_headers(base_url)
                session = requests.Session()
                session.headers.update(headers)
                
                # Visit homepage first to establish session/cookies
                try:
                    self.stdout.write('    Visiting homepage to establish session...')
                    homepage_resp = session.get(base_url, timeout=10, allow_redirects=True, proxies=proxies)
                    self.stdout.write(f'    Homepage response: HTTP {homepage_resp.status_code}')
                    time.sleep(1.0)  # Delay to mimic human behavior
                except Exception as e:
                    self.stdout.write(f'    ⚠️  Homepage visit failed: {str(e)}')
                
                self.stdout.write('    Fetching sitemap...')
                response = session.get(sitemap_url, timeout=30, allow_redirects=True, proxies=proxies)
                self.stdout.write(f'    Sitemap response: HTTP {response.status_code}')
                if response.status_code in (200, 202):
                    self.stdout.write(self.style.SUCCESS(f'  ✅ Success with full headers (HTTP {response.status_code})'))
                else:
                    self.stdout.write(f'  ⚠️  Approach 1 failed: HTTP {response.status_code}')
            except requests.exceptions.Timeout:
                self.stdout.write(f'  ⚠️  Approach 1 failed: Timeout')
                if 'response' not in locals() or response is None:
                    response = None
            except requests.exceptions.RequestException as e:
                self.stdout.write(f'  ⚠️  Approach 1 failed: {type(e).__name__}: {str(e)}')
                # Keep response if it exists (might have a status code)
                if 'response' not in locals():
                    response = None
            except Exception as e:
                self.stdout.write(f'  ⚠️  Approach 1 failed: {type(e).__name__}: {str(e)}')
                if 'response' not in locals():
                    response = None
            
            # Approach 2: If first approach failed, try with minimal headers
            if not response or (hasattr(response, 'status_code') and response.status_code not in (200, 202)):
                try:
                    self.stdout.write('  Approach 2: Minimal headers...')
                    minimal_headers = self.get_request_headers(base_url, minimal=True)
                    response = requests.get(sitemap_url, headers=minimal_headers, timeout=30, allow_redirects=True, proxies=proxies)
                    self.stdout.write(f'    Sitemap response: HTTP {response.status_code}')
                    if response.status_code in (200, 202):
                        self.stdout.write(self.style.SUCCESS(f'  ✅ Success with minimal headers (HTTP {response.status_code})'))
                    else:
                        self.stdout.write(f'  ⚠️  Approach 2 failed: HTTP {response.status_code}')
                except requests.exceptions.Timeout:
                    self.stdout.write(f'  ⚠️  Approach 2 failed: Timeout')
                    # Keep previous response if it exists
                    if 'response' not in locals():
                        response = None
                except requests.exceptions.RequestException as e:
                    self.stdout.write(f'  ⚠️  Approach 2 failed: {type(e).__name__}: {str(e)}')
                    # Keep previous response if it exists
                    if 'response' not in locals():
                        response = None
                except Exception as e:
                    self.stdout.write(f'  ⚠️  Approach 2 failed: {type(e).__name__}: {str(e)}')
                    if 'response' not in locals():
                        response = None
            
            # Approach 3: If still failing, try with just User-Agent and Referer
            if not response or (hasattr(response, 'status_code') and response.status_code not in (200, 202)):
                try:
                    self.stdout.write('  Approach 3: Simple headers...')
                    simple_headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        'Referer': f"{base_url}/",
                    }
                    response = requests.get(sitemap_url, headers=simple_headers, timeout=30, allow_redirects=True, proxies=proxies)
                    self.stdout.write(f'    Sitemap response: HTTP {response.status_code}')
                    if response.status_code in (200, 202):
                        self.stdout.write(self.style.SUCCESS(f'  ✅ Success with simple headers (HTTP {response.status_code})'))
                    else:
                        self.stdout.write(f'  ⚠️  Approach 3 failed: HTTP {response.status_code}')
                except requests.exceptions.Timeout:
                    self.stdout.write(f'  ⚠️  Approach 3 failed: Timeout')
                    # Keep previous response if it exists
                    if 'response' not in locals():
                        response = None
                except requests.exceptions.RequestException as e:
                    self.stdout.write(f'  ⚠️  Approach 3 failed: {type(e).__name__}: {str(e)}')
                    # Keep previous response if it exists
                    if 'response' not in locals():
                        response = None
                except Exception as e:
                    self.stdout.write(f'  ⚠️  Approach 3 failed: {type(e).__name__}: {str(e)}')
                    if 'response' not in locals():
                        response = None
            
            # Approach 4: Use curl via subprocess (curl often works when requests fails)
            if not response or (hasattr(response, 'status_code') and response.status_code not in (200, 202)):
                try:
                    self.stdout.write('  Approach 4: Using curl (subprocess)...')
                    # Build curl command
                    curl_cmd = ['curl', '-s', '-L', '--max-time', '30']
                    
                    # Add proxy if available
                    if proxies and proxies.get('http'):
                        proxy_url = proxies['http']
                        # Extract proxy details for curl format: http://user:pass@host:port
                        curl_cmd.extend(['--proxy', proxy_url])
                    
                    curl_cmd.append(sitemap_url)
                    
                    # Use curl to fetch the sitemap
                    result = subprocess.run(
                        curl_cmd,
                        capture_output=True,
                        text=True,
                        timeout=35
                    )
                    
                    if result.returncode == 0 and result.stdout:
                        # Create a mock response object
                        class MockResponse:
                            def __init__(self, content, status_code=200):
                                self.content = content.encode('utf-8') if isinstance(content, str) else content
                                self.text = content if isinstance(content, str) else content.decode('utf-8', errors='ignore')
                                self.status_code = status_code
                                self.headers = {'Content-Type': 'application/xml'}
                        
                        response = MockResponse(result.stdout, 200)
                        self.stdout.write(self.style.SUCCESS(f'  ✅ Success with curl (HTTP 200)'))
                    else:
                        self.stdout.write(f'  ⚠️  Approach 4 failed: curl returned code {result.returncode}')
                        if result.stderr:
                            self.stdout.write(f'    Error: {result.stderr[:200]}')
                except FileNotFoundError:
                    self.stdout.write('  ⚠️  Approach 4 failed: curl not found in PATH')
                    # Keep previous response if it exists
                    if 'response' not in locals():
                        response = None
                except subprocess.TimeoutExpired:
                    self.stdout.write(f'  ⚠️  Approach 4 failed: curl timeout')
                    # Keep previous response if it exists
                    if 'response' not in locals():
                        response = None
                except Exception as e:
                    self.stdout.write(f'  ⚠️  Approach 4 failed: {type(e).__name__}: {str(e)}')
                    # Keep previous response if it exists
                    if 'response' not in locals():
                        response = None
            
            # Final check - accept 200 (OK) and 202 (Accepted)
            if not response or (hasattr(response, 'status_code') and response.status_code not in (200, 202)):
                status_code = response.status_code if (response and hasattr(response, 'status_code')) else 'No response'
                self.stdout.write(self.style.ERROR(f'\n❌ Failed to fetch sitemap: HTTP {status_code}'))
                
                # Debug info for various error codes
                if response and hasattr(response, 'status_code'):
                    if response.status_code == 403:
                        if hasattr(response, 'text') and response.text:
                            response_preview = response.text[:200].lower()
                            if 'cloudflare' in response_preview or 'challenge' in response_preview:
                                self.stdout.write('  ℹ️  Site appears to be using Cloudflare protection')
                                self.stdout.write('  💡 Tip: This site may require JavaScript or browser automation to access')
                            elif 'access denied' in response_preview or 'forbidden' in response_preview:
                                self.stdout.write('  ℹ️  Access denied by server - may require authentication or whitelisting')
                        return posts
                    elif response.status_code == 202:
                        # 202 Accepted - check if content is actually valid
                        if hasattr(response, 'content') and response.content:
                            # Try to parse as XML to see if it's valid
                            try:
                                test_soup = BeautifulSoup(response.content, 'xml')
                                if test_soup.find('urlset') or test_soup.find('sitemapindex'):
                                    self.stdout.write('  ℹ️  Received 202 Accepted but content appears valid - attempting to parse')
                                    # Continue processing with this response (don't return)
                                else:
                                    self.stdout.write('  ℹ️  Received 202 Accepted but content doesn\'t appear to be a sitemap')
                                    return posts
                            except Exception as e:
                                self.stdout.write(f'  ℹ️  Received 202 Accepted but content is not valid XML: {str(e)}')
                                return posts
                        else:
                            return posts
                    else:
                        return posts
                else:
                    return posts
            
            # Validate it's a sitemap
            content_type = response.headers.get('Content-Type', '')
            if not self.is_valid_sitemap(response.content, content_type):
                self.stdout.write(self.style.ERROR('❌ Response is not a valid sitemap'))
                return posts
            
            self.stdout.write(self.style.SUCCESS('✅ Successfully fetched sitemap\n'))
            
            # Parse XML
            soup = BeautifulSoup(response.content, 'xml')
            
            # Check if it's a sitemap index (contains other sitemaps)
            sitemapindex = soup.find('sitemapindex')
            if sitemapindex:
                self.stdout.write('📋 Found sitemap index, extracting post-related sitemaps...\n')
                
                # Extract all sitemap URLs
                sitemap_urls = []
                for sitemap_tag in soup.find_all('sitemap'):
                    loc = sitemap_tag.find('loc')
                    if loc and loc.text:
                        sitemap_urls.append(loc.text.strip())
                
                self.stdout.write(f'Found {len(sitemap_urls)} sitemaps in index')
                
                # Filter to only post-related sitemaps
                post_sitemaps = [url for url in sitemap_urls if self.is_post_sitemap(url)]
                
                self.stdout.write(f'Filtered to {len(post_sitemaps)} post-related sitemap(s):')
                for url in post_sitemaps:
                    self.stdout.write(f'  - {url}')
                self.stdout.write('')
                
                # Parse each post sitemap
                for i, post_sitemap_url in enumerate(post_sitemaps, 1):
                    self.stdout.write(f'Parsing sitemap {i}/{len(post_sitemaps)}: {post_sitemap_url}')
                    nested_posts = self.parse_sitemap(post_sitemap_url, base_url=base_url, proxies=proxies)
                    posts.extend(nested_posts)
                    self.stdout.write(f'  Found {len(nested_posts)} posts\n')
                    
                    # Small delay between requests
                    if i < len(post_sitemaps):
                        time.sleep(0.5)
            else:
                # Regular sitemap with URLs
                self.stdout.write('📄 Parsing regular sitemap...\n')
                
                filtered_language_count = 0
                for url_tag in soup.find_all('url'):
                    loc = url_tag.find('loc')
                    if not loc or not loc.text:
                        continue
                    
                    url = loc.text.strip()
                    
                    # Filter out non-English versions (URLs with language codes)
                    if self.has_language_code(url):
                        filtered_language_count += 1
                        continue
                    
                    # Extract lastmod (date)
                    lastmod = url_tag.find('lastmod')
                    date_str = lastmod.text.strip() if lastmod and lastmod.text else ''
                    
                    # Extract title if available (some sitemaps include it)
                    title_tag = url_tag.find('image:title') or url_tag.find('title')
                    if title_tag and title_tag.text:
                        title = title_tag.text.strip()
                    else:
                        # Generate title from URL if not in sitemap (same logic as views.py)
                        parsed = urlparse(url)
                        path = parsed.path.strip('/')
                        # Get the last part of the path (slug)
                        slug = path.split('/')[-1] if path else ''
                        # Convert slug to title (replace hyphens with spaces, title case)
                        if slug:
                            title = slug.replace('-', ' ').replace('_', ' ').title()
                        else:
                            title = url
                    
                    posts.append({
                        'url': url,
                        'title': title,
                        'date': date_str
                    })
                
                if filtered_language_count > 0:
                    self.stdout.write(f'  ⏭️  Filtered out {filtered_language_count} non-English URLs (language codes)\n')
                self.stdout.write(self.style.SUCCESS(f'✅ Extracted {len(posts)} URLs from sitemap\n'))
        
        except requests.exceptions.Timeout:
            self.stdout.write(self.style.ERROR(f'❌ Timeout fetching sitemap: {sitemap_url}'))
        except requests.exceptions.RequestException as e:
            self.stdout.write(self.style.ERROR(f'❌ Request error: {str(e)}'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error parsing sitemap: {str(e)}'))
            import traceback
            self.stdout.write(traceback.format_exc())
        
        return posts

