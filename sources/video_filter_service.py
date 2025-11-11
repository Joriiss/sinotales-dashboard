"""
Service for filtering YouTube videos using AI to determine relevance to China
"""
from typing import Optional
from django.conf import settings


def is_video_relevant(title: str, description: str = '', tags: Optional[list] = None, model: Optional[str] = None) -> bool:
    """
    Use Ollama to determine if a YouTube video is relevant to China.
    
    Args:
        title: Video title
        description: Video description (optional)
        tags: List of video tags (optional)
        model: Ollama model name (if None, uses settings)
        
    Returns:
        True if video is relevant to China, False otherwise
    """
    try:
        import requests
    except ImportError:
        raise ImportError("requests library required for Ollama. Install with: pip install requests")
    
    # Get model from settings if not provided
    if not model:
        try:
            from .models import Settings
            settings_obj = Settings.get_settings()
            model = settings_obj.default_video_filter_model
        except Exception:
            model = 'gpt-oss:20b-cloud'  # Fallback
    
    # Build context from available information
    context_parts = [f"Title: {title}"]
    
    if description:
        # Truncate description if too long (keep first 500 chars)
        desc_preview = description[:500] + "..." if len(description) > 500 else description
        context_parts.append(f"Description: {desc_preview}")
    
    if tags:
        tags_str = ", ".join(tags[:10])  # Limit to first 10 tags
        context_parts.append(f"Tags: {tags_str}")
    
    context = "\n".join(context_parts)
    
    # Create prompt
    prompt = f"""Analyze the following YouTube video information and determine if it is relevant to China.

{context}

Consider the video relevant to China if it discusses:
- Chinese culture, history, geography, or society
- Travel to China, Chinese cities, or regions
- Chinese food, traditions, or customs
- Chinese language, literature, or arts
- Current events or news about China
- Chinese people, communities, or diaspora
- Business, economy, or technology in China

Respond with ONLY "yes" or "no" (lowercase, no punctuation, no explanation).

Relevant?"""
    
    ollama_url = getattr(settings, 'OLLAMA_URL', 'http://localhost:11434')
    url = f"{ollama_url}/api/generate"
    
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,  # Very low temperature for consistent yes/no answers
            "top_p": 0.9,
        }
    }
    
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        answer = result.get('response', '').strip().lower()
        
        # Parse response - look for "yes" or "no"
        if 'yes' in answer and 'no' not in answer:
            return True
        elif 'no' in answer:
            return False
        else:
            # If unclear, default to False (be conservative)
            print(f"Unclear AI response for video '{title}': {answer}")
            return False
            
    except requests.exceptions.ConnectionError:
        raise ConnectionError(
            f"Could not connect to Ollama at {ollama_url}. "
            "Make sure Ollama is running: https://ollama.ai"
        )
    except requests.exceptions.RequestException as e:
        raise Exception(f"Error calling Ollama API: {str(e)}")

