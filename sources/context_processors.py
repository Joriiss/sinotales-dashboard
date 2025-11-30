from .models import PostIdea


def post_idea_count(request):
    """Context processor to add post idea count to all templates"""
    if request.user.is_authenticated:
        count = PostIdea.objects.count()
        return {'post_idea_count': count}
    return {'post_idea_count': 0}

