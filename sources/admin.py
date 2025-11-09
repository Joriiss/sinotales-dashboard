from django.contrib import admin
from .models import Source, Content, Tag


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


@admin.register(Content)
class ContentAdmin(admin.ModelAdmin):
    list_display = ['title', 'source', 'content_type', 'date', 'has_content', 'processed', 'created_at']
    list_filter = ['content_type', 'has_content', 'processed', 'date', 'created_at', 'source', 'tags']
    search_fields = ['title', 'external_id', 'link', 'content']
    readonly_fields = ['has_content', 'created_at', 'updated_at']
    raw_id_fields = ['source']
    filter_horizontal = ['tags']
    
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
            'fields': ('has_content', 'processed')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


