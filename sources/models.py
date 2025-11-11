from django.db import models
from django.core.validators import URLValidator
from django.utils.text import slugify
from pgvector.django import VectorField


class Tag(models.Model):
    """
    Tags for categorizing and retrieving content
    """
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Tag name (e.g., 'culture', 'history', 'food')"
    )
    slug = models.SlugField(
        max_length=100,
        unique=True,
        help_text="URL-friendly version of the tag"
    )
    description = models.TextField(
        blank=True,
        help_text="Optional description of what this tag represents"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'tags'
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['slug']),
        ]
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)
            self.slug = base_slug
            # Handle slug collisions by appending a number
            counter = 1
            while Tag.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{base_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)


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
        blank=True,
        null=True,
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
        blank=True,
        null=True,
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
    
    # Tags
    tags = models.ManyToManyField(
        Tag,
        related_name='contents',
        blank=True,
        help_text="Tags for categorizing this content"
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


class ContentChunk(models.Model):
    """
    Represents a chunk of content with its vector embedding for semantic search
    """
    content = models.ForeignKey(
        Content,
        on_delete=models.CASCADE,
        related_name='chunks',
        help_text="Content this chunk belongs to"
    )
    chunk_index = models.IntegerField(
        help_text="Order of this chunk within the content (0-based)"
    )
    text = models.TextField(
        help_text="Text content of this chunk"
    )
    embedding = VectorField(
        dimensions=1536,  # text-embedding-3-small dimensions (supports vector indexes)
        null=True,
        blank=True,
        help_text="Vector embedding for semantic search"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'content_chunks'
        ordering = ['content', 'chunk_index']
        indexes = [
            models.Index(fields=['content', 'chunk_index']),
            # Note: Vector index will be created in migration using raw SQL
        ]
        unique_together = [['content', 'chunk_index']]
    
    def __str__(self):
        return f"Chunk {self.chunk_index} of {self.content.title}"


class ActivityLog(models.Model):
    """
    Logs of activities performed in the system
    """
    ACTIVITY_TYPE_CHOICES = [
        ('source_created', 'Source Created'),
        ('source_updated', 'Source Updated'),
        ('source_deleted', 'Source Deleted'),
        ('content_created', 'Content Created'),
        ('content_updated', 'Content Updated'),
        ('content_deleted', 'Content Deleted'),
        ('content_tagged', 'Content Tagged'),
        ('embeddings_generated', 'Embeddings Generated'),
        ('tags_created', 'Tags Created'),
        ('import_completed', 'Import Completed'),
    ]
    
    activity_type = models.CharField(
        max_length=50,
        choices=ACTIVITY_TYPE_CHOICES,
        help_text="Type of activity"
    )
    description = models.TextField(
        help_text="Description of the activity"
    )
    user = models.CharField(
        max_length=150,
        blank=True,
        null=True,
        help_text="User who performed the activity (if applicable)"
    )
    # Optional foreign keys to related objects
    source = models.ForeignKey(
        Source,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='activity_logs',
        help_text="Related source (if applicable)"
    )
    content = models.ForeignKey(
        Content,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='activity_logs',
        help_text="Related content (if applicable)"
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional metadata (e.g., count of items processed)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'activity_logs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['activity_type']),
            models.Index(fields=['created_at']),
            models.Index(fields=['source']),
            models.Index(fields=['content']),
        ]
    
    def __str__(self):
        return f"{self.get_activity_type_display()} - {self.description[:50]}"


