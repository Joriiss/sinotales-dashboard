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
    ]
    
    LANGUAGE_CHOICES = [
        ('en', 'English'),
        ('fr', 'French'),
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
    filter_videos = models.BooleanField(
        default=False,
        help_text="Filter videos to only include those relevant to China (YouTube sources only)"
    )
    
    # Blog-specific fields
    xml_feed = models.URLField(
        max_length=500,
        validators=[URLValidator()],
        blank=True,
        null=True,
        help_text="RSS/XML feed URL (optional, for blog sources)"
    )
    sitemap = models.URLField(
        max_length=500,
        validators=[URLValidator()],
        blank=True,
        null=True,
        help_text="Sitemap URL (for blog sources)"
    )
    blog_only = models.BooleanField(
        default=False,
        help_text="Only collect blog posts, exclude other content types (blog sources only)"
    )
    filter_china = models.BooleanField(
        default=False,
        help_text="Filter blog posts to only include those relevant to China (blog sources only)"
    )
    
    # Ebook-specific fields
    ebook_file = models.FileField(
        upload_to='ebooks/',
        blank=True,
        null=True,
        help_text="Upload ebook text file (for ebook sources)"
    )
    publication_date = models.DateField(
        blank=True,
        null=True,
        help_text="Publication date of the ebook (for ebook sources)"
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
        """Validate that source-specific fields are used only for appropriate source types"""
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
            if self.filter_videos:
                self.filter_videos = False
        
        if self.source_type != 'blog':
            # Clear blog-specific fields for non-blog sources
            if self.xml_feed:
                self.xml_feed = None
            if self.sitemap:
                self.sitemap = None
            if self.blog_only:
                self.blog_only = False
            if self.filter_china:
                self.filter_china = False
        
        if self.source_type != 'ebook':
            # Clear ebook-specific fields for non-ebook sources
            if self.ebook_file:
                # Note: We don't delete the file here to avoid data loss
                # File will be cleared on save if source_type changes
                pass
            if self.publication_date:
                self.publication_date = None


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
        ('settings_updated', 'Settings Updated'),
        ('transcript_fetched', 'Transcript Fetched'),
        ('content_fetched', 'Content Fetched'),
        ('post_idea_created', 'Post Idea Created'),
        ('post_idea_updated', 'Post Idea Updated'),
        ('post_idea_deleted', 'Post Idea Deleted'),
        ('post_ideas_generated', 'Post Ideas Generated'),
        ('blog_post_created', 'Blog Post Created'),
        ('blog_post_updated', 'Blog Post Updated'),
        ('blog_post_deleted', 'Blog Post Deleted'),
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


class PostIdea(models.Model):
    """
    Post ideas for future blog posts
    """
    title = models.CharField(
        max_length=255,
        help_text="Title or topic of the post idea"
    )
    description = models.TextField(
        blank=True,
        help_text="Optional description or notes about the post idea"
    )
    primary_keyword = models.CharField(
        max_length=100,
        blank=True,
        help_text="Primary SEO keyword for this post idea (e.g., 'Chengdu travel guide', 'China visa requirements')"
    )
    title_embedding = VectorField(
        dimensions=1536,  # text-embedding-3-small dimensions (same as ContentChunk)
        null=True,
        blank=True,
        help_text="Vector embedding of the title for semantic similarity checking"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'post_ideas'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self):
        return self.title


class Settings(models.Model):
    """
    Application settings (singleton pattern - only one instance)
    """
    # Tagging settings
    default_tagging_provider = models.CharField(
        max_length=20,
        choices=[
            ('ollama', 'Ollama'),
            ('openai', 'OpenAI'),
        ],
        default='ollama',
        help_text="Default provider for content tagging"
    )
    default_tagging_model = models.CharField(
        max_length=100,
        default='gpt-oss:20b-cloud',
        help_text="Default model for content tagging (e.g., 'gpt-oss:20b-cloud' for Ollama, 'gpt-3.5-turbo' for OpenAI)"
    )
    default_filtering_model = models.CharField(
        max_length=100,
        default='llama3.2:latest',
        help_text="Default Ollama model for filtering content (YouTube videos and blog posts) - e.g., 'llama3.2:latest', 'gpt-oss:20b-cloud'"
    )
    
    # Embedding settings (for future use)
    default_embedding_provider = models.CharField(
        max_length=20,
        choices=[
            ('openai', 'OpenAI'),
        ],
        default='openai',
        help_text="Default provider for embeddings"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'settings'
        verbose_name = 'Settings'
        verbose_name_plural = 'Settings'
    
    def __str__(self):
        return "Application Settings"
    
    @classmethod
    def get_settings(cls):
        """Get or create the singleton settings instance"""
        settings, created = cls.objects.get_or_create(pk=1)
        return settings
    
    def save(self, *args, **kwargs):
        """Ensure only one settings instance exists"""
        self.pk = 1
        super().save(*args, **kwargs)


class ScheduledPostIdeaGeneration(models.Model):
    """
    Settings for scheduled automatic post idea generation (singleton pattern)
    """
    enabled = models.BooleanField(
        default=False,
        help_text="Enable or disable scheduled post idea generation"
    )
    trigger_time = models.TimeField(
        default='09:00',
        help_text="Time of day to trigger generation (24-hour format, e.g., 09:00 for 9 AM)"
    )
    num_ideas = models.IntegerField(
        default=5,
        help_text="Number of post ideas to generate each time"
    )
    provider = models.CharField(
        max_length=20,
        choices=[
            ('ollama', 'Ollama'),
            ('openai', 'OpenAI'),
            ('gemini', 'Gemini'),
        ],
        default='ollama',
        help_text="AI provider to use for generation"
    )
    model = models.CharField(
        max_length=100,
        default='gpt-oss:20b-cloud',
        help_text="Model to use for generation (e.g., 'gpt-oss:20b-cloud' for Ollama, 'gpt-4o-mini' for OpenAI)"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_run = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time the scheduled generation was executed"
    )
    
    class Meta:
        db_table = 'scheduled_post_idea_generation'
        verbose_name = 'Scheduled Post Idea Generation'
        verbose_name_plural = 'Scheduled Post Idea Generation'
    
    def __str__(self):
        return f"Scheduled Generation ({'Enabled' if self.enabled else 'Disabled'})"
    
    @classmethod
    def get_settings(cls):
        """Get or create the singleton settings instance"""
        settings, created = cls.objects.get_or_create(pk=1)
        return settings
    
    def save(self, *args, **kwargs):
        """Ensure only one settings instance exists"""
        self.pk = 1
        super().save(*args, **kwargs)


class BlogPost(models.Model):
    """
    Blog posts for the China travel blog
    """
    title = models.CharField(
        max_length=255,
        help_text="Title of the blog post"
    )
    slug = models.SlugField(
        max_length=255,
        unique=True,
        help_text="URL-friendly version of the title (auto-generated if not provided)"
    )
    content = models.TextField(
        help_text="Full content of the blog post (HTML or Markdown)"
    )
    meta_title = models.CharField(
        max_length=60,
        blank=True,
        help_text="SEO meta title (recommended: 50-60 characters)"
    )
    meta_description = models.CharField(
        max_length=160,
        blank=True,
        help_text="SEO meta description (recommended: 150-160 characters)"
    )
    post_idea = models.ForeignKey(
        PostIdea,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='blog_posts',
        help_text="Post idea this blog post was created from (optional)"
    )
    published = models.BooleanField(
        default=False,
        help_text="Whether this blog post is published"
    )
    tags = models.ManyToManyField(
        Tag,
        related_name='blog_posts',
        blank=True,
        help_text="Tags for categorizing this blog post"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'blog_posts'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['-created_at']),
            models.Index(fields=['post_idea']),
            models.Index(fields=['published']),
        ]
    
    def __str__(self):
        return self.title
    
    def save(self, *args, **kwargs):
        """Auto-generate slug from title if not provided"""
        if not self.slug:
            base_slug = slugify(self.title)
            self.slug = base_slug
            # Handle slug collisions by appending a number
            counter = 1
            while BlogPost.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{base_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)


