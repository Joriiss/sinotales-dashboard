"""
Utility functions for the sources app
"""
from django.contrib.auth import get_user
from .models import ActivityLog, PostIdea
from pgvector.django import CosineDistance


def log_activity(activity_type, description, user=None, source=None, content=None, metadata=None):
    """
    Log an activity to the activity log
    
    Args:
        activity_type: Type of activity (from ActivityLog.ACTIVITY_TYPE_CHOICES)
        description: Human-readable description
        user: User who performed the activity (can be User object or username string)
        source: Related Source object (optional)
        content: Related Content object (optional)
        metadata: Additional metadata dict (optional)
    """
    # Get username from user object or use string directly
    username = None
    if user:
        if hasattr(user, 'username'):
            username = user.username
        else:
            username = str(user)
    
    ActivityLog.objects.create(
        activity_type=activity_type,
        description=description,
        user=username,
        source=source,
        content=content,
        metadata=metadata or {}
    )


def is_idea_too_similar_with_embeddings(new_title, existing_ideas, embedding_service, similarity_threshold=0.92, debug=False):
    """
    Check if a new idea is too similar to existing ideas using embeddings.
    
    Args:
        new_title: Title of the new idea to check
        existing_ideas: List of PostIdea objects with embeddings
        embedding_service: EmbeddingService instance
        similarity_threshold: Cosine similarity threshold (0.0-1.0, higher = more strict)
        debug: If True, print similarity scores for debugging
    
    Returns:
        True if the idea is too similar to any existing idea, False otherwise
    """
    if not new_title or not existing_ideas:
        return False
    
    # Generate embedding for new title
    new_embedding = embedding_service.generate_embedding(new_title)
    if not new_embedding:
        return False  # If embedding generation fails, allow the idea (fail open)
    
    # Check against existing ideas using vector similarity search
    # CosineDistance: 0 = identical, 2 = opposite
    # Based on find_similar_post_ideas_embeddings.py: similarity = 1.0 - distance
    # So if similarity_threshold = 0.92, we need distance <= 0.08
    # max_distance = 1.0 - similarity_threshold
    # If similarity_threshold = 0.92, max_distance = 1.0 - 0.92 = 0.08
    max_distance = 1.0 - similarity_threshold
    
    # Use database vector search for efficiency
    similar_ideas = PostIdea.objects.filter(
        title_embedding__isnull=False,
        id__in=[idea.id for idea in existing_ideas]
    ).annotate(
        distance=CosineDistance('title_embedding', new_embedding)
    ).filter(
        distance__lte=max_distance
    ).order_by('distance')[:5]  # Get top 5 most similar for debugging
    
    if similar_ideas.exists():
        if debug:
            print(f"  [DEBUG] '{new_title}' flagged as similar (threshold: {similarity_threshold:.2f}, max_distance: {max_distance:.3f}):")
            for idea in similar_ideas:
                similarity = max(0.0, min(1.0, 1.0 - float(idea.distance)))  # Convert distance back to similarity
                print(f"    - Distance: {idea.distance:.3f}, Similarity: {similarity:.3f} ({similarity*100:.1f}%) to: {idea.title}")
        return True
    
    return False

