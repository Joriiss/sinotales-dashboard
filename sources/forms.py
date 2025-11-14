from django import forms
from django.utils.text import slugify
from .models import Source, Content, Settings
from django.core.files.uploadedfile import InMemoryUploadedFile


class SourceForm(forms.ModelForm):
    class Meta:
        model = Source
        fields = [
            'name',
            'source_type',
            'link',
            'language',
            'publication_date',
            'channel_id',
            'include_shorts',
            'filter_videos',
            'xml_feed',
            'sitemap',
            'blog_only',
            'filter_china',
            'ebook_file',
            'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter source name'
            }),
            'source_type': forms.Select(attrs={
                'class': 'form-control',
            }),
            'link': forms.URLInput(attrs={
                'class': 'form-control',
                'placeholder': 'https://www.youtube.com/@channelname/ (optional for ebooks)'
            }),
            'language': forms.Select(attrs={
                'class': 'form-control',
            }),
            'publication_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'ebook_file': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': '.txt'
            }),
            'channel_id': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'UC1UNB6Gy11umcbEj_hqIwhw'
            }),
            'include_shorts': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
            }),
            'filter_videos': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
            }),
            'xml_feed': forms.URLInput(attrs={
                'class': 'form-control',
                'placeholder': 'https://example.com/feed.xml'
            }),
            'sitemap': forms.URLInput(attrs={
                'class': 'form-control',
                'placeholder': 'https://example.com/sitemap.xml'
            }),
            'blog_only': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
            }),
            'filter_china': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
            }),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make channel_id required for YouTube sources
        source_type = self.data.get('source_type') if self.data else (self.instance.source_type if self.instance.pk else None)
        
        # Make link required for blog sources, optional for others
        if source_type == 'blog':
            self.fields['link'].required = True
            self.fields['sitemap'].required = True
        else:
            self.fields['link'].required = False
            self.fields['sitemap'].required = False
        
        # Make ebook_file and publication_date required for ebook sources
        if source_type == 'ebook':
            # ebook_file is only required if it doesn't already exist
            if not (self.instance and self.instance.pk and self.instance.ebook_file):
                self.fields['ebook_file'].required = True
            else:
                self.fields['ebook_file'].required = False
            self.fields['publication_date'].required = True
            self.fields['link'].required = False
        else:
            self.fields['ebook_file'].required = False
            self.fields['publication_date'].required = False
        
        if source_type == 'youtube':
            self.fields['channel_id'].required = True
            self.fields['channel_id'].widget.attrs['placeholder'] = 'UC1UNB6Gy11umcbEj_hqIwhw'
            # Disable blog-specific fields for YouTube sources
            if self.instance and self.instance.pk:
                self.fields['xml_feed'].widget.attrs['disabled'] = True
                self.fields['sitemap'].widget.attrs['disabled'] = True
                self.fields['blog_only'].widget.attrs['disabled'] = True
                self.fields['filter_china'].widget.attrs['disabled'] = True
        elif source_type == 'blog':
            # Hide channel_id for blog sources - it won't be used
            self.fields['channel_id'].required = False
            # Disable YouTube-specific fields for blog sources
            if self.instance and self.instance.pk:
                self.fields['channel_id'].widget.attrs['disabled'] = True
                self.fields['include_shorts'].widget.attrs['disabled'] = True
                self.fields['filter_videos'].widget.attrs['disabled'] = True
        elif source_type == 'ebook':
            # Hide channel_id and blog fields for ebook sources
            self.fields['channel_id'].required = False
            # Disable YouTube and blog-specific fields for ebook sources
            if self.instance and self.instance.pk:
                self.fields['channel_id'].widget.attrs['disabled'] = True
                self.fields['include_shorts'].widget.attrs['disabled'] = True
                self.fields['filter_videos'].widget.attrs['disabled'] = True
                self.fields['xml_feed'].widget.attrs['disabled'] = True
                self.fields['sitemap'].widget.attrs['disabled'] = True
                self.fields['blog_only'].widget.attrs['disabled'] = True
                self.fields['filter_china'].widget.attrs['disabled'] = True
        else:
            self.fields['channel_id'].required = False
            self.fields['channel_id'].widget.attrs['placeholder'] = 'UC1UNB6Gy11umcbEj_hqIwhw'
            # For non-YouTube, non-blog, non-ebook sources, disable all type-specific fields
            if self.instance and self.instance.pk:
                self.fields['channel_id'].widget.attrs['disabled'] = True
                self.fields['include_shorts'].widget.attrs['disabled'] = True
                self.fields['filter_videos'].widget.attrs['disabled'] = True
                self.fields['xml_feed'].widget.attrs['disabled'] = True
                self.fields['sitemap'].widget.attrs['disabled'] = True
                self.fields['blog_only'].widget.attrs['disabled'] = True
                self.fields['filter_china'].widget.attrs['disabled'] = True
                self.fields['ebook_file'].widget.attrs['disabled'] = True
                self.fields['publication_date'].widget.attrs['disabled'] = True
    
    def clean(self):
        cleaned_data = super().clean()
        source_type = cleaned_data.get('source_type')
        channel_id = cleaned_data.get('channel_id')
        
        # Validate that channel_id is required for YouTube sources
        if source_type == 'youtube' and not channel_id:
            self.add_error('channel_id', 'Channel ID is required for YouTube sources.')
        
        # Validate that link and sitemap are required for blog sources
        if source_type == 'blog':
            link = cleaned_data.get('link')
            sitemap = cleaned_data.get('sitemap')
            if not link:
                self.add_error('link', 'Link is required for blog sources.')
            if not sitemap:
                self.add_error('sitemap', 'Sitemap is required for blog sources.')
        
        # Validate that ebook_file and publication_date are required for ebook sources
        if source_type == 'ebook':
            ebook_file = cleaned_data.get('ebook_file')
            publication_date = cleaned_data.get('publication_date')
            # Check if file is being uploaded (new file) or already exists (existing source)
            # For new sources, file is required. For existing sources, file is optional if already uploaded.
            if not ebook_file:
                if not self.instance or not self.instance.pk:
                    # New source - file is required
                    self.add_error('ebook_file', 'Ebook file is required for ebook sources.')
                elif not self.instance.ebook_file:
                    # Existing source but no file uploaded yet - file is required
                    self.add_error('ebook_file', 'Ebook file is required for ebook sources.')
            if not publication_date:
                self.add_error('publication_date', 'Publication date is required for ebook sources.')
        
        # Clear channel_id for non-YouTube sources
        if source_type != 'youtube' and channel_id:
            cleaned_data['channel_id'] = ''
            if self.instance and self.instance.pk:
                self.instance.channel_id = None
        
        # Clear blog-specific fields for non-blog sources
        if source_type != 'blog':
            if cleaned_data.get('xml_feed'):
                cleaned_data['xml_feed'] = ''
            if cleaned_data.get('sitemap'):
                cleaned_data['sitemap'] = ''
            if cleaned_data.get('blog_only'):
                cleaned_data['blog_only'] = False
            if cleaned_data.get('filter_china'):
                cleaned_data['filter_china'] = False
        
        # Clear YouTube-specific fields for non-YouTube sources
        if source_type != 'youtube':
            if cleaned_data.get('include_shorts'):
                cleaned_data['include_shorts'] = False
            if cleaned_data.get('filter_videos'):
                cleaned_data['filter_videos'] = False
        
        return cleaned_data


class ContentForm(forms.ModelForm):
    class Meta:
        model = Content
        fields = [
            'source',
            'external_id',
            'title',
            'link',
            'content_type',
            'date',
            'content',
            'tags',
            'processed',
        ]
        widgets = {
            'source': forms.Select(attrs={
                'class': 'form-control',
            }),
            'external_id': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., video_id, blog slug'
            }),
            'title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Content title'
            }),
            'link': forms.URLInput(attrs={
                'class': 'form-control',
                'placeholder': 'https://www.youtube.com/watch?v=... (optional for ebooks)'
            }),
            'content_type': forms.Select(attrs={
                'class': 'form-control',
            }),
            'date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'content': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 10,
                'placeholder': 'Transcript, article text, etc.'
            }),
            'tags': forms.SelectMultiple(attrs={
                'class': 'form-control',
            }),
            'processed': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
            }),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make link optional (especially for ebooks)
        self.fields['link'].required = False
        
        # Make external_id conditionally required based on content_type
        # Get content_type from data or instance
        content_type = None
        if self.data:
            content_type = self.data.get('content_type')
        elif self.instance and self.instance.pk:
            content_type = self.instance.content_type
        
        # Only require external_id for videos (YouTube videos need video ID)
        if content_type == 'video':
            self.fields['external_id'].required = True
            self.fields['external_id'].widget.attrs['placeholder'] = 'e.g., video_id (required for videos)'
        else:
            # For blog posts and ebooks, external_id is optional (will be auto-generated)
            self.fields['external_id'].required = False
            self.fields['external_id'].widget.attrs['placeholder'] = 'e.g., blog-slug (optional, will be auto-generated from title if empty)'
    
    def clean(self):
        cleaned_data = super().clean()
        content_type = cleaned_data.get('content_type')
        external_id = cleaned_data.get('external_id', '').strip()
        title = cleaned_data.get('title', '').strip()
        
        # For videos, external_id is required
        if content_type == 'video':
            if not external_id:
                self.add_error('external_id', 'External ID is required for video content.')
        else:
            # For blog posts and ebooks, auto-generate external_id from title if not provided
            if not external_id:
                if title:
                    # Generate a slug-based external_id from title
                    base_slug = slugify(title)
                    if not base_slug:
                        # Fallback if slugify returns empty (e.g., only special chars)
                        base_slug = f"content-{cleaned_data.get('date', 'unknown')}"
                    
                    # Ensure uniqueness by checking if it exists
                    source = cleaned_data.get('source')
                    if source:
                        counter = 1
                        unique_slug = base_slug
                        while Content.objects.filter(source=source, external_id=unique_slug).exclude(pk=self.instance.pk if self.instance else None).exists():
                            unique_slug = f"{base_slug}-{counter}"
                            counter += 1
                        cleaned_data['external_id'] = unique_slug
                    else:
                        cleaned_data['external_id'] = base_slug
                else:
                    # If no title either, we'll need to handle this in save() or raise error
                    # But title should be required by the model, so this shouldn't happen
                    pass
        
        return cleaned_data


class SettingsForm(forms.ModelForm):
    class Meta:
        model = Settings
        fields = [
            'default_tagging_provider',
            'default_tagging_model',
            'default_embedding_provider',
        ]
        widgets = {
            'default_tagging_provider': forms.Select(attrs={
                'class': 'form-control',
            }),
            'default_tagging_model': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., gpt-oss:20b-cloud, llama3.2:latest'
            }),
            'default_embedding_provider': forms.Select(attrs={
                'class': 'form-control',
            }),
        }
        help_texts = {
            'default_tagging_provider': 'Provider for AI-powered content tagging',
            'default_tagging_model': 'Model name for tagging (Ollama: e.g., gpt-oss:20b-cloud, llama3.2:latest | OpenAI: e.g., gpt-3.5-turbo)',
            'default_embedding_provider': 'Provider for generating content embeddings',
        }


