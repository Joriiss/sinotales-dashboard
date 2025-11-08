from django import forms
from .models import Source, Content


class SourceForm(forms.ModelForm):
    class Meta:
        model = Source
        fields = [
            'name',
            'source_type',
            'link',
            'language',
            'channel_id',
            'include_shorts',
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
            'channel_id': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'UC1UNB6Gy11umcbEj_hqIwhw'
            }),
            'include_shorts': forms.CheckboxInput(attrs={
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
        
        # Make link optional (especially for ebooks)
        self.fields['link'].required = False
        
        if source_type == 'youtube':
            self.fields['channel_id'].required = True
            self.fields['channel_id'].widget.attrs['placeholder'] = 'UC1UNB6Gy11umcbEj_hqIwhw'
        else:
            self.fields['channel_id'].required = False
            self.fields['channel_id'].widget.attrs['placeholder'] = 'UC1UNB6Gy11umcbEj_hqIwhw'
            # For non-YouTube sources, disable and clear channel_id
            if self.instance and self.instance.pk:
                self.fields['channel_id'].widget.attrs['disabled'] = True
                self.fields['include_shorts'].widget.attrs['disabled'] = True
    
    def clean(self):
        cleaned_data = super().clean()
        source_type = cleaned_data.get('source_type')
        channel_id = cleaned_data.get('channel_id')
        
        # Validate that channel_id is required for YouTube sources
        if source_type == 'youtube' and not channel_id:
            self.add_error('channel_id', 'Channel ID is required for YouTube sources.')
        
        # Clear channel_id for non-YouTube sources
        if source_type != 'youtube' and channel_id:
            cleaned_data['channel_id'] = ''
            if self.instance and self.instance.pk:
                self.instance.channel_id = None
        
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
            'processed': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
            }),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make link optional (especially for ebooks)
        self.fields['link'].required = False


