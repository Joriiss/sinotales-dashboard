"""
RAG (Retrieval Augmented Generation) service for semantic search and LLM integration
"""
from typing import List, Optional, Dict, Tuple
from django.conf import settings
from django.db.models import Q
from pgvector.django import CosineDistance
from .models import ContentChunk, Content, Tag
from .embedding_service import EmbeddingService
import re
import requests


class RAGService:
    """Service for RAG: semantic search + LLM generation"""
    
    def __init__(self):
        """Initialize RAG service"""
        try:
            self.embedding_service = EmbeddingService()
        except ValueError as e:
            # Re-raise with clearer message
            if "OPENAI_API_KEY" in str(e):
                raise ValueError(
                    "OPENAI_API_KEY is required for semantic search. "
                    "Even though Ollama is used for generation, OpenAI embeddings are needed to find relevant content. "
                    "Please set OPENAI_API_KEY in your settings or environment variables."
                )
            raise
        self._session = None
        self._web_session = None
    
    def search_similar_chunks(
        self,
        query_text: str,
        num_chunks: int = 5,
        source_id: Optional[int] = None,
        tag_ids: Optional[List[int]] = None,
        content_type: Optional[str] = None
    ) -> List[Dict]:
        """
        Search for similar content chunks using vector similarity
        
        Args:
            query_text: User's query text
            num_chunks: Number of chunks to retrieve
            source_id: Optional source filter
            tag_ids: Optional list of tag IDs to filter by
            content_type: Optional content type filter (video, blog_post, ebook)
        
        Returns:
            List of dicts with chunk data: {
                'chunk': ContentChunk object,
                'content': Content object,
                'title': str,
                'tags': List[str],
                'text': str,
                'similarity': float (1 - distance, higher is better)
            }
        """
        # Generate embedding for query
        query_embedding = self.embedding_service.generate_embedding(query_text)
        if not query_embedding:
            return []
        
        # Start with base queryset
        queryset = ContentChunk.objects.filter(
            embedding__isnull=False
        ).select_related('content', 'content__source').prefetch_related('content__tags')
        
        # Apply filters
        if source_id:
            queryset = queryset.filter(content__source_id=source_id)
        
        if tag_ids:
            queryset = queryset.filter(content__tags__id__in=tag_ids).distinct()
        
        if content_type:
            queryset = queryset.filter(content__content_type=content_type)
        
        # Perform vector similarity search
        # CosineDistance returns a value where 0 = identical, 2 = opposite
        # We want to order by distance (ascending) to get most similar first
        queryset = queryset.annotate(
            distance=CosineDistance('embedding', query_embedding)
        ).order_by('distance')[:num_chunks]
        
        # Format results
        results = []
        for chunk in queryset:
            # Convert distance to similarity (1 - distance, clamped to 0-1)
            # CosineDistance: 0 = identical, 2 = opposite
            # Similarity: 1 = identical, 0 = opposite
            similarity = max(0, 1 - chunk.distance)
            
            results.append({
                'chunk': chunk,
                'content': chunk.content,
                'title': chunk.content.title,
                'tags': [tag.name for tag in chunk.content.tags.all()],
                'text': chunk.text,
                'similarity': similarity,
                'source_name': chunk.content.source.name,
                'content_type': chunk.content.get_content_type_display(),
            })
        
        return results
    
    def _format_context(self, chunks: List[Dict]) -> str:
        """
        Format retrieved chunks as context for LLM
        
        Args:
            chunks: List of chunk dicts from search_similar_chunks
        
        Returns:
            Formatted context string
        """
        if not chunks:
            return "No relevant content found."
        
        context_parts = []
        for i, chunk_data in enumerate(chunks, 1):
            title = chunk_data['title']
            tags = chunk_data['tags']
            text = chunk_data['text']
            source = chunk_data['source_name']
            
            context_part = f"[Source {i}] {title}"
            if tags:
                context_part += f" (Tags: {', '.join(tags)})"
            context_part += f"\nFrom: {source}\n\n{text}"
            context_parts.append(context_part)
        
        return "\n\n---\n\n".join(context_parts)
    
    def search_web(self, query: str, num_results: int = 3) -> List[Dict]:
        """
        Search the web for additional information with automatic fallback
        
        Args:
            query: Search query
            num_results: Number of results to return
            
        Returns:
            List of dicts with web results: {
                'title': str,
                'url': str,
                'snippet': str,
                'score': float
            }
        """
        tavily_api_key = getattr(settings, 'TAVILY_API_KEY', None)
        serper_api_key = getattr(settings, 'SERPER_API_KEY', None)
        google_api_key = getattr(settings, 'GOOGLE_API_KEY', None)
        google_cse_id = getattr(settings, 'GOOGLE_CSE_ID', None)
        
        # Try Tavily first (if configured)
        if tavily_api_key:
            try:
                print(f"Trying Tavily API for web search")
                return self._search_tavily(query, num_results, tavily_api_key)
            except Exception as e:
                error_msg = str(e).lower()
                # Check if it's a rate limit, quota, or credit limit error
                if any(keyword in error_msg for keyword in [
                    'rate limit', 'quota', '429', '402', 'limit exceeded', 
                    'credits', 'exceeded', 'insufficient', 'payment required'
                ]):
                    print(f"Tavily rate limit/quota exceeded, falling back to Serper...")
                else:
                    print(f"Tavily API error: {e}, falling back to Serper...")
                # Fall through to try Serper
        
        # Try Serper as fallback (if configured)
        if serper_api_key:
            try:
                print(f"Trying Serper API for web search")
                return self._search_serper(query, num_results, serper_api_key)
            except Exception as e:
                print(f"Serper API error: {e}")
                # Fall through to try Google if Serper also fails
        
        # Try Google as last resort (if configured)
        if google_api_key and google_cse_id:
            try:
                print(f"Trying Google Custom Search API for web search")
                return self._search_google(query, num_results, google_api_key, google_cse_id)
            except Exception as e:
                print(f"Google API error: {e}")
                raise Exception(f"All web search APIs failed. Last error: {str(e)}")
        
        # No API key configured
        print("No web search API key configured")
        raise Exception(
            "No web search API key configured. "
            "Please set TAVILY_API_KEY, SERPER_API_KEY, or GOOGLE_API_KEY in your settings."
        )
    
    def _search_tavily(self, query: str, num_results: int, api_key: str) -> List[Dict]:
        """Search using Tavily API (optimized for RAG)"""
        if self._web_session is None:
            self._web_session = requests.Session()
        
        url = "https://api.tavily.com/search"
        payload = {
            "api_key": api_key,
            "query": query,
            "search_depth": "basic",
            "include_answer": True,
            "include_raw_content": False,
            "max_results": num_results
        }
        
        try:
            response = self._web_session.post(url, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Check for errors in response
            if 'error' in data:
                raise Exception(f"Tavily API error: {data['error']}")
            
            results = []
            # Tavily provides an answer summary
            if data.get('answer'):
                results.append({
                    'title': 'Web Search Summary',
                    'url': '',
                    'snippet': data['answer'],
                    'score': 1.0
                })
            
            # Add individual results
            for result in data.get('results', []):
                results.append({
                    'title': result.get('title', ''),
                    'url': result.get('url', ''),
                    'snippet': result.get('content', ''),
                    'score': result.get('score', 0.8)
                })
            
            print(f"Tavily search successful: {len(results)} results")
            return results[:num_results] if results else []
        except requests.exceptions.HTTPError as e:
            error_detail = ""
            try:
                error_json = response.json()
                error_detail = error_json.get('error', str(e))
            except:
                error_detail = response.text[:200] if hasattr(response, 'text') else str(e)
            print(f"Tavily HTTP error: {error_detail}")
            raise Exception(f"Tavily API HTTP error: {error_detail}")
        except Exception as e:
            print(f"Tavily search error: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    def _search_serper(self, query: str, num_results: int, api_key: str) -> List[Dict]:
        """Search using Serper API"""
        if self._web_session is None:
            self._web_session = requests.Session()
        
        url = "https://google.serper.dev/search"
        headers = {
            "X-API-KEY": api_key,
            "Content-Type": "application/json"
        }
        payload = {
            "q": query,
            "num": num_results
        }
        
        try:
            response = self._web_session.post(url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Check for errors in response
            if 'error' in data:
                raise Exception(f"Serper API error: {data['error']}")
            
            results = []
            for result in data.get('organic', []):
                results.append({
                    'title': result.get('title', ''),
                    'url': result.get('link', ''),
                    'snippet': result.get('snippet', ''),
                    'score': 0.8
                })
            
            print(f"Serper search successful: {len(results)} results")
            return results if results else []
        except requests.exceptions.HTTPError as e:
            error_detail = ""
            try:
                error_json = response.json()
                error_detail = error_json.get('message', error_json.get('error', str(e)))
            except:
                error_detail = response.text[:200] if hasattr(response, 'text') else str(e)
            print(f"Serper HTTP error: {error_detail}")
            raise Exception(f"Serper API HTTP error: {error_detail}")
        except Exception as e:
            print(f"Serper search error: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    def _search_google(self, query: str, num_results: int, api_key: str, cse_id: str) -> List[Dict]:
        """Search using Google Custom Search API"""
        if self._web_session is None:
            self._web_session = requests.Session()
        
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": api_key,
            "cx": cse_id,
            "q": query,
            "num": min(num_results, 10)  # Google limits to 10
        }
        
        try:
            response = self._web_session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            results = []
            for item in data.get('items', []):
                results.append({
                    'title': item.get('title', ''),
                    'url': item.get('link', ''),
                    'snippet': item.get('snippet', ''),
                    'score': 0.8
                })
            
            return results
        except Exception as e:
            print(f"Google search error: {e}")
            return []
    
    def _format_context_with_web(self, chunks: List[Dict], web_results: List[Dict] = None) -> str:
        """
        Format retrieved chunks and web results as context for LLM
        """
        context_parts = []
        
        # Database context
        if chunks:
            context_parts.append("=== Information from your content library ===")
            for i, chunk_data in enumerate(chunks, 1):
                title = chunk_data['title']
                tags = chunk_data['tags']
                text = chunk_data['text']
                source = chunk_data['source_name']
                
                context_part = f"[Source {i}] {title}"
                if tags:
                    context_part += f" (Tags: {', '.join(tags)})"
                context_part += f"\nFrom: {source}\n\n{text}"
                context_parts.append(context_part)
        else:
            context_parts.append("No relevant information found in your content library.")
        
        # Web context
        if web_results:
            context_parts.append("\n\n=== Additional information from the web ===")
            for i, web_result in enumerate(web_results, 1):
                context_part = f"[Web Source {i}] {web_result['title']}\n"
                if web_result['url']:
                    context_part += f"URL: {web_result['url']}\n"
                context_part += f"Content: {web_result['snippet']}"
                context_parts.append(context_part)
        
        return "\n\n---\n\n".join(context_parts)
    
    def _extract_search_request(self, text: str) -> Optional[str]:
        """
        Extract web search request from LLM response
        Looks for patterns like: SEARCH: <query> or [SEARCH: <query>]
        """
        # Pattern 1: SEARCH: query
        match = re.search(r'SEARCH:\s*(.+?)(?:\n|$)', text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        
        # Pattern 2: [SEARCH: query]
        match = re.search(r'\[SEARCH:\s*(.+?)\]', text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        
        # Pattern 3: search_web("query")
        match = re.search(r'search_web\(["\'](.+?)["\']\)', text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        
        return None
    
    def _create_rag_prompt(
        self,
        question: str,
        context: str,
        conversation_history: Optional[List[Dict]] = None,
        web_search_enabled: bool = False
    ) -> str:
        """
        Create RAG prompt with context and conversation history
        
        Args:
            question: Current user question
            context: Formatted context from retrieved chunks
            conversation_history: Optional list of previous Q&A pairs
            web_search_enabled: Whether web search is available
        
        Returns:
            Formatted prompt string
        """
        prompt_parts = []
        
        # System instruction
        instruction = (
            "You are a helpful assistant that answers questions about China based on the provided context. "
            "Answer the question using the information from the context. "
            "Be concise and accurate."
        )
        
        # Add web search instructions if enabled
        if web_search_enabled:
            instruction += (
                "\n\nIf you need more current information, recent data, or the database context is insufficient, "
                "you can request a web search by responding with exactly: SEARCH: <your search query>\n"
                "For example: SEARCH: current visa requirements for China 2024\n"
                "Only request a search if truly needed. Otherwise, answer based on the provided context."
            )
        else:
            instruction += (
                " If the answer cannot be found in the context, say so clearly."
            )
        
        prompt_parts.append(instruction)
        
        # Conversation history (if any)
        if conversation_history:
            prompt_parts.append("\n\nPrevious conversation:")
            for hist in conversation_history[-3:]:  # Last 3 exchanges
                prompt_parts.append(f"Q: {hist.get('question', '')}")
                prompt_parts.append(f"A: {hist.get('answer', '')}")
        
        # Current context
        prompt_parts.append(f"\n\nContext:\n{context}")
        
        # Current question
        prompt_parts.append(f"\n\nQuestion: {question}")
        prompt_parts.append("\nAnswer:")
        
        return "\n".join(prompt_parts)
    
    def _call_ollama(self, prompt: str, model: str, max_tokens: int = None) -> str:
        """Call Ollama API
        
        Args:
            prompt: The prompt to send
            model: The model name
            max_tokens: Optional maximum tokens (default: None, uses model default)
        """
        try:
            import requests
        except ImportError:
            raise ImportError("requests library required for Ollama. Install with: pip install requests")
        
        # Use session for connection pooling
        if self._session is None:
            self._session = requests.Session()
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
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,  # Higher temperature for more natural responses
                "top_p": 0.9,
            }
        }
        
        # Add num_predict (max_tokens) if specified
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens
        
        try:
            response = self._session.post(url, json=payload, timeout=180)
            response.raise_for_status()
            result = response.json()
            return result.get('response', '').strip()
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Could not connect to Ollama at {ollama_url}. "
                "Make sure Ollama is running: https://ollama.ai"
            )
        except requests.exceptions.HTTPError as e:
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
                f"Model: '{model}'. "
                f"Try: 'ollama pull {model}'"
            )
        except requests.exceptions.RequestException as e:
            raise Exception(f"Ollama API error: {str(e)}")
    
    def _call_openai(self, prompt: str, model: str, max_tokens: int = 2000) -> str:
        """Call OpenAI API
        
        Args:
            prompt: The prompt to send
            model: The model name
            max_tokens: Maximum tokens to generate (default: 2000)
        """
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai library required. Install with: pip install openai")
        
        api_key = getattr(settings, 'OPENAI_API_KEY', None)
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not set in settings. Please configure it to use OpenAI.")
        
        client = OpenAI(api_key=api_key)
        
        # Check model type for parameter compatibility
        is_gpt5 = 'gpt-5' in model.lower()
        is_newer_model = any(keyword in model.lower() for keyword in ['gpt-4o', 'gpt-5', 'o1', 'o3'])
        
        # Build request parameters
        request_params = {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
        }
        
        # GPT-5 only supports default temperature (1), so don't set it
        if not is_gpt5:
            request_params["temperature"] = 0.7
        
        # Use appropriate parameter based on model
        if is_newer_model:
            request_params["max_completion_tokens"] = max_tokens
        else:
            request_params["max_tokens"] = max_tokens
        
        try:
            response = client.chat.completions.create(**request_params)
            return response.choices[0].message.content.strip()
        except Exception as e:
            error_str = str(e)
            raise Exception(f"OpenAI API error: {error_str}")
    
    def _call_gemini(self, prompt: str, model: str, max_tokens: int = 2000) -> str:
        """Call Gemini API via REST (IPv4-forced / optional proxy).
        
        Uses sources.gemini_client instead of google.generativeai so VPS egress
        can prefer IPv4 or a GEMINI_HTTP_PROXY (avoids location blocks).
        
        Args:
            prompt: The prompt to send
            model: The model name
            max_tokens: Maximum tokens to generate (default: 2000)
        """
        from .gemini_client import generate_content

        try:
            return generate_content(prompt, model, max_tokens=max_tokens)
        except Exception as e:
            error_str = str(e)
            # Already prefixed / structured by gemini_client
            if error_str.startswith('Gemini API') or 'blocked' in error_str.lower() or 'empty response' in error_str.lower():
                raise
            raise Exception(f'Gemini API error: {error_str}') from e
    
    def generate_response(
        self,
        question: str,
        provider: str = 'ollama',
        model: str = '',
        num_chunks: int = 5,
        source_id: Optional[int] = None,
        tag_ids: Optional[List[int]] = None,
        content_type: Optional[str] = None,
        conversation_history: Optional[List[Dict]] = None,
        web_search_enabled: bool = False
    ) -> Tuple[str, List[Dict]]:
        """
        Generate RAG response: search + LLM generation with optional web search
        
        Args:
            question: User's question
            provider: AI provider ('ollama', 'openai', or 'gemini')
            model: Model name (provider-specific)
            num_chunks: Number of chunks to retrieve
            source_id: Optional source filter
            tag_ids: Optional tag filter
            content_type: Optional content type filter
            conversation_history: Optional conversation history
            web_search_enabled: Whether to allow LLM to request web searches
        
        Returns:
            Tuple of (answer_text, sources_list)
        """
        # Search for similar chunks
        chunks = self.search_similar_chunks(
            query_text=question,
            num_chunks=num_chunks,
            source_id=source_id,
            tag_ids=tag_ids,
            content_type=content_type
        )
        
        # Format context
        context = self._format_context(chunks)
        
        # Create prompt with web search instructions if enabled
        prompt = self._create_rag_prompt(question, context, conversation_history, web_search_enabled)
        
        # Generate initial response based on provider
        try:
            if provider == 'ollama':
                answer = self._call_ollama(prompt, model)
            elif provider == 'openai':
                answer = self._call_openai(prompt, model)
            elif provider == 'gemini':
                answer = self._call_gemini(prompt, model)
            else:
                answer = f"❌ Error: Invalid provider '{provider}'. Must be one of: ollama, openai, gemini"
                sources = []
                return answer, sources
        except ConnectionError as e:
            if provider == 'ollama':
                answer = f"❌ Connection Error: {str(e)}\n\nPlease make sure Ollama is running."
            else:
                answer = f"❌ Connection Error: {str(e)}"
            sources = []
            return answer, sources
        except ValueError as e:
            # API key errors
            answer = f"❌ Configuration Error: {str(e)}"
            sources = []
            return answer, sources
        except Exception as e:
            error_msg = str(e)
            # Check if it's a memory error (Ollama specific)
            if provider == 'ollama' and ("memory" in error_msg.lower() or "system memory" in error_msg.lower()):
                answer = (
                    f"❌ Memory Error: The model '{model}' requires more memory than available.\n\n"
                    f"💡 Suggestions:\n"
                    f"- Try a smaller model (e.g., llama3.2:3b, gemma3:4b)\n"
                    f"- Close other applications to free up memory\n"
                    f"- Use CPU mode if GPU memory is limited\n"
                    f"- Check available models with: ollama list"
                )
            else:
                provider_name = provider.upper()
                answer = f"❌ Error: {error_msg}\n\n💡 Try selecting a different model or check your {provider_name} configuration."
            sources = []
            return answer, sources
        
        # Check if LLM requested a web search
        web_results = []
        if web_search_enabled:
            search_query = self._extract_search_request(answer)
            if search_query:
                # Perform web search
                try:
                    print(f"Web search requested: {search_query}")
                    web_results = self.search_web(search_query, num_results=3)
                    print(f"Web search returned {len(web_results)} results")
                    
                    if web_results:
                        # Update context with web results
                        context = self._format_context_with_web(chunks, web_results)
                        
                        # Create new prompt with web results
                        followup_prompt = self._create_rag_prompt(
                            question, 
                            context, 
                            conversation_history, 
                            web_search_enabled=False  # Don't allow another search
                        )
                        
                        # Generate final answer with web context
                        try:
                            print("Generating followup answer with web results...")
                            if provider == 'ollama':
                                followup_answer = self._call_ollama(followup_prompt, model)
                            elif provider == 'openai':
                                followup_answer = self._call_openai(followup_prompt, model)
                            elif provider == 'gemini':
                                followup_answer = self._call_gemini(followup_prompt, model)
                            else:
                                followup_answer = "Error: Invalid provider"
                            
                            # Remove the SEARCH: line from answer if present
                            answer = re.sub(r'SEARCH:\s*.+', '', followup_answer, flags=re.IGNORECASE).strip()
                            # Also remove any remaining SEARCH: lines
                            answer = re.sub(r'SEARCH:\s*.+', '', answer, flags=re.IGNORECASE | re.MULTILINE).strip()
                            
                            # If answer is empty after removing SEARCH line, use the followup answer
                            if not answer or len(answer) < 10:
                                answer = followup_answer
                                # Try one more time to clean it
                                answer = re.sub(r'SEARCH:\s*.+', '', answer, flags=re.IGNORECASE | re.MULTILINE).strip()
                            
                            print(f"Followup answer generated successfully (length: {len(answer)})")
                        except Exception as e:
                            # If followup fails, use original answer but inform user
                            print(f"Followup generation error: {e}")
                            import traceback
                            traceback.print_exc()
                            answer = (
                                f"I requested a web search for: {search_query}\n\n"
                                f"However, I encountered an error generating the final answer. "
                                f"Error: {str(e)}"
                            )
                    else:
                        # Web search returned no results
                        print("Web search returned no results")
                        answer = (
                            f"I requested a web search for: {search_query}\n\n"
                            f"However, the search did not return any results. "
                            f"This might be due to API configuration issues or the search query."
                        )
                except Exception as e:
                    # Web search failed, inform user
                    print(f"Web search error: {e}")
                    import traceback
                    traceback.print_exc()
                    answer = (
                        f"I requested a web search for: {search_query}\n\n"
                        f"However, the web search failed: {str(e)}\n\n"
                        f"Please check your API key configuration (TAVILY_API_KEY, SERPER_API_KEY, or GOOGLE_API_KEY)."
                    )
        
        # Format sources for return
        sources = [
            {
                'title': chunk['title'],
                'source': chunk['source_name'],
                'content_type': chunk['content_type'],
                'tags': chunk['tags'],
                'similarity': round(chunk['similarity'], 3),
                'type': 'database'
            }
            for chunk in chunks
        ]
        
        # Add web sources
        for web_result in web_results:
            sources.append({
                'title': web_result['title'],
                'source': web_result['url'] if web_result['url'] else 'Web Search',
                'content_type': 'Web',
                'tags': [],
                'similarity': round(web_result.get('score', 0.7), 3),
                'type': 'web'
            })
        
        return answer, sources
