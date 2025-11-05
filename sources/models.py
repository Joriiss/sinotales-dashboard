from django.db import models
from django.core.validators import URLValidator


class Source(models.Model):
    """
    Represents a source of content (YouTube channel, blog, ebook, etc.)
    """
    
    SOURCE_TYPE_CHOICES = [
        ('youtube', 'YouTube Channel'),
        ('blog', 'Blog'),
        ('ebook', 'Ebook'),
        ('rss', 'RSS Feed'),
    ]
    
    LANGUAGE_CHOICES = [
        ('en', 'English'),
        ('fr', 'French'),
        ('zh', 'Chinese'),
        ('es', 'Spanish'),
        ('de', 'German'),
        ('other', 'Other'),
    ]
    
    # Basic information
    name = models.CharField(max_length=255, help_text="Name of the source (e.g., channel name)")
    source_type = models.CharField(
        max_length=20,
        choices=SOURCE_TYPE_CHOICES,
        default='youtube',
        help_text="Type of content source"
    )
    link = models.URLField(
        max_length=500,
        validators=[URLValidator()],
        help_text="URL to the source (YouTube channel, blog URL, etc.)"
    )
    language = models.CharField(
        max_length=20,
        choices=LANGUAGE_CHOICES,
        default='en',
        help_text="Primary language of the content"
    )
    
    # YouTube-specific fields
    channel_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="YouTube channel ID (for YouTube sources)"
    )
    include_shorts = models.BooleanField(
        default=False,
        help_text="Include YouTube Shorts when collecting videos (YouTube sources only)"
    )
    
    # Metadata
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional metadata as JSON (e.g., description, tags, etc.)"
    )
    
    # Status tracking
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this source is currently active and being monitored"
    )
    last_collected = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Last time content was collected from this source"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'sources'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['source_type']),
            models.Index(fields=['language']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.get_source_type_display()})"
    
    def clean(self):
        """Validate that YouTube-specific fields are used only for YouTube sources"""
        from django.core.exceptions import ValidationError
        
        if self.source_type == 'youtube' and not self.channel_id:
            # Channel ID is optional but recommended for YouTube
            pass
        elif self.source_type != 'youtube':
            # Clear YouTube-specific fields for non-YouTube sources
            if self.channel_id:
                self.channel_id = None
            if self.include_shorts:
                self.include_shorts = False

