from django.contrib import admin
from django.core.exceptions import ImproperlyConfigured
from django.utils.safestring import mark_safe

try:
    from .models import Source, Content, Tag, ContentChunk, PostIdea, ScheduledPostIdeaGeneration, BlogPost, BlogPostImage
except ImportError as e:
    raise ImproperlyConfigured(f"Error importing models in admin.py: {e}")


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'created_at']
    list_filter = ['created_at']
    search_fields = ['name', 'description']
    readonly_fields = ['created_at']
    prepopulated_fields = {'slug': ('name',)}
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'slug', 'description')
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',),
        }),
    )


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'source_type', 'language', 'is_active', 'last_collected', 'created_at']
    list_filter = ['source_type', 'language', 'is_active', 'created_at']
    search_fields = ['name', 'link', 'channel_id']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'source_type', 'link', 'language')
        }),
        ('YouTube Specific', {
            'fields': ('channel_id', 'include_shorts'),
            'classes': ('collapse',),
        }),
        ('Status', {
            'fields': ('is_active', 'last_collected')
        }),
        ('Metadata', {
            'fields': ('metadata',),
            'classes': ('collapse',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


class ContentChunkInline(admin.TabularInline):
    """Inline admin for content chunks"""
    model = ContentChunk
    extra = 0
    readonly_fields = ['chunk_index', 'text', 'created_at', 'embedding_status']
    fields = ['chunk_index', 'text', 'embedding_status', 'created_at']
    can_delete = False
    show_change_link = True
    
    def embedding_status(self, obj):
        """Check if chunk has embedding"""
        return obj.embedding is not None
    embedding_status.boolean = True
    embedding_status.short_description = 'Has Embedding'


@admin.register(ContentChunk)
class ContentChunkAdmin(admin.ModelAdmin):
    list_display = ['content', 'chunk_index', 'text_preview', 'has_embedding', 'created_at']
    list_filter = ['created_at', 'content__source']
    search_fields = ['content__title', 'text']
    readonly_fields = ['created_at', 'has_embedding']
    raw_id_fields = ['content']
    
    fieldsets = (
        ('Content', {
            'fields': ('content', 'chunk_index')
        }),
        ('Chunk Data', {
            'fields': ('text', 'has_embedding')
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',),
        }),
    )
    
    def text_preview(self, obj):
        """Show preview of chunk text"""
        return obj.text[:100] + '...' if len(obj.text) > 100 else obj.text
    text_preview.short_description = 'Text Preview'
    
    def has_embedding(self, obj):
        """Check if chunk has embedding"""
        return obj.embedding is not None
    has_embedding.boolean = True
    has_embedding.short_description = 'Has Embedding'


@admin.register(Content)
class ContentAdmin(admin.ModelAdmin):
    list_display = ['title', 'source', 'content_type', 'date', 'has_content', 'processed', 'chunks_count', 'created_at']
    list_filter = ['content_type', 'has_content', 'processed', 'date', 'created_at', 'source', 'tags']
    search_fields = ['title', 'external_id', 'link', 'content']
    readonly_fields = ['has_content', 'created_at', 'updated_at', 'chunks_count']
    raw_id_fields = ['source']
    filter_horizontal = ['tags']
    inlines = [ContentChunkInline]
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('source', 'title', 'link', 'external_id', 'content_type', 'date')
        }),
        ('Content', {
            'fields': ('content',),
        }),
        ('Tags', {
            'fields': ('tags',),
        }),
        ('Status', {
            'fields': ('has_content', 'processed', 'chunks_count')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
    
    def chunks_count(self, obj):
        """Count of chunks for this content"""
        return obj.chunks.count()
    chunks_count.short_description = 'Chunks'


@admin.register(PostIdea)
class PostIdeaAdmin(admin.ModelAdmin):
    list_display = ['title', 'primary_keyword', 'description_preview', 'blog_posts_count', 'created_at', 'updated_at']
    list_filter = ['created_at', 'updated_at']
    search_fields = ['title', 'description', 'primary_keyword']
    readonly_fields = ['created_at', 'updated_at', 'blog_posts_count', 'blog_posts_list']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('title', 'primary_keyword', 'description')
        }),
        ('Related Blog Posts', {
            'fields': ('blog_posts_count', 'blog_posts_list'),
            'classes': ('collapse',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
    
    def description_preview(self, obj):
        """Show preview of description"""
        if obj.description:
            return obj.description[:50] + '...' if len(obj.description) > 50 else obj.description
        return '-'
    description_preview.short_description = 'Description'
    
    def blog_posts_count(self, obj):
        """Count of blog posts created from this idea"""
        return obj.blog_posts.count()
    blog_posts_count.short_description = 'Blog Posts'
    
    def blog_posts_list(self, obj):
        """List of blog posts created from this idea"""
        posts = obj.blog_posts.all()
        if posts:
            return '\n'.join([f'• {post.title}' for post in posts])
        return 'No blog posts created yet'
    blog_posts_list.short_description = 'Blog Posts List'


@admin.register(ScheduledPostIdeaGeneration)
class ScheduledPostIdeaGenerationAdmin(admin.ModelAdmin):
    list_display = ['enabled', 'trigger_time', 'num_ideas', 'provider', 'model', 'last_run', 'updated_at']
    list_filter = ['enabled', 'provider']
    readonly_fields = ['created_at', 'updated_at', 'last_run']
    
    fieldsets = (
        ('Schedule Settings', {
            'fields': ('enabled', 'trigger_time', 'num_ideas')
        }),
        ('AI Settings', {
            'fields': ('provider', 'model')
        }),
        ('Status', {
            'fields': ('last_run',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
    
    def has_add_permission(self, request):
        # Only allow one instance
        return not ScheduledPostIdeaGeneration.objects.exists()
    
    def has_delete_permission(self, request, obj=None):
        # Don't allow deletion
        return False


@admin.register(BlogPostImage)
class BlogPostImageAdmin(admin.ModelAdmin):
    list_display = ['blog_post', 'filename', 'is_featured', 'has_file', 'created_at']
    list_filter = ['is_featured', 'created_at']
    search_fields = ['blog_post__title', 'filename', 'alt_text']
    readonly_fields = ['created_at', 'updated_at', 'has_file']
    raw_id_fields = ['blog_post']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('blog_post', 'filename', 'is_featured')
        }),
        ('Image', {
            'fields': ('image_file', 'alt_text', 'has_file')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
    
    def has_file(self, obj):
        """Check if image file exists"""
        return bool(obj.image_file)
    has_file.boolean = True
    has_file.short_description = 'Has File'


@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    list_display = ['title', 'slug', 'post_idea', 'published', 'meta_title', 'tags_display', 'has_faq', 'created_at', 'updated_at']
    list_filter = ['published', 'created_at', 'updated_at', 'tags', 'post_idea']
    search_fields = ['title', 'slug', 'content', 'meta_title', 'meta_description', 'post_idea__title', 'featured_image_description', 'faq_title']
    readonly_fields = ['created_at', 'updated_at', 'faq_display']
    prepopulated_fields = {'slug': ('title',)}
    filter_horizontal = ['tags']
    raw_id_fields = ['post_idea']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('title', 'slug', 'post_idea', 'published')
        }),
        ('Content', {
            'fields': ('content',)
        }),
        ('Featured Image', {
            'fields': ('featured_image', 'featured_image_description')
        }),
        ('SEO', {
            'fields': ('meta_title', 'meta_description')
        }),
        ('FAQ', {
            'fields': ('faq_title', 'faq', 'faq_display')
        }),
        ('Tags', {
            'fields': ('tags',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
    
    def tags_display(self, obj):
        """Display tags as comma-separated list"""
        return ', '.join([tag.name for tag in obj.tags.all()[:5]])
    tags_display.short_description = 'Tags'
    
    def has_faq(self, obj):
        """Check if blog post has FAQs"""
        return bool(obj.faq and len(obj.faq) > 0)
    has_faq.boolean = True
    has_faq.short_description = 'Has FAQ'
    
    def faq_display(self, obj):
        """Display FAQs in a readable format"""
        if not obj.faq or not isinstance(obj.faq, list) or len(obj.faq) == 0:
            return 'No FAQs'
        
        import json
        try:
            formatted = json.dumps(obj.faq, indent=2, ensure_ascii=False)
            html = f'<pre style="white-space: pre-wrap; font-family: monospace; background: #f5f5f5; padding: 10px; border-radius: 4px;">{formatted}</pre>'
            return mark_safe(html)
        except Exception:
            return str(obj.faq)
    faq_display.short_description = 'FAQ Preview'


