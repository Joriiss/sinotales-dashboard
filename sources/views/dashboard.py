from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Count, Q, Sum
from django.db.models.functions import Length
from django.core.cache import cache
from ..models import Source, Content, Tag, PostIdea, BlogPost, ContentChunk


@login_required
def dashboard(request):
    """Dashboard homepage with statistics"""
    # Cache dashboard statistics for 10 minutes to improve performance
    cache_key = 'dashboard_stats'
    cached_stats = cache.get(cache_key)
    
    if cached_stats is None:
        # Basic counts - combine where possible
        total_sources = Source.objects.count()
        total_contents = Content.objects.count()
        active_sources = Source.objects.filter(is_active=True).count()
    
        # Content breakdown by type
        content_by_type = list(Content.objects.values('content_type').annotate(
        count=Count('id')
        ).order_by('-count'))
    
        # Source breakdown by type
        sources_by_type = list(Source.objects.values('source_type').annotate(
        count=Count('id')
        ).order_by('-count'))
    
        # Content with text
        contents_with_text = Content.objects.filter(has_content=True).count()
        contents_processed = Content.objects.filter(processed=True).count()
    
        # Calculate total words and data size
        # Use database aggregation for better performance
        total_chars_result = Content.objects.filter(has_content=True).aggregate(
        total_length=Sum(Length('content'))
        )
        total_chars = total_chars_result['total_length'] or 0
    
        # Estimate: ~5 chars per word on average
        total_words = total_chars // 5 if total_chars else 0
        # Estimate: UTF-8 encoding, average 2 bytes per character
        total_mb = (total_chars * 2) / (1024 * 1024) if total_chars else 0
    
        # Language breakdown
        sources_by_language = list(Source.objects.values('language').annotate(
        count=Count('id')
        ).order_by('-count'))
    
        # Tags statistics
        total_tags = Tag.objects.count()
        contents_with_tags = Content.objects.filter(tags__isnull=False).distinct().count()
    
        # Chunks and embeddings statistics
        total_chunks = ContentChunk.objects.count()
        chunks_with_embeddings = ContentChunk.objects.filter(embedding__isnull=False).count()
        contents_with_embeddings = Content.objects.filter(
        chunks__embedding__isnull=False
        ).distinct().count()
    
        # Calculate embedding percentage
        embedding_percentage = (
        (chunks_with_embeddings / total_chunks * 100) 
        if total_chunks > 0 else 0
        )
    
        # Top tags
        top_tags = list(Tag.objects.annotate(
        content_count=Count('contents')
        ).filter(content_count__gt=0).order_by('-content_count')[:10])
    
        # Cache the expensive aggregations
        cached_stats = {
        'total_sources': total_sources,
        'total_contents': total_contents,
        'active_sources': active_sources,
        'content_by_type': content_by_type,
        'sources_by_type': sources_by_type,
            'contents_with_text': contents_with_text,
            'contents_processed': contents_processed,
        'total_words': total_words,
        'total_mb': total_mb,
            'sources_by_language': sources_by_language,
        'total_tags': total_tags,
        'contents_with_tags': contents_with_tags,
        'total_chunks': total_chunks,
        'chunks_with_embeddings': chunks_with_embeddings,
        'contents_with_embeddings': contents_with_embeddings,
        'embedding_percentage': embedding_percentage,
        'top_tags': top_tags,
        }
        cache.set(cache_key, cached_stats, 600)  # Cache for 10 minutes
    
    # Recent activity - don't cache, should be fresh
    recent_contents = Content.objects.select_related('source').prefetch_related('tags').order_by('-created_at')[:10]
    recent_sources = Source.objects.order_by('-created_at')[:5]
    
    # Content by source (top sources) - don't cache, should be fresh
    top_sources = Source.objects.annotate(
        content_count=Count('contents')
    ).filter(content_count__gt=0).order_by('-content_count')[:10]
    
    context = {
        **cached_stats,  # Unpack cached statistics
        'recent_contents': recent_contents,
        'recent_sources': recent_sources,
        'top_sources': top_sources,
    }
    return render(request, 'sources/dashboard.html', context)
