"""
RAG (Retrieval Augmented Generation) service for semantic search and LLM integration
"""
from typing import List, Optional, Dict, Tuple
from django.conf import settings
from django.db.models import Q
from pgvector.django import CosineDistance
from .models import ContentChunk, Content, Tag
from .embedding_service import EmbeddingService


class RAGService:
    """Service for RAG: semantic search + LLM generation"""
    
    def __init__(self):
        """Initialize RAG service"""
        self.embedding_service = EmbeddingService()
        self._session = None
    
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
    
    def _create_rag_prompt(
        self,
        question: str,
        context: str,
        conversation_history: Optional[List[Dict]] = None
    ) -> str:
        """
        Create RAG prompt with context and conversation history
        
        Args:
            question: Current user question
            context: Formatted context from retrieved chunks
            conversation_history: Optional list of previous Q&A pairs
        
        Returns:
            Formatted prompt string
        """
        prompt_parts = []
        
        # System instruction
        prompt_parts.append(
            "You are a helpful assistant that answers questions about China based on the provided context. "
            "Answer the question using only the information from the context. "
            "If the answer cannot be found in the context, say so clearly. "
            "Be concise and accurate."
        )
        
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
    
    def _call_ollama(self, prompt: str, model: str) -> str:
        """Call Ollama API"""
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
    
    def generate_response(
        self,
        question: str,
        model: str,
        num_chunks: int = 5,
        source_id: Optional[int] = None,
        tag_ids: Optional[List[int]] = None,
        content_type: Optional[str] = None,
        conversation_history: Optional[List[Dict]] = None
    ) -> Tuple[str, List[Dict]]:
        """
        Generate RAG response: search + LLM generation
        
        Args:
            question: User's question
            model: Ollama model name
            num_chunks: Number of chunks to retrieve
            source_id: Optional source filter
            tag_ids: Optional tag filter
            content_type: Optional content type filter
            conversation_history: Optional conversation history
        
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
        
        # Create prompt
        prompt = self._create_rag_prompt(question, context, conversation_history)
        
        # Generate response
        try:
            answer = self._call_ollama(prompt, model)
        except ConnectionError as e:
            answer = f"❌ Connection Error: {str(e)}\n\nPlease make sure Ollama is running."
        except Exception as e:
            error_msg = str(e)
            # Check if it's a memory error
            if "memory" in error_msg.lower() or "system memory" in error_msg.lower():
                answer = (
                    f"❌ Memory Error: The model '{model}' requires more memory than available.\n\n"
                    f"💡 Suggestions:\n"
                    f"- Try a smaller model (e.g., llama3.2:3b, gemma3:4b)\n"
                    f"- Close other applications to free up memory\n"
                    f"- Use CPU mode if GPU memory is limited\n"
                    f"- Check available models with: ollama list"
                )
            else:
                answer = f"❌ Error: {error_msg}\n\n💡 Try selecting a different model or check if Ollama is running properly."
        
        # Format sources for return (simplified)
        sources = [
            {
                'title': chunk['title'],
                'source': chunk['source_name'],
                'content_type': chunk['content_type'],
                'tags': chunk['tags'],
                'similarity': round(chunk['similarity'], 3)
            }
            for chunk in chunks
        ]
        
        return answer, sources

