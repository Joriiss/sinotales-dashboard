from django import forms
from .models import Source


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
                'placeholder': 'https://www.youtube.com/@channelname/'
            }),
            'language': forms.Select(attrs={
                'class': 'form-control',
            }),
            'channel_id': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'UC1UNB6Gy11umcbEj_hqIwhw (optional)'
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
        # Make channel_id required only for YouTube sources
        if self.instance and self.instance.pk:
            if self.instance.source_type != 'youtube':
                self.fields['channel_id'].widget.attrs['disabled'] = True
                self.fields['include_shorts'].widget.attrs['disabled'] = True

