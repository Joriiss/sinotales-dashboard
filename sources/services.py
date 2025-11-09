"""
Service module for AI-powered content tagging
Supports multiple LLM providers: Ollama (default), OpenAI
"""
import json
import re
from typing import List, Optional
from django.conf import settings


class TaggingService:
    """Service for generating tags from content using LLMs"""
    
    def __init__(self, provider: str = 'ollama', model: Optional[str] = None):
        """
        Initialize tagging service
        
        Args:
            provider: 'ollama' or 'openai'
            model: Model name (e.g., 'llama3.2' for Ollama, 'gpt-3.5-turbo' for OpenAI)
        """
        self.provider = provider.lower()
        self.model = model or self._get_default_model()
        # Create session for connection pooling (Ollama only)
        self._session = None
        
    def _get_default_model(self) -> str:
        """Get default model based on provider"""
        if self.provider == 'ollama':
            return 'llama3.2:latest'  # Good balance of quality and speed
        elif self.provider == 'openai':
            return 'gpt-3.5-turbo'
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")
    
    def _create_prompt(self, title: str, content: str, content_type: str) -> str:
        """Create prompt for tag generation"""
        # Truncate content if too long (keep first 1000 chars for context to avoid API issues)
        content_preview = content[:1000] if content else ""
        if len(content) > 1000:
            content_preview += "..."
        
        prompt = f"""Analyze the following {content_type} about China and suggest 3-8 relevant tags.

Title: {title}

Content preview:
{content_preview}

Instructions:
- Generate 3-8 tags that best categorize this content
- Focus on topics like: culture, history, food, travel, cities, nature, architecture, traditions, etc.
- Use lowercase, single words or short phrases (max 2 words)
- Separate tags with commas
- Return ONLY the tags, nothing else
- Example format: culture, history, beijing, temples

Tags:"""
        
        return prompt
    
    def _call_ollama(self, prompt: str) -> str:
        """Call Ollama API"""
        try:
            import requests
        except ImportError:
            raise ImportError("requests library required for Ollama. Install with: pip install requests")
        
        # Use session for connection pooling (reuse connections)
        if self._session is None:
            self._session = requests.Session()
            # Configure session for better performance
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=10,
                pool_maxsize=20,
                max_retries=2
            )
            self._session.mount('http://', adapter)
            self._session.mount('https://', adapter)
        
        ollama_url = getattr(settings, 'OLLAMA_URL', 'http://localhost:11434')
        url = f"{ollama_url}/api/generate"
        
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,  # Lower temperature for more consistent tagging
                "top_p": 0.9,
            }
        }
        
        try:
            response = self._session.post(url, json=payload, timeout=120)  # Use session
            response.raise_for_status()
            result = response.json()
            return result.get('response', '').strip()
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Could not connect to Ollama at {ollama_url}. "
                "Make sure Ollama is running: https://ollama.ai"
            )
        except requests.exceptions.HTTPError as e:
            # Try to get more details from the error response
            error_detail = ""
            try:
                error_detail = response.text[:500] if hasattr(response, 'text') else ""
                try:
                    error_json = response.json()
                    if isinstance(error_json, dict) and 'error' in error_json:
                        error_detail = error_json['error']
                except:
                    pass
            except:
                pass
            
            raise Exception(
                f"Ollama API error ({response.status_code}): {str(e)}. "
                f"Error details: {error_detail}. "
                f"Model: '{self.model}'. "
                f"Try: 'ollama pull {self.model}' or use '--model llama3.2:latest'"
            )
        except requests.exceptions.RequestException as e:
            raise Exception(f"Ollama API error: {str(e)}")
    
    def _call_openai(self, prompt: str) -> str:
        """Call OpenAI API"""
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai library required. Install with: pip install openai")
        
        api_key = getattr(settings, 'OPENAI_API_KEY', None)
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set in settings")
        
        client = OpenAI(api_key=api_key)
        
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that generates relevant tags for content about China."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=100,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            raise Exception(f"OpenAI API error: {str(e)}")
    
    def _parse_tags(self, response: str) -> List[str]:
        """Parse tags from LLM response"""
        # Clean the response
        response = response.strip()
        
        # Remove common prefixes/suffixes
        response = re.sub(r'^(tags?|tagged|categories?):?\s*', '', response, flags=re.IGNORECASE)
        response = re.sub(r'\.$', '', response)  # Remove trailing period
        
        # Split by comma, semicolon, or newline
        tags = re.split(r'[,;\n]', response)
        
        # Clean each tag
        cleaned_tags = []
        for tag in tags:
            tag = tag.strip().lower()
            # Remove quotes and extra whitespace
            tag = re.sub(r'^["\']|["\']$', '', tag)
            tag = tag.strip()
            
            # Skip empty tags and very long ones
            if tag and len(tag) <= 50:
                cleaned_tags.append(tag)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_tags = []
        for tag in cleaned_tags:
            if tag not in seen:
                seen.add(tag)
                unique_tags.append(tag)
        
        # Limit to 8 tags max
        return unique_tags[:8]
    
    def generate_tags(self, title: str, content: str = "", content_type: str = "content") -> List[str]:
        """
        Generate tags for content
        
        Args:
            title: Content title
            content: Content text (optional, will use preview if provided)
            content_type: Type of content (video, blog_post, ebook)
        
        Returns:
            List of tag names
        """
        if not title:
            return []
        
        prompt = self._create_prompt(title, content, content_type)
        
        # Call appropriate provider
        if self.provider == 'ollama':
            response = self._call_ollama(prompt)
        elif self.provider == 'openai':
            response = self._call_openai(prompt)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")
        
        # Parse and return tags
        tags = self._parse_tags(response)
        return tags

