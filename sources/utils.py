"""
Utility functions for the sources app
"""
from django.contrib.auth import get_user
from .models import ActivityLog


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

