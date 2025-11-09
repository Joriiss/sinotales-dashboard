"""
Service module for generating vector embeddings using OpenAI
"""
import re
from typing import List, Optional, Tuple
from django.conf import settings


class EmbeddingService:
    """Service for generating embeddings using OpenAI"""
    
    def __init__(self):
        """Initialize embedding service with OpenAI client"""
        self.api_key = getattr(settings, 'OPENAI_API_KEY', None)
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not set in settings")
        
        self.model = getattr(settings, 'OPENAI_EMBEDDING_MODEL', 'text-embedding-3-small')
        self.dimensions = getattr(settings, 'OPENAI_EMBEDDING_DIMENSIONS', 1536)
        
        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=self.api_key)
        except ImportError:
            raise ImportError("openai library required. Install with: pip install openai")
    
    def _prepare_text_for_embedding(
        self, 
        title: str, 
        chunk_text: str, 
        tags: List[str] = None
    ) -> str:
        """
        Prepare text for embedding by combining title, tags, and chunk text
        
        Args:
            title: Content title
            chunk_text: Text content of the chunk
            tags: List of tag names
        
        Returns:
            Formatted text string for embedding
        """
        parts = []
        
        # Add title
        if title:
            parts.append(f"Title: {title}")
        
        # Add tags if available
        if tags:
            tags_str = ', '.join(tags)
            parts.append(f"Tags: {tags_str}")
        
        # Add chunk content
        if chunk_text:
            parts.append(f"Content: {chunk_text}")
        
        return "\n\n".join(parts)
    
    def _chunk_content(
        self, 
        content: str, 
        chunk_size: int = 8000, 
        overlap: int = 200
    ) -> List[str]:
        """
        Split content into chunks with overlap
        
        Args:
            content: Full content text to chunk
            chunk_size: Maximum characters per chunk (default: 8000)
            overlap: Number of characters to overlap between chunks (default: 200)
        
        Returns:
            List of chunk text strings
        """
        if not content or len(content) <= chunk_size:
            return [content] if content else []
        
        chunks = []
        start = 0
        content_length = len(content)
        
        while start < content_length:
            # Calculate end position
            end = start + chunk_size
            
            # If this is not the last chunk, try to break at a sentence boundary
            if end < content_length:
                # Look for sentence endings within the last 500 chars
                search_start = max(start, end - 500)
                sentence_end = max(
                    content.rfind('. ', search_start, end),
                    content.rfind('.\n', search_start, end),
                    content.rfind('! ', search_start, end),
                    content.rfind('?\n', search_start, end),
                    content.rfind('\n\n', search_start, end),  # Paragraph break
                )
                
                # If we found a good break point, use it
                if sentence_end > start:
                    end = sentence_end + 1
            
            # Extract chunk
            chunk = content[start:end].strip()
            if chunk:
                chunks.append(chunk)
            
            # Move start position (with overlap)
            if end >= content_length:
                break
            start = end - overlap
        
        return chunks
    
    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """
        Generate embedding for a single text string
        
        Args:
            text: Text to generate embedding for
        
        Returns:
            List of floats representing the embedding vector, or None on error
        """
        if not text or not text.strip():
            return None
        
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=text,
                dimensions=self.dimensions
            )
            
            return response.data[0].embedding
        except Exception as e:
            print(f"Error generating embedding: {str(e)}")
            return None
    
    def generate_embeddings_for_content(
        self,
        title: str,
        content_text: str,
        tags: List[str] = None,
        chunk_size: int = 8000,
        overlap: int = 200
    ) -> List[Tuple[str, Optional[List[float]]]]:
        """
        Generate embeddings for all chunks of a content item
        
        Args:
            title: Content title
            content_text: Full content text
            tags: List of tag names
            chunk_size: Maximum characters per chunk
            overlap: Overlap between chunks
        
        Returns:
            List of tuples: (chunk_text, embedding_vector)
            Each tuple represents one chunk and its embedding
        """
        if not content_text or not content_text.strip():
            # If no content, create a single embedding from title and tags only
            text = self._prepare_text_for_embedding(title, "", tags)
            embedding = self.generate_embedding(text)
            return [("", embedding)] if embedding else []
        
        # Chunk the content
        chunks = self._chunk_content(content_text, chunk_size, overlap)
        
        if not chunks:
            return []
        
        # Generate embedding for each chunk
        results = []
        for chunk_text in chunks:
            # Prepare text with title and tags for each chunk
            text = self._prepare_text_for_embedding(title, chunk_text, tags)
            embedding = self.generate_embedding(text)
            results.append((chunk_text, embedding))
        
        return results

