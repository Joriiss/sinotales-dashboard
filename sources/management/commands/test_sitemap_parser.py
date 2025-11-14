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
from pathlib import Path
from django.conf import settings


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

    def handle(self, *args, **options):
        sitemap_url = options['sitemap_url']
        base_url = options.get('base_url')
        use_proxy = options.get('use_proxy', False)
        
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
        
        # Parse the sitemap
        posts = self.parse_sitemap(sitemap_url, base_url=base_url, proxies=proxies)
        
        # Display results
        if posts:
            self.stdout.write(self.style.SUCCESS(f'\n✅ Found {len(posts)} posts:\n'))
            for i, post in enumerate(posts, 1):
                self.stdout.write(f'{i}. {post["title"]}')
                self.stdout.write(f'   URL: {post["url"]}')
                if post.get('date'):
                    self.stdout.write(f'   Date: {post["date"]}')
                self.stdout.write('')
        else:
            self.stdout.write(self.style.WARNING('\n⚠️  No posts found in sitemap'))
        
        self.stdout.write(self.style.SUCCESS(f'\n{"="*60}\n'))

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
            
            # Get proxy list (limit to 1 for testing, you can increase this)
            params = {
                'mode': 'direct',  # direct, backconnect, or datacenter
                'page': 1,
                'page_size': 1,  # Get just one proxy for testing
            }
            
            # Webshare API v2 uses token-based authentication
            # Use API token if available, otherwise use username as token
            headers = {
                'Authorization': f'Token {token_to_use}'
            }
            
            # Disable SSL warnings for cleaner output
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            # Try with SSL verification first
            try:
                response = requests.get(api_url, headers=headers, params=params, timeout=10, verify=True)
            except requests.exceptions.SSLError as ssl_error:
                # If SSL verification fails, try without verification (with warning)
                self.stdout.write(self.style.WARNING('  ⚠️  SSL verification failed, retrying without verification...'))
                response = requests.get(api_url, headers=headers, params=params, timeout=10, verify=False)
            
            # If token auth fails and we have username/password, try basic auth as fallback
            # (Only if we're not using an API token, since API tokens don't work with basic auth)
            if response.status_code == 401 and not api_token and proxy_username and proxy_password:
                self.stdout.write('  Token auth failed, trying basic auth...')
                try:
                    auth = (proxy_username, proxy_password)
                    response = requests.get(api_url, auth=auth, params=params, timeout=10, verify=False)
                except requests.exceptions.SSLError:
                    pass
            
            if response.status_code == 200:
                data = response.json()
                results = data.get('results', [])
                
                if results:
                    # Get the first proxy
                    proxy = results[0]
                    proxy_address = proxy.get('proxy_address')
                    port = proxy.get('port')
                    username = proxy.get('username')
                    password = proxy.get('password')
                    
                    if proxy_address and port and username and password:
                        # Format proxy URL for requests library
                        proxy_url = f'http://{username}:{password}@{proxy_address}:{port}'
                        proxies = {
                            'http': proxy_url,
                            'https': proxy_url
                        }
                        self.stdout.write(self.style.SUCCESS(f'  ✅ Loaded proxy: {proxy_address}:{port}'))
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
                
                for url_tag in soup.find_all('url'):
                    loc = url_tag.find('loc')
                    if not loc or not loc.text:
                        continue
                    
                    url = loc.text.strip()
                    
                    # Extract lastmod (date)
                    lastmod = url_tag.find('lastmod')
                    date_str = lastmod.text.strip() if lastmod and lastmod.text else ''
                    
                    # Extract title if available (some sitemaps include it)
                    title_tag = url_tag.find('image:title') or url_tag.find('title')
                    title = title_tag.text.strip() if title_tag and title_tag.text else url
                    
                    posts.append({
                        'url': url,
                        'title': title,
                        'date': date_str
                    })
                
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

