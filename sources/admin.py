from django.contrib import admin
from .models import Source, Content, Tag, ContentChunk


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
    readonly_fields = ['created_at']
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


