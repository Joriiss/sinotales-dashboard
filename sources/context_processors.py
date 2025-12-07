from django.core.cache import cache
from .models import PostIdea


def post_idea_count(request):
    """Context processor to add post idea count to all templates"""
    if request.user.is_authenticated:
        # Cache the count for 5 minutes to avoid querying on every request
        cache_key = 'post_idea_count'
        count = cache.get(cache_key)
        if count is None:
            count = PostIdea.objects.count()
            cache.set(cache_key, count, 300)  # Cache for 5 minutes
        return {'post_idea_count': count}
    return {'post_idea_count': 0}

