"""
Service for processing content: extract, translate, tag, and embed
"""
from typing import Optional
from django.db import transaction
from .models import Content, Tag, ContentChunk
from .content_extraction_service import extract_article_content
from .services import TaggingService
from .embedding_service import EmbeddingService
from urllib.parse import urlparse

# Translation imports
try:
    from deep_translator import GoogleTranslator
    TRANSLATION_AVAILABLE = True
except ImportError:
    TRANSLATION_AVAILABLE = False


class ContentProcessingService:
    """Service for processing content through the full pipeline"""
    
    def __init__(self, tagging_provider='ollama', tagging_model=None):
        """
        Initialize the processing service
        
        Args:
            tagging_provider: Provider for tagging ('ollama' or 'openai')
            tagging_model: Model name for tagging (optional)
        """
        self.tagging_service = TaggingService(provider=tagging_provider, model=tagging_model)
        self.embedding_service = EmbeddingService()
    
    def extract_content(self, content: Content) -> bool:
        """
        Extract content from URL if content is empty and link is available.
        
        Args:
            content: Content object
            
        Returns:
            True if content was extracted, False otherwise
        """
        # Only extract for blog posts with links
        if content.content_type != 'blog_post':
            return False
        
        # Skip if content already exists
        if content.content and content.content.strip():
            return False
        
        # Skip if no link
        if not content.link:
            return False
        
        try:
            # Extract base URL for Referer header
            parsed_url = urlparse(content.link)
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            
            # Extract content
            result = extract_article_content(content.link, base_url)
            
            if result and result.get('content'):
                content.content = result['content']
                
                # Update date if missing and we found one
                if result.get('date') and not content.date:
                    try:
                        from datetime import datetime
                        date_str = result['date']
                        # Parse ISO format date
                        if 'T' in date_str:
                            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                        else:
                            dt = datetime.strptime(date_str, '%Y-%m-%d')
                        content.date = dt.date()
                    except (ValueError, AttributeError):
                        pass
                
                # Save the extracted content
                content.save(update_fields=['content', 'date'])
                return True
        except Exception as e:
            # Log error but don't fail
            print(f"Error extracting content from {content.link}: {str(e)}")
            return False
        
        return False
    
    def translate_content(self, content: Content) -> bool:
        """
        Translate content from French to English if source language is French.
        
        Args:
            content: Content object
            
        Returns:
            True if content was translated, False otherwise
        """
        # Check if translation is available
        if not TRANSLATION_AVAILABLE:
            return False
        
        # Check if source is French
        source = content.source
        if not source or source.language.lower() not in ('fr', 'french', 'français'):
            return False
        
        # Skip if no content to translate
        if not content.content or not content.content.strip():
            return False
        
        try:
            content_text = content.content
            chunk_size = 4500
            translated_chunks = []
            
            if len(content_text) <= chunk_size:
                # Small content - translate in one go
                translator = GoogleTranslator(source='fr', target='en')
                translated_text = translator.translate(content_text)
                content.content = translated_text
                return True
            else:
                # Large content - translate in chunks
                translator = GoogleTranslator(source='fr', target='en')
                sentences = content_text.split('. ')
                current_chunk = ''
                
                for sentence in sentences:
                    if len(current_chunk) + len(sentence) <= chunk_size:
                        current_chunk += sentence + '. '
                    else:
                        if current_chunk:
                            translated_chunk = translator.translate(current_chunk)
                            translated_chunks.append(translated_chunk)
                        current_chunk = sentence + '. '
                
                if current_chunk:
                    translated_chunk = translator.translate(current_chunk)
                    translated_chunks.append(translated_chunk)
                
                content.content = ' '.join(translated_chunks)
                return True
        except Exception as e:
            print(f"Error translating content {content.id}: {str(e)}")
            return False
    
    def add_tags(self, content: Content) -> bool:
        """
        Add tags to content using AI tagging service.
        
        Args:
            content: Content object
            
        Returns:
            True if tags were added, False otherwise
        """
        # Skip if content already has tags
        if content.tags.exists():
            return False
        
        # Skip if no title
        if not content.title:
            return False
        
        try:
            # Generate tags
            content_text = content.content if hasattr(content, 'content') else ""
            generated_tags = self.tagging_service.generate_tags(
                title=content.title,
                content=content_text,
                content_type=content.content_type
            )
            
            if not generated_tags:
                return False
            
            # Get or create tag objects
            tag_objects = []
            for tag_name in generated_tags:
                tag, created = Tag.objects.get_or_create(name=tag_name)
                tag_objects.append(tag)
            
            # Set tags
            content.tags.set(tag_objects)
            return True
        except Exception as e:
            print(f"Error adding tags to content {content.id}: {str(e)}")
            return False
    
    def generate_embeddings(self, content: Content, chunk_size: int = 8000, overlap: int = 200) -> bool:
        """
        Generate embeddings for content.
        
        Args:
            content: Content object
            chunk_size: Maximum characters per chunk
            overlap: Overlap between chunks
            
        Returns:
            True if embeddings were generated, False otherwise
        """
        # Skip if content already has embeddings
        if content.chunks.exists():
            return False
        
        # Skip if no content text
        if not content.content or not content.content.strip():
            return False
        
        # Skip if no tags (required for embedding context)
        if not content.tags.exists():
            return False
        
        try:
            # Get tags
            tags = list(content.tags.values_list('name', flat=True))
            
            # Generate embeddings
            chunk_results = self.embedding_service.generate_embeddings_for_content(
                title=content.title,
                content_text=content.content,
                tags=tags,
                chunk_size=chunk_size,
                overlap=overlap
            )
            
            if not chunk_results:
                return False
            
            # Delete existing chunks if any
            content.chunks.all().delete()
            
            # Create chunks with embeddings
            chunks_to_create = []
            for idx, (chunk_text, embedding) in enumerate(chunk_results):
                if embedding:  # Only create chunks with valid embeddings
                    chunks_to_create.append(
                        ContentChunk(
                            content=content,
                            chunk_index=idx,
                            text=chunk_text,
                            embedding=embedding
                        )
                    )
            
            if chunks_to_create:
                ContentChunk.objects.bulk_create(chunks_to_create)
                content.processed = True
                return True
        except Exception as e:
            print(f"Error generating embeddings for content {content.id}: {str(e)}")
            return False
        
        return False
    
    def process_content(self, content: Content, extract: bool = True, translate: bool = True, 
                       tag: bool = True, embed: bool = True) -> dict:
        """
        Process content through the full pipeline.
        
        Args:
            content: Content object
            extract: Whether to extract content from URL
            translate: Whether to translate French content
            tag: Whether to add tags
            embed: Whether to generate embeddings
            
        Returns:
            Dict with processing results
        """
        results = {
            'extracted': False,
            'translated': False,
            'tagged': False,
            'embedded': False,
        }
        
        # Step 1: Extract content
        if extract:
            results['extracted'] = self.extract_content(content)
            # Content is saved inside extract_content, no need to refresh
        
        # Step 2: Translate if French
        if translate:
            results['translated'] = self.translate_content(content)
            if results['translated']:
                content.save(update_fields=['content'])
        
        # Step 3: Add tags (needs content to be present)
        if tag:
            # Only tag if we have content
            if content.content and content.content.strip():
                results['tagged'] = self.add_tags(content)
                # Tags are saved via ManyToMany, refresh to get updated tags
                if results['tagged']:
                    content.refresh_from_db()
        
        # Step 4: Generate embeddings (needs tags)
        if embed:
            # Only embed if we have content and tags
            if content.content and content.content.strip() and content.tags.exists():
                results['embedded'] = self.generate_embeddings(content)
                if results['embedded']:
                    content.save(update_fields=['processed'])
        
        return results

