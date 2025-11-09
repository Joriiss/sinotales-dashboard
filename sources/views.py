from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.views.decorators.http import require_http_methods
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.db.models.functions import Length
from .models import Source, Content
from .forms import SourceForm, ContentForm


class CustomLoginView(LoginView):
    """Custom login view with redirect to sources list"""
    template_name = 'registration/login.html'
    redirect_authenticated_user = True


class CustomLogoutView(LogoutView):
    """Custom logout view"""
    next_page = 'login'


@login_required
def dashboard(request):
    """Dashboard homepage with statistics"""
    # Basic counts
    total_sources = Source.objects.count()
    total_contents = Content.objects.count()
    active_sources = Source.objects.filter(is_active=True).count()
    
    # Content breakdown by type
    content_by_type = Content.objects.values('content_type').annotate(
        count=Count('id')
    ).order_by('-count')
    
    # Source breakdown by type
    sources_by_type = Source.objects.values('source_type').annotate(
        count=Count('id')
    ).order_by('-count')
    
    # Content with text
    contents_with_text = Content.objects.filter(has_content=True).count()
    contents_processed = Content.objects.filter(processed=True).count()
    
    # Calculate total words and data size
    # Use database aggregation for better performance
    total_chars_result = Content.objects.filter(has_content=True).aggregate(
        total_length=Sum(Length('content'))
    )
    total_chars = total_chars_result['total_length'] or 0
    
    # Estimate: ~5 chars per word on average
    total_words = total_chars // 5 if total_chars else 0
    # Estimate: UTF-8 encoding, average 2 bytes per character
    total_mb = (total_chars * 2) / (1024 * 1024) if total_chars else 0
    
    # Language breakdown
    sources_by_language = Source.objects.values('language').annotate(
        count=Count('id')
    ).order_by('-count')
    
    # Recent activity
    recent_contents = Content.objects.select_related('source').prefetch_related('tags').order_by('-created_at')[:10]
    recent_sources = Source.objects.order_by('-created_at')[:5]
    
    # Content by source (top sources)
    top_sources = Source.objects.annotate(
        content_count=Count('contents')
    ).filter(content_count__gt=0).order_by('-content_count')[:10]
    
    context = {
        'total_sources': total_sources,
        'total_contents': total_contents,
        'active_sources': active_sources,
        'contents_with_text': contents_with_text,
        'contents_processed': contents_processed,
        'content_by_type': content_by_type,
        'sources_by_type': sources_by_type,
        'sources_by_language': sources_by_language,
        'total_words': total_words,
        'total_mb': total_mb,
        'recent_contents': recent_contents,
        'recent_sources': recent_sources,
        'top_sources': top_sources,
    }
    return render(request, 'sources/dashboard.html', context)


@login_required
def source_list(request):
    """Display list of all sources"""
    # Annotate sources with content counts
    sources = Source.objects.annotate(
        total_contents=Count('contents'),
        contents_with_text=Count('contents', filter=Q(contents__has_content=True)),
        processed_contents=Count('contents', filter=Q(contents__processed=True)),
    ).all()
    
    context = {
        'sources': sources,
    }
    return render(request, 'sources/source_list.html', context)


@login_required
def source_add(request):
    """Add a new source"""
    if request.method == 'POST':
        form = SourceForm(request.POST)
        if form.is_valid():
            source = form.save()
            messages.success(request, f'Source "{source.name}" added successfully!')
            return redirect('sources:source_list')
    else:
        form = SourceForm()
    
    context = {
        'form': form,
        'action': 'Add',
    }
    return render(request, 'sources/source_form.html', context)


@login_required
def source_edit(request, pk):
    """Edit an existing source"""
    source = get_object_or_404(Source, pk=pk)
    
    if request.method == 'POST':
        form = SourceForm(request.POST, instance=source)
        if form.is_valid():
            source = form.save()
            messages.success(request, f'Source "{source.name}" updated successfully!')
            return redirect('sources:source_list')
    else:
        form = SourceForm(instance=source)
    
    context = {
        'form': form,
        'source': source,
        'action': 'Edit',
    }
    return render(request, 'sources/source_form.html', context)


@login_required
@require_http_methods(["POST"])
def source_delete(request, pk):
    """Delete a source"""
    source = get_object_or_404(Source, pk=pk)
    source_name = source.name
    source.delete()
    messages.success(request, f'Source "{source_name}" deleted successfully!')
    return redirect('sources:source_list')


# Content Views
@login_required
def content_list(request):
    """Display list of all contents"""
    contents = Content.objects.select_related('source').prefetch_related('tags').all()
    
    # Filtering
    source_filter = request.GET.get('source', '').strip()
    content_type_filter = request.GET.get('content_type', '').strip()
    has_content_filter = request.GET.get('has_content', '').strip()
    processed_filter = request.GET.get('processed', '').strip()
    search_query = request.GET.get('search', '').strip()
    
    if source_filter:
        contents = contents.filter(source_id=source_filter)
    if content_type_filter:
        contents = contents.filter(content_type=content_type_filter)
    if has_content_filter:
        contents = contents.filter(has_content=(has_content_filter == 'true'))
    if processed_filter:
        contents = contents.filter(processed=(processed_filter == 'true'))
    if search_query:
        contents = contents.filter(
            title__icontains=search_query
        ) | contents.filter(
            external_id__icontains=search_query
        )
    
    # Pagination
    paginator = Paginator(contents, 25)  # Show 25 contents per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'contents': page_obj,
        'sources': Source.objects.all(),
        'source_filter': source_filter,
        'content_type_filter': content_type_filter,
        'has_content_filter': has_content_filter,
        'processed_filter': processed_filter,
        'search_query': search_query,
    }
    return render(request, 'sources/content_list.html', context)


@login_required
def content_add(request):
    """Add a new content"""
    if request.method == 'POST':
        form = ContentForm(request.POST)
        if form.is_valid():
            content = form.save()
            messages.success(request, f'Content "{content.title}" added successfully!')
            return redirect('sources:content_list')
    else:
        form = ContentForm()
    
    context = {
        'form': form,
        'action': 'Add',
    }
    return render(request, 'sources/content_form.html', context)


@login_required
def content_edit(request, pk):
    """Edit an existing content"""
    content = get_object_or_404(Content, pk=pk)
    
    if request.method == 'POST':
        form = ContentForm(request.POST, instance=content)
        if form.is_valid():
            content = form.save()
            messages.success(request, f'Content "{content.title}" updated successfully!')
            return redirect('sources:content_list')
    else:
        form = ContentForm(instance=content)
    
    context = {
        'form': form,
        'content': content,
        'action': 'Edit',
    }
    return render(request, 'sources/content_form.html', context)


@login_required
def content_detail(request, pk):
    """View content details"""
    content = get_object_or_404(Content.objects.select_related('source').prefetch_related('tags'), pk=pk)
    context = {
        'content': content,
    }
    return render(request, 'sources/content_detail.html', context)


@login_required
@require_http_methods(["POST"])
def content_delete(request, pk):
    """Delete a content"""
    content = get_object_or_404(Content, pk=pk)
    content_title = content.title
    content.delete()
    messages.success(request, f'Content "{content_title}" deleted successfully!')
    return redirect('sources:content_list')
