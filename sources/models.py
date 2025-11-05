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
        help_text="YouTube channel ID (required for YouTube sources)"
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


class Content(models.Model):
    """
    Represents content collected from sources (videos, blog posts, ebooks, etc.)
    """
    
    CONTENT_TYPE_CHOICES = [
        ('video', 'Video'),
        ('blog_post', 'Blog Post'),
        ('ebook', 'Ebook'),
    ]
    
    # Foreign key to source
    source = models.ForeignKey(
        Source,
        on_delete=models.CASCADE,
        related_name='contents',
        help_text="Source this content came from"
    )
    
    # External identifiers
    external_id = models.CharField(
        max_length=255,
        help_text="External ID (e.g., video_id, blog post slug, etc.)"
    )
    title = models.CharField(
        max_length=500,
        help_text="Title of the content"
    )
    link = models.URLField(
        max_length=500,
        validators=[URLValidator()],
        help_text="URL to the content"
    )
    
    # Content type and date
    content_type = models.CharField(
        max_length=20,
        choices=CONTENT_TYPE_CHOICES,
        default='video',
        help_text="Type of content"
    )
    date = models.DateField(
        help_text="Date when content was published/uploaded"
    )
    
    # Content text
    content = models.TextField(
        blank=True,
        help_text="Text content (transcript, article text, etc.)"
    )
    
    # Content status flags
    has_content = models.BooleanField(
        default=False,
        help_text="Whether content text is available (automatically set)"
    )
    processed = models.BooleanField(
        default=False,
        help_text="Whether content has been processed (embedded, etc.)"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'contents'
        ordering = ['-date', '-created_at']
        indexes = [
            models.Index(fields=['source']),
            models.Index(fields=['external_id']),
            models.Index(fields=['content_type']),
            models.Index(fields=['date']),
            models.Index(fields=['has_content']),
            models.Index(fields=['processed']),
        ]
        unique_together = [['source', 'external_id']]
    
    def save(self, *args, **kwargs):
        # Automatically set has_content based on whether content field has text
        self.has_content = bool(self.content and self.content.strip())
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.title} ({self.get_content_type_display()})"


