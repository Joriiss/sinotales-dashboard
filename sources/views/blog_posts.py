from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.db.models.functions import Length
from django.http import JsonResponse
from django.conf import settings
from django.utils.html import json_script
from django.utils.text import slugify
from django.core.cache import cache
import json
from ..models import BlogPost, BlogPostImage, Tag, PostIdea
from ..forms import SourceForm
from ..utils import log_activity
from ..rag_service import RAGService
from ..content_processing_service import ContentProcessingService
import re

@login_required
def blog_post_list(request):
    """Display list of all blog posts"""
    # Optimize queryset: select_related for ForeignKey, prefetch_related for ManyToMany
    # Base queryset with optimizations
    base_queryset = BlogPost.objects.select_related('post_idea').prefetch_related('tags')
    
    # Get total count before filtering
    total_count = base_queryset.count()
    
    # Apply search filter
    search_query = request.GET.get('search', '').strip()
    if search_query:
        # For search, we need to include content field for searching
        blog_posts = base_queryset.filter(
            Q(title__icontains=search_query) | 
            Q(content__icontains=search_query) |
            Q(meta_title__icontains=search_query) |
            Q(meta_description__icontains=search_query)
        )
    else:
        # For non-search queries, defer the large content field
        blog_posts = base_queryset.defer('content')
    
    # Filter by published status if provided
    published_filter = request.GET.get('published', '').strip()
    if published_filter == 'true':
        blog_posts = blog_posts.filter(published=True)
    elif published_filter == 'false':
        blog_posts = blog_posts.filter(published=False)
    
    # Filter by tag if provided
    tag_id = request.GET.get('tag', '').strip()
    if tag_id:
        try:
            tag = Tag.objects.get(pk=tag_id)
            blog_posts = blog_posts.filter(tags=tag).distinct()
        except Tag.DoesNotExist:
            pass
    
    # Filter by post idea if provided
    post_idea_id = request.GET.get('post_idea', '').strip()
    if post_idea_id:
        try:
            post_idea = PostIdea.objects.get(pk=post_idea_id)
            blog_posts = blog_posts.filter(post_idea=post_idea)
        except PostIdea.DoesNotExist:
            pass
    
    # Apply sorting (default: newest first)
    sort_by = request.GET.get('sort', 'newest').strip()
    if sort_by == 'oldest':
        blog_posts = blog_posts.order_by('created_at')
    elif sort_by == 'title_asc':
        blog_posts = blog_posts.order_by('title')
    elif sort_by == 'title_desc':
        blog_posts = blog_posts.order_by('-title')
    elif sort_by == 'updated':
        blog_posts = blog_posts.order_by('-updated_at')
    else:
        # Default: newest first
        blog_posts = blog_posts.order_by('-created_at')
    
    # Pagination
    paginator = Paginator(blog_posts, 25)  # Show 25 blog posts per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Get filtered count from paginator
    filtered_count = paginator.count
    
    # Get all tags for filter dropdown (cached for 1 hour)
    cache_key = 'all_tags_list'
    all_tags = cache.get(cache_key)
    if all_tags is None:
        all_tags = list(Tag.objects.all().order_by('name'))
        cache.set(cache_key, all_tags, 3600)  # Cache for 1 hour
    
    context = {
        'blog_posts': page_obj,
        'page_obj': page_obj,
        'search_query': search_query,
        'sort_by': sort_by,
        'total_count': total_count,
        'filtered_count': filtered_count,
        'all_tags': all_tags,
        'selected_tag_id': tag_id,
        'selected_post_idea_id': post_idea_id,
        'published_filter': published_filter,
    }
    
    return render(request, 'sources/blog_post_list.html', context)


@login_required
def blog_post_detail(request, pk):
    """Display detail view of a blog post"""
    blog_post = get_object_or_404(BlogPost, pk=pk)
    
    context = {
        'blog_post': blog_post,
    }
    
    return render(request, 'sources/blog_post_detail.html', context)


@login_required
def blog_post_edit(request, pk):
    """Edit a blog post"""
    blog_post = get_object_or_404(BlogPost, pk=pk)
    all_tags = Tag.objects.all().order_by('name')
    
    if request.method == 'POST':
        # Update fields
        blog_post.title = request.POST.get('title', blog_post.title)
        blog_post.slug = request.POST.get('slug', blog_post.slug)
        blog_post.content = request.POST.get('content', blog_post.content)
        blog_post.meta_title = request.POST.get('meta_title', '')
        blog_post.meta_description = request.POST.get('meta_description', '')
        blog_post.featured_image_description = request.POST.get('featured_image_description', '')
        blog_post.faq_title = request.POST.get('faq_title', '')
        blog_post.published = request.POST.get('published', 'off') == 'on'
        
        # Handle FAQ
        faq_text = request.POST.get('faq', '').strip()
        if faq_text:
            try:
                faq_data = json.loads(faq_text)
                # Validate it's a list with proper structure
                if isinstance(faq_data, list):
                    # Ensure each item has question and answer
                    valid_faq = []
                    for item in faq_data:
                        if isinstance(item, dict) and 'question' in item and 'answer' in item:
                            valid_faq.append({
                                'question': str(item['question']).strip(),
                                'answer': str(item['answer']).strip()
                            })
                    blog_post.faq = valid_faq
                else:
                    blog_post.faq = []
            except (json.JSONDecodeError, ValueError, KeyError):
                # If JSON parsing fails, keep existing FAQ or set to empty
                if not blog_post.faq:
                    blog_post.faq = []
        else:
            blog_post.faq = []
        
        # Handle featured image upload
        if 'featured_image' in request.FILES:
            blog_post.featured_image = request.FILES['featured_image']
        
        # Handle tags
        selected_tag_ids = request.POST.getlist('tags')
        blog_post.tags.set(selected_tag_ids)
        
        # Save
        try:
            blog_post.save()
            
            # Parse and sync image records from content
            _parse_and_create_blog_post_images(blog_post)
            
            messages.success(request, f'Blog post "{blog_post.title}" updated successfully!')
            
            # Log activity
            log_activity(
                'blog_post_updated',
                f'Blog post "{blog_post.title}" was updated',
                user=request.user,
                metadata={'blog_post_id': blog_post.id}
            )
            
            return redirect('sources:blog_post_detail', pk=blog_post.pk)
        except Exception as e:
            messages.error(request, f'Error updating blog post: {str(e)}')
    
    # Prefetch blog post tags to avoid N+1 queries in template
    blog_post_tags = set(blog_post.tags.all())
    
    # Format FAQ as JSON string for textarea
    faq_json = ''
    if blog_post.faq:
        faq_json = json.dumps(blog_post.faq, indent=2, ensure_ascii=False)
    
    context = {
        'blog_post': blog_post,
        'all_tags': all_tags,
        'blog_post_tags': blog_post_tags,
        'faq_json': faq_json,
    }
    
    return render(request, 'sources/blog_post_edit.html', context)


@login_required
def blog_post_delete(request, pk):
    """Delete a blog post"""
    try:
        blog_post = BlogPost.objects.get(pk=pk)
    except BlogPost.DoesNotExist:
        messages.error(request, 'Blog post not found')
        return redirect('sources:blog_post_list')
    
    if request.method == 'POST':
        title = blog_post.title
        blog_post_id = blog_post.id
        blog_post.delete()
        # Log activity
        log_activity(
            'blog_post_deleted',
            f'Blog post "{title}" was deleted',
            user=request.user,
            metadata={'blog_post_id': blog_post_id, 'title': title}
        )
        messages.success(request, f'Blog post "{title}" deleted successfully')
        return redirect('sources:blog_post_list')
    
    context = {
        'blog_post': blog_post
    }
    return render(request, 'sources/blog_post_confirm_delete.html', context)


@login_required
def blog_post_generate_metadata(request, pk):
    """Generate metadata (slug, meta title, meta description, tags) for a blog post"""
    blog_post = get_object_or_404(BlogPost, pk=pk)
    
    if not blog_post.content:
        messages.error(request, 'Blog post has no content. Please generate content first.')
        return redirect('sources:blog_post_detail', pk=blog_post.pk)
    
    # Load the prompt template
    import os
    from django.conf import settings as django_settings
    
    prompt_file_path = os.path.join(django_settings.BASE_DIR, 'prompt-metadata-generator')
    try:
        with open(prompt_file_path, 'r', encoding='utf-8') as f:
            prompt_template = f.read()
    except FileNotFoundError:
        messages.error(request, 'Prompt template file not found. Please ensure prompt-metadata-generator exists.')
        return redirect('sources:blog_post_detail', pk=blog_post.pk)
    
    if request.method == 'POST':
        provider = request.POST.get('provider', 'gemini').strip().lower()
        model = request.POST.get('model', 'gemini-3-pro-preview').strip()
        
        # Validate provider
        if provider not in ['ollama', 'openai', 'gemini']:
            messages.error(request, 'Invalid provider selected.')
            return redirect('sources:blog_post_detail', pk=blog_post.pk)
        
        # Get default model if not provided
        if not model:
            if provider == 'ollama':
                try:
                    app_settings = Settings.get_settings()
                    model = app_settings.default_tagging_model
                except Exception:
                    model = 'gpt-oss:20b-cloud'
            elif provider == 'openai':
                model = 'gpt-4o-mini'
            elif provider == 'gemini':
                model = 'gemini-3-pro-preview'
        
        try:
            # Strip HTML tags from content before sending to AI (HTML might trigger safety filters)
            import re
            # Remove HTML tags but keep the text content
            text_content = re.sub(r'<[^>]+>', ' ', blog_post.content)
            # Clean up extra whitespace
            text_content = ' '.join(text_content.split())
            
            # Build the prompt with the cleaned text content
            prompt = prompt_template.replace('[PASTE YOUR GENERATED HTML CONTENT HERE]', text_content)
            
            # Call the appropriate AI provider
            # Use higher token limits for metadata generation (especially for Gemini which uses "thoughts" tokens)
            rag_service = RAGService()
            if provider == 'ollama':
                generated_metadata = rag_service._call_ollama(prompt, model, max_tokens=2000)
            elif provider == 'openai':
                generated_metadata = rag_service._call_openai(prompt, model, max_tokens=2000)
            elif provider == 'gemini':
                # Gemini models (especially gemini-3-pro) use "thoughts" tokens, so we need more headroom
                # Increase to 4000 to account for thoughts tokens (999 used) + actual response
                generated_metadata = rag_service._call_gemini(prompt, model, max_tokens=4000)
            else:
                messages.error(request, 'Invalid provider.')
                return redirect('sources:blog_post_detail', pk=blog_post.pk)
            
            # Parse the generated metadata
            import re
            
            # Extract meta title - try multiple patterns
            meta_title = None
            patterns = [
                r'\*\*Meta Title:\*\*\s*(.+?)(?:\n|$)',
                r'Meta Title:\s*(.+?)(?:\n|$)',
                r'Title:\s*(.+?)(?:\n|$)',
            ]
            for pattern in patterns:
                meta_title_match = re.search(pattern, generated_metadata, re.IGNORECASE)
                if meta_title_match:
                    meta_title = meta_title_match.group(1).strip()
                    # Remove markdown formatting if present
                    meta_title = re.sub(r'\*\*|\*|\[|\]|`', '', meta_title).strip()
                    if meta_title and len(meta_title) <= 60:
                        blog_post.meta_title = meta_title
                        break
            
            # Extract meta description - try multiple patterns
            meta_description = None
            patterns = [
                r'\*\*Meta Description:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
                r'Meta Description:\s*(.+?)(?=\n\*\*|\n\n|$)',
                r'Description:\s*(.+?)(?=\n\*\*|\n\n|$)',
            ]
            for pattern in patterns:
                meta_desc_match = re.search(pattern, generated_metadata, re.IGNORECASE | re.DOTALL)
                if meta_desc_match:
                    meta_description = meta_desc_match.group(1).strip()
                    # Remove markdown formatting if present
                    meta_description = re.sub(r'\*\*|\*|\[|\]|`', '', meta_description).strip()
                    # Remove newlines and extra spaces
                    meta_description = ' '.join(meta_description.split())
                    if meta_description and len(meta_description) <= 160:
                        blog_post.meta_description = meta_description
                        break
            
            # Extract URL slug - try multiple patterns
            slug = None
            patterns = [
                r'\*\*URL Slug:\*\*\s*(.+?)(?:\n|$)',
                r'URL Slug:\s*(.+?)(?:\n|$)',
                r'Slug:\s*(.+?)(?:\n|$)',
            ]
            for pattern in patterns:
                slug_match = re.search(pattern, generated_metadata, re.IGNORECASE)
                if slug_match:
                    slug = slug_match.group(1).strip()
                    # Remove markdown formatting and ensure it's a valid slug
                    slug = re.sub(r'\*\*|\*|\[|\]|`', '', slug).strip()
                    slug = slugify(slug)
                    if slug and len(slug) <= 255:
                        # Check if slug is unique (excluding current post)
                        if not BlogPost.objects.filter(slug=slug).exclude(pk=blog_post.pk).exists():
                            blog_post.slug = slug
                        else:
                            messages.warning(request, f'Slug "{slug}" already exists. Keeping current slug.')
                        break
            
            # Extract tags - try multiple patterns
            tags_text = None
            patterns = [
                r'\*\*Tags:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
                r'Tags:\s*(.+?)(?=\n\*\*|\n\n|$)',
            ]
            for pattern in patterns:
                tags_match = re.search(pattern, generated_metadata, re.IGNORECASE | re.DOTALL)
                if tags_match:
                    tags_text = tags_match.group(1).strip()
                    break
            
            if tags_text:
                # Remove markdown formatting
                tags_text = re.sub(r'\*\*|\*|\[|\]|`', '', tags_text)
                # Split by comma and clean up
                tag_names = [tag.strip() for tag in tags_text.split(',') if tag.strip()]
                
                # Get or create tags
                tags_to_add = []
                for tag_name in tag_names[:10]:  # Limit to 10 tags
                    if tag_name:  # Ensure tag name is not empty
                        tag_slug = slugify(tag_name)
                        # First try to get by slug (since slug is unique)
                        try:
                            tag = Tag.objects.get(slug=tag_slug)
                        except Tag.DoesNotExist:
                            # If slug doesn't exist, try to get by name (case-insensitive)
                            try:
                                tag = Tag.objects.get(name__iexact=tag_name)
                            except Tag.DoesNotExist:
                                # Create new tag
                                tag = Tag.objects.create(
                                    name=tag_name,
                                    slug=tag_slug
                                )
                        tags_to_add.append(tag)
                
                # Set tags (this replaces existing tags)
                if tags_to_add:
                    blog_post.tags.set(tags_to_add)
            
            # Extract featured image alt text - try multiple patterns
            featured_image_desc = None
            patterns = [
                r'\*\*Featured Image Alt Text:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
                r'Featured Image Alt Text:\s*(.+?)(?=\n\*\*|\n\n|$)',
                r'\*\*Featured Image Description:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',  # Fallback for old format
                r'Featured Image Description:\s*(.+?)(?=\n\*\*|\n\n|$)',  # Fallback for old format
            ]
            for pattern in patterns:
                img_desc_match = re.search(pattern, generated_metadata, re.IGNORECASE | re.DOTALL)
                if img_desc_match:
                    featured_image_desc = img_desc_match.group(1).strip()
                    # Remove markdown formatting
                    featured_image_desc = re.sub(r'\*\*|\*|\[|\]|`', '', featured_image_desc).strip()
                    # Remove newlines and extra spaces
                    featured_image_desc = ' '.join(featured_image_desc.split())
                    # Limit to 200 characters for alt text (reasonable limit)
                    if featured_image_desc:
                        if len(featured_image_desc) > 200:
                            featured_image_desc = featured_image_desc[:197] + '...'
                        blog_post.featured_image_description = featured_image_desc
                        break
            
            # Extract FAQ Section Title - try multiple patterns
            faq_title = None
            patterns = [
                r'\*\*FAQ Section Title:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
                r'FAQ Section Title:\s*(.+?)(?=\n\*\*|\n\n|$)',
                r'\*\*FAQ Title:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
                r'FAQ Title:\s*(.+?)(?=\n\*\*|\n\n|$)',
            ]
            for pattern in patterns:
                faq_title_match = re.search(pattern, generated_metadata, re.IGNORECASE | re.DOTALL)
                if faq_title_match:
                    faq_title = faq_title_match.group(1).strip()
                    # Remove markdown formatting if present
                    faq_title = re.sub(r'\*\*|\*|\[|\]|`', '', faq_title).strip()
                    # Remove newlines and extra spaces
                    faq_title = ' '.join(faq_title.split())
                    if faq_title and len(faq_title) <= 200:
                        blog_post.faq_title = faq_title
                        break
            
            # Extract FAQ - try multiple patterns
            faq_data = None
            # Try to find FAQ section - look for FAQ: followed by JSON array (may span multiple lines)
            patterns = [
                r'\*\*FAQ:\*\*\s*(\[.*?\])\s*(?=\n\*\*|\n\n|$)',
                r'FAQ:\s*(\[.*?\])\s*(?=\n\*\*|\n\n|$)',
                r'\*\*FAQ\*\*\s*(\[.*?\])\s*(?=\n\*\*|\n\n|$)',
                r'\*\*FAQ:\*\*\s*(\[[\s\S]*?\])',  # More flexible for multi-line
                r'FAQ:\s*(\[[\s\S]*?\])',  # More flexible for multi-line
            ]
            for pattern in patterns:
                faq_match = re.search(pattern, generated_metadata, re.IGNORECASE | re.DOTALL)
                if faq_match:
                    faq_text = faq_match.group(1).strip()
                    try:
                        # Try to parse as JSON
                        faq_data = json.loads(faq_text)
                        # Validate it's a list with proper structure
                        if isinstance(faq_data, list) and len(faq_data) > 0:
                            # Ensure each item has question and answer
                            valid_faq = []
                            for item in faq_data[:4]:  # Limit to 4 items
                                if isinstance(item, dict) and 'question' in item and 'answer' in item:
                                    valid_faq.append({
                                        'question': str(item['question']).strip(),
                                        'answer': str(item['answer']).strip()
                                    })
                            # Accept any number of valid FAQ items (1-4) instead of requiring exactly 4
                            if len(valid_faq) > 0:
                                blog_post.faq = valid_faq
                                break
                    except (json.JSONDecodeError, ValueError, KeyError) as e:
                        # If JSON parsing fails, try next pattern
                        continue
            
            # If FAQ wasn't found with patterns, try to find JSON array anywhere after "FAQ:"
            if not blog_post.faq:
                # Find the position of "FAQ:" in the text
                faq_label_match = re.search(r'FAQ:', generated_metadata, re.IGNORECASE)
                if faq_label_match:
                    # Get text after FAQ:
                    text_after_faq = generated_metadata[faq_label_match.end():]
                    # Try to find a JSON array - look for opening bracket and try to match closing bracket
                    bracket_start = text_after_faq.find('[')
                    if bracket_start != -1:
                        # Find matching closing bracket by counting brackets
                        bracket_count = 0
                        bracket_end = -1
                        for i in range(bracket_start, len(text_after_faq)):
                            if text_after_faq[i] == '[':
                                bracket_count += 1
                            elif text_after_faq[i] == ']':
                                bracket_count -= 1
                                if bracket_count == 0:
                                    bracket_end = i + 1
                                    break
                        
                        if bracket_end > bracket_start:
                            faq_text = text_after_faq[bracket_start:bracket_end].strip()
                            try:
                                faq_data = json.loads(faq_text)
                                if isinstance(faq_data, list) and len(faq_data) > 0:
                                    valid_faq = []
                                    for item in faq_data[:4]:
                                        if isinstance(item, dict) and 'question' in item and 'answer' in item:
                                            valid_faq.append({
                                                'question': str(item['question']).strip(),
                                                'answer': str(item['answer']).strip()
                                            })
                                    if len(valid_faq) > 0:
                                        blog_post.faq = valid_faq
                            except (json.JSONDecodeError, ValueError, KeyError):
                                pass
            
            # Save the blog post
            blog_post.save()
            
            # Log activity
            log_activity(
                'blog_post_updated',
                f'Metadata generated for blog post "{blog_post.title}"',
                user=request.user,
                metadata={
                    'blog_post_id': blog_post.id,
                    'provider': provider,
                    'model': model
                }
            )
            
            messages.success(request, 'Metadata generated successfully!')
            return redirect('sources:blog_post_detail', pk=blog_post.pk)
            
        except Exception as e:
            error_msg = str(e)
            messages.error(request, f'Error generating metadata: {error_msg}')
            import traceback
            traceback.print_exc()
    
    # GET request - show the generation form
    context = {
        'blog_post': blog_post,
        'default_provider': 'gemini',
        'default_model': 'gemini-3-pro-preview',
    }
    
    return render(request, 'sources/blog_post_generate_metadata.html', context)


@login_required
def blog_post_generate(request, pk):
    """Generate a blog post from a post idea"""
    post_idea = get_object_or_404(PostIdea, pk=pk)
    
    # Load the prompt template
    import os
    from django.conf import settings as django_settings
    
    prompt_file_path = os.path.join(django_settings.BASE_DIR, 'prompt-post-generation.md')
    try:
        with open(prompt_file_path, 'r', encoding='utf-8') as f:
            prompt_template = f.read()
    except FileNotFoundError:
        messages.error(request, 'Prompt template file not found. Please ensure prompt-post-generation.md exists.')
        return redirect('sources:post_idea_list')
    
    if request.method == 'POST':
        provider = request.POST.get('provider', 'gemini').strip().lower()
        model = request.POST.get('model', 'gemini-3-pro-preview').strip()
        use_rag = request.POST.get('use_rag', 'off') == 'on'
        num_chunks = int(request.POST.get('num_chunks', 5))
        
        # Validate provider
        if provider not in ['ollama', 'openai', 'gemini']:
            messages.error(request, 'Invalid provider selected.')
            return redirect('sources:post_idea_list')
        
        # Get default model if not provided
        if not model:
            if provider == 'ollama':
                try:
                    app_settings = Settings.get_settings()
                    model = app_settings.default_tagging_model
                except Exception:
                    model = 'gpt-oss:20b-cloud'
            elif provider == 'openai':
                model = 'gpt-4o-mini'
            elif provider == 'gemini':
                model = 'gemini-3-pro-preview'
        
        try:
            # Get RAG context if enabled
            rag_context = ""
            if use_rag:
                try:
                    rag_service = RAGService()
                    chunks = rag_service.search_similar_chunks(
                        query_text=post_idea.title,
                        num_chunks=num_chunks
                    )
                    if chunks:
                        rag_context = rag_service._format_context(chunks)
                except Exception as e:
                    messages.warning(request, f'RAG context retrieval failed: {str(e)}. Continuing without context.')
            
            # Build the prompt
            # Include primary keyword if available, otherwise use title as fallback
            primary_keyword = post_idea.primary_keyword or post_idea.title
            prompt = prompt_template.format(
                title=post_idea.title,
                description=post_idea.description or "No description provided.",
                primary_keyword=primary_keyword
            )
            
            # Add RAG context if available
            if rag_context:
                prompt = f"{prompt}\n\n### Additional Context from Content Library:\n{rag_context}"
            
            # Call the appropriate AI provider with higher token limits for blog posts
            rag_service = RAGService()
            # Set higher token limits for blog post generation
            # Blog posts need much more content than regular Q&A responses
            if provider == 'ollama':
                # Ollama: 8000 tokens (or None for unlimited if model supports it)
                generated_content = rag_service._call_ollama(prompt, model, max_tokens=8000)
            elif provider == 'openai':
                # OpenAI: 8000-16000 tokens depending on model capabilities
                # GPT-4o and newer models support up to 16k
                max_tokens = 16000 if any(keyword in model.lower() for keyword in ['gpt-4o', 'gpt-4-turbo', 'gpt-4']) else 8000
                generated_content = rag_service._call_openai(prompt, model, max_tokens=max_tokens)
            elif provider == 'gemini':
                # Gemini: gemini-3-pro-preview supports up to 32k tokens
                # Use 16k for comprehensive blog posts
                max_tokens = 16000 if 'gemini-3-pro' in model.lower() else 8000
                generated_content = rag_service._call_gemini(prompt, model, max_tokens=max_tokens)
            else:
                messages.error(request, 'Invalid provider.')
                return redirect('sources:post_idea_list')
            
            # The prompt generates the blog post content
            # We'll use the post idea title as the blog post title for now
            # Other fields (meta_title, meta_description) will be handled later
            blog_title = post_idea.title
            blog_content = generated_content.strip()
            
            # Post-process to remove any unwanted intro text or JSON
            import re
            # Remove any text before the first <h1> tag
            h1_match = re.search(r'<h1>', blog_content, re.IGNORECASE)
            if h1_match:
                blog_content = blog_content[h1_match.start():]
            
            # Remove any JSON-LD schema blocks at the end
            # Look for ```json or ``` followed by JSON content
            json_pattern = r'```(?:json)?\s*\{.*?\}\s*```'
            blog_content = re.sub(json_pattern, '', blog_content, flags=re.DOTALL | re.IGNORECASE)
            
            # Remove standalone JSON blocks (without code fences)
            json_block_pattern = r'\s*\{[^{}]*"@context"[^{}]*"@type"[^{}]*\}'
            blog_content = re.sub(json_block_pattern, '', blog_content, flags=re.DOTALL | re.IGNORECASE)
            
            # Remove common intro phrases if they appear at the start
            intro_phrases = [
                r'^Here is a comprehensive.*?optimized for.*?\n+',
                r'^This is a comprehensive.*?guide.*?\n+',
                r'^Below is.*?\n+',
            ]
            for pattern in intro_phrases:
                blog_content = re.sub(pattern, '', blog_content, flags=re.IGNORECASE | re.MULTILINE)
            
            # Remove FAQ sections if they were generated (they should be in metadata, not content)
            # Remove FAQ sections with various heading formats
            faq_patterns = [
                r'<h[2-6][^>]*>.*?FAQ.*?</h[2-6]>.*?(?=<h[2-6]|</body>|$)',
                r'<h[2-6][^>]*>.*?Frequently Asked.*?</h[2-6]>.*?(?=<h[2-6]|</body>|$)',
                r'<h[2-6][^>]*>.*?People Also Ask.*?</h[2-6]>.*?(?=<h[2-6]|</body>|$)',
            ]
            for pattern in faq_patterns:
                blog_content = re.sub(pattern, '', blog_content, flags=re.IGNORECASE | re.DOTALL)
            
            blog_content = blog_content.strip()
            
            # Create the blog post with just the content field populated
            blog_post = BlogPost.objects.create(
                title=blog_title,
                content=blog_content,
                post_idea=post_idea
                # meta_title and meta_description will be handled later
            )
            
            # Parse and create image records from content
            _parse_and_create_blog_post_images(blog_post)
            
            # Copy tags from post idea if any (we'll need to add tags to PostIdea or handle differently)
            # For now, we'll leave tags empty
            
            # Log activity
            log_activity(
                'blog_post_created',
                f'Blog post "{blog_title}" was generated from post idea "{post_idea.title}"',
                user=request.user,
                metadata={
                    'blog_post_id': blog_post.id,
                    'post_idea_id': post_idea.id,
                    'provider': provider,
                    'model': model
                }
            )
            
            messages.success(request, f'Blog post "{blog_title}" generated successfully!')
            # Redirect to blog post detail page
            return redirect('sources:blog_post_detail', pk=blog_post.id)
            
        except Exception as e:
            error_msg = str(e)
            messages.error(request, f'Error generating blog post: {error_msg}')
            import traceback
            traceback.print_exc()
    
    # GET request - show the generation form
    # Get available models for the default provider (Gemini)
    context = {
        'post_idea': post_idea,
        'default_provider': 'gemini',
        'default_model': 'gemini-3-pro-preview',
    }
    
    return render(request, 'sources/blog_post_generate.html', context)


def _generate_post_ideas(num_ideas, provider, model, selected_tags=None, selected_contents=None, user=None, max_retries=5, similarity_threshold=0.70):
    """
    Helper function to generate post ideas using the specified provider and model.
    Automatically retries generation until the requested number of valid (non-duplicate) ideas are found.
    Returns tuple: (success: bool, created_count: int, created_ideas: list, error_message: str, skipped_similar: int)
    
    Args:
        num_ideas: Number of ideas to generate
        provider: AI provider ('ollama', 'openai', or 'gemini')
        model: Model name to use
        selected_tags: Optional list of tags to use as context
        selected_contents: Optional list of content items to use as context
        user: Optional user object for logging
        max_retries: Maximum number of retry attempts (default: 5)
        similarity_threshold: Similarity threshold for duplicate detection (0.0-1.0, default: 0.70)
            Higher threshold = more strict (only flag near-duplicates)
            0.92 = very strict (only near-duplicates), 0.9 = strict, 0.85 = moderate, 0.7 = lenient
    """
    selected_tags = selected_tags or []
    selected_contents = selected_contents or []
    
    # Initialize embedding service for similarity checking
    embedding_service = None
    try:
        embedding_service = EmbeddingService()
    except (ValueError, ImportError):
        # Embedding service not available, skip similarity checking
        pass
    
    # Get existing ideas with embeddings for comparison (load once, update as we create)
    existing_ideas_with_embeddings = list(
        PostIdea.objects.filter(title_embedding__isnull=False)
    ) if embedding_service else []
    
    # Build context for prompt
    context_parts = []
    
    if selected_tags:
        tag_names = [tag.name for tag in selected_tags]
        context_parts.append(f"Tags/Categories: {', '.join(tag_names)}")
    
    if selected_contents:
        content_summaries = []
        for content in selected_contents[:5]:  # Limit to 5 contents to avoid too long prompts
            summary = f"- {content.title}"
            if content.content:
                # Truncate content preview
                content_preview = content.content[:300] if len(content.content) > 300 else content.content
                summary += f"\n  Preview: {content_preview}..."
            content_summaries.append(summary)
        context_parts.append(f"Related Content:\n" + "\n".join(content_summaries))
    
    context_text = "\n\n".join(context_parts) if context_parts else "General blog post ideas about China, Chinese culture, travel, history, and related topics."
    
    # Track totals across all retries
    total_created_count = 0
    total_created_ideas = []
    total_skipped_similar = 0
    total_skipped_titles = []
    total_attempts = 0
    ideas_still_needed = num_ideas
    
    # Get sample of existing idea titles to avoid (for diversity)
    existing_titles_sample = list(PostIdea.objects.values_list('title', flat=True)[:20])
    existing_titles_text = ""
    if existing_titles_sample:
        existing_titles_text = f"\n\n### AVOID THESE TOPICS (already covered):\n" + "\n".join([f"- {title}" for title in existing_titles_sample[:15]])
    
    # Get random tags for diversity (if available) - prepare list once
    all_tags = list(Tag.objects.all())
    
    # Call API to generate ideas
    try:
        import requests
        import json
    except ImportError:
        return False, 0, [], 'requests library required. Install with: pip install requests', 0
    
    # Retry loop: continue generating until we have enough valid ideas
    while ideas_still_needed > 0 and total_attempts < max_retries:
        total_attempts += 1
        # Increase batch size on retries to get more variety
        batch_size = max(ideas_still_needed, 5 + (total_attempts - 1) * 2)  # 5, 7, 9, 11, 13...
        
        # Increase creativity on retries
        creativity_boost = ""
        if total_attempts > 1:
            creativity_boost = f"""
### CREATIVITY REQUIREMENT (Attempt #{total_attempts}):
- **BE MORE CREATIVE AND DIVERSE** - Previous attempts generated ideas too similar to existing ones.
- Explore **less common destinations** (beyond Beijing, Shanghai, Xi'an, Chengdu, Yunnan).
- Consider **unique angles**: specific neighborhoods, festivals, activities, transportation modes, accommodation types, travel styles (budget, luxury, solo, family, etc.).
- Think about **niche topics**: photography spots, hiking trails, local markets, traditional crafts, specific dishes, regional dialects, local customs, etc.
- Avoid repeating the same format (e.g., don't keep generating "X-Day Itinerary" or "Best Time to Visit X").
"""
        
        # Get random tags for diversity on retries
        random_tags_text = ""
        if total_attempts > 1 and len(all_tags) >= 3:
            random_tags = random.sample(all_tags, min(3, len(all_tags)))
            random_tag_names = [tag.name for tag in random_tags]
            random_tags_text = f"\n\n### EXPLORE THESE TOPICS (for diversity):\n" + ", ".join(random_tag_names)
        
        # Build prompt with current iteration values
        prompt = f"""
        Generate {batch_size} high-quality blog post ideas for a China travel blog.

Your ideas must be directly useful for people planning a trip to China.  
Avoid abstract cultural topics unless they clearly help a traveler understand a place, activity, or tradition they can experience on a trip.

Use the following context (optional reference material):
{context_text}{existing_titles_text}{random_tags_text}{creativity_boost}

### Requirements
- Each idea must clearly answer a real search intent a traveler might have.
- Ideas should be practical, specific, and actionable: itineraries, travel guides, food recommendations, destination highlights, logistics, tips, or seasonal advice.
- Avoid purely cultural or historical analysis unless it connects directly to a travel experience.
- **IMPORTANT: Ensure each idea is DISTINCT and covers a different topic or angle. Avoid generating multiple variations of the same idea (e.g., don't generate "How to Book Trains" and "Train Booking Guide" - they're too similar).**
- **CRITICAL: If this is a retry attempt, you MUST generate ideas that are significantly different from common topics. Explore unique angles, less-visited destinations, or specific niches.**
- Each idea must contain:
  1. A compelling and SEO-friendly title (50–80 characters)
  2. A brief description (1–2 sentences) explaining what the post covers and why it helps a traveler.
- Cover a diverse range of regions, themes, and traveler needs.

### Tone Guidance
Prioritize topics that answer searches like:
- "Best things to do in ___"
- "Where to eat in ___"
- "Travel guide"
- "Hidden gems in ___"
- "Is ___ worth visiting?"
- "How to get from ___ to ___"
- "Best time to visit ___"
- "What to eat in ___"
- "7-day itinerary for ___"

### Important
Every idea must be explicitly linked to travel, trip planning, or on-the-ground experience in China.

### Output Format
Respond in JSON only:

{{
  "ideas": [
    {{
      "title": "Post title here",
      "description": "Brief summary explaining the travel value",
      "primary_keyword": "Main SEO keyword for this post (e.g., 'Chengdu travel guide', 'China visa requirements')"
    }}
  ]
}}

Generate exactly {batch_size} ideas.
Response:
"""
        
        response_text = None
        api_error = None
        
        try:
            if provider == 'ollama':
                # Call Ollama
                ollama_url = getattr(settings, 'OLLAMA_URL', 'http://localhost:11434')
                url = f"{ollama_url}/api/generate"
                
                # Increase temperature on retries for more creativity
                base_temp = 0.8
                retry_temp = min(1.2, base_temp + (total_attempts - 1) * 0.1)  # 0.8, 0.9, 1.0, 1.1, 1.2
                
                payload = {
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": retry_temp,
                        "top_p": 0.9,
                    }
                }
                
                response = requests.post(url, json=payload, timeout=120)
                response.raise_for_status()
                result = response.json()
                response_text = result.get('response', '').strip()
                
            elif provider == 'openai':
                # Call OpenAI
                api_key = getattr(settings, 'OPENAI_API_KEY', None)
                if not api_key:
                    api_error = 'OPENAI_API_KEY is not set in settings. Please configure it to use OpenAI.'
                    if total_attempts >= max_retries:
                        return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                    continue  # Continue while loop
            
                try:
                    from openai import OpenAI
                except ImportError:
                    api_error = 'openai library required. Install with: pip install openai'
                    if total_attempts >= max_retries:
                        return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                    continue  # Continue while loop
                
                client = OpenAI(api_key=api_key)
                
                # Check model type for parameter compatibility
                is_gpt5 = 'gpt-5' in model.lower()
                is_newer_model = any(keyword in model.lower() for keyword in ['gpt-4o', 'gpt-5', 'o1', 'o3'])
                
                # Build request parameters
                request_params = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant that generates blog post ideas for a China travel blog. Always respond with valid JSON only."},
                        {"role": "user", "content": prompt}
                    ],
                }
                
                # GPT-5 only supports default temperature (1), so don't set it
                if not is_gpt5:
                    # Increase temperature on retries for more creativity
                    base_temp = 0.8
                    retry_temp = min(1.2, base_temp + (total_attempts - 1) * 0.1)  # 0.8, 0.9, 1.0, 1.1, 1.2
                    request_params["temperature"] = retry_temp
                
                # Use appropriate parameter based on model
                if is_newer_model:
                    request_params["max_completion_tokens"] = 2000
                else:
                    request_params["max_tokens"] = 2000
                
                response = client.chat.completions.create(**request_params)
                response_text = response.choices[0].message.content.strip()
                
            elif provider == 'gemini':
                # Call Gemini
                api_key = getattr(settings, 'GEMINI_API_KEY', None)
                if not api_key:
                    api_error = 'GEMINI_API_KEY is not set in settings. Please configure it to use Gemini.'
                    if total_attempts >= max_retries:
                        return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                    continue  # Continue while loop
                
                try:
                    import google.generativeai as genai
                except ImportError:
                    api_error = 'google-generativeai library required. Install with: pip install google-generativeai'
                    if total_attempts >= max_retries:
                        return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                    continue  # Continue while loop
                
                genai.configure(api_key=api_key)
                
                # Configure safety settings to be more permissive for content generation
                # This helps avoid false positives when generating blog post ideas
                # Note: gemini-3-pro-preview may have stricter filters, so we use BLOCK_ONLY_HIGH for all
                try:
                    from google.generativeai.types import HarmCategory, HarmBlockThreshold
                    safety_settings = {
                        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
                        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_ONLY_HIGH,
                        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
                        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
                    }
                except (ImportError, AttributeError):
                    # Fallback to string-based settings if enum import fails
                    safety_settings = {
                        "HARM_CATEGORY_HARASSMENT": "BLOCK_ONLY_HIGH",
                        "HARM_CATEGORY_HATE_SPEECH": "BLOCK_ONLY_HIGH",
                        "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_ONLY_HIGH",
                        "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_ONLY_HIGH",
                    }
                
                genai_model = genai.GenerativeModel(model, safety_settings=safety_settings)
                # Increase temperature on retries for more creativity
                base_temp = 0.8
                retry_temp = min(1.2, base_temp + (total_attempts - 1) * 0.1)  # 0.8, 0.9, 1.0, 1.1, 1.2
                
                # Calculate appropriate token limit based on batch size
                # Each idea needs ~200-300 tokens (title + description + keyword), so we need headroom
                # Use at least 4000 tokens, or scale with batch size
                max_tokens = max(4000, batch_size * 500)
                # Cap at 8192 (common limit for many Gemini models)
                max_tokens = min(max_tokens, 8192)
                
                response = genai_model.generate_content(
                    prompt,
                    generation_config={
                        "temperature": retry_temp,
                        "max_output_tokens": max_tokens,
                    }
                )
                
                # Check for blocked responses or empty content BEFORE accessing response.text
                # Accessing response.text when finish_reason indicates a block will throw an exception
                if response.candidates and len(response.candidates) > 0:
                    candidate = response.candidates[0]
                    finish_reason = candidate.finish_reason
                    
                    # Handle different finish reasons
                    # finish_reason can be an integer (1=STOP, 2=MAX_TOKENS, 3=SAFETY, etc.) or string
                    finish_reason_str = str(finish_reason).upper() if finish_reason else ""
                    finish_reason_int = int(finish_reason) if isinstance(finish_reason, (int, str)) and str(finish_reason).isdigit() else None
                    
                    # Check for SAFETY blocks (finish_reason 3 or "SAFETY")
                    # Note: Sometimes SAFETY may be reported as finish_reason 2 when there are no parts
                    if finish_reason_int == 3 or "SAFETY" in finish_reason_str:
                        reason_text = "SAFETY (blocked by content safety filters)"
                        # Try to get detailed safety ratings if available
                        safety_info = ""
                        blocked_categories = []
                        if hasattr(candidate, 'safety_ratings') and candidate.safety_ratings:
                            for rating in candidate.safety_ratings:
                                # Check if this rating caused the block
                                # HIGH or MEDIUM probability ratings are likely the cause
                                prob_name = rating.probability.name if hasattr(rating.probability, 'name') else str(rating.probability)
                                cat_name = rating.category.name if hasattr(rating.category, 'name') else str(rating.category)
                                if prob_name not in ['NEGLIGIBLE', 'LOW']:
                                    blocked_categories.append(f"{cat_name}: {prob_name}")
                            if blocked_categories:
                                safety_info = f" Blocked categories: {', '.join(blocked_categories)}."
                        
                        # Also check prompt_feedback for additional info
                        if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                            if hasattr(response.prompt_feedback, 'safety_ratings') and response.prompt_feedback.safety_ratings:
                                prompt_blocked = []
                                for rating in response.prompt_feedback.safety_ratings:
                                    prob_name = rating.probability.name if hasattr(rating.probability, 'name') else str(rating.probability)
                                    cat_name = rating.category.name if hasattr(rating.category, 'name') else str(rating.category)
                                    if prob_name not in ['NEGLIGIBLE', 'LOW']:
                                        prompt_blocked.append(f"{cat_name}: {prob_name}")
                                if prompt_blocked:
                                    safety_info += f" Prompt blocked categories: {', '.join(prompt_blocked)}."
                        
                        api_error = f'Gemini API response was blocked ({reason_text}).{safety_info} This may be a false positive. Try: 1) Using a different model (e.g., gemini-1.5-pro), 2) Simplifying the prompt, or 3) Using a different provider.'
                        if total_attempts >= max_retries:
                            return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                        continue  # Continue while loop
                    
                    # Check for MAX_TOKENS (finish_reason 2 or "MAX_TOKENS")
                    # Note: finish_reason 2 can also indicate SAFETY when there are no parts
                    # We'll try to access response.text below and handle the exception if it fails
                    if finish_reason_int == 2 or "MAX_TOKENS" in finish_reason_str:
                        # Try to access response.text - if it fails, it's likely a safety block
                        try:
                            if hasattr(response, 'text') and response.text:
                                # It's actually MAX_TOKENS with partial content
                                api_error = f'Gemini API response hit the token limit (MAX_TOKENS). The max_output_tokens ({max_tokens}) may be too low for the requested batch size. Try requesting fewer ideas at once or using a different provider.'
                                if total_attempts >= max_retries:
                                    return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                                continue  # Continue while loop
                        except (ValueError, AttributeError) as e:
                            # finish_reason 2 but can't access text - likely a safety block
                            reason_text = "SAFETY (blocked by content safety filters)"
                            safety_info = ""
                            blocked_categories = []
                            if hasattr(candidate, 'safety_ratings') and candidate.safety_ratings:
                                for rating in candidate.safety_ratings:
                                    prob_name = rating.probability.name if hasattr(rating.probability, 'name') else str(rating.probability)
                                    cat_name = rating.category.name if hasattr(rating.category, 'name') else str(rating.category)
                                    if prob_name not in ['NEGLIGIBLE', 'LOW']:
                                        blocked_categories.append(f"{cat_name}: {prob_name}")
                                if blocked_categories:
                                    safety_info = f" Blocked categories: {', '.join(blocked_categories)}."
                            
                            # Check prompt_feedback
                            if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                                if hasattr(response.prompt_feedback, 'safety_ratings') and response.prompt_feedback.safety_ratings:
                                    prompt_blocked = []
                                    for rating in response.prompt_feedback.safety_ratings:
                                        prob_name = rating.probability.name if hasattr(rating.probability, 'name') else str(rating.probability)
                                        cat_name = rating.category.name if hasattr(rating.category, 'name') else str(rating.category)
                                        if prob_name not in ['NEGLIGIBLE', 'LOW']:
                                            prompt_blocked.append(f"{cat_name}: {prob_name}")
                                    if prompt_blocked:
                                        safety_info += f" Prompt blocked categories: {', '.join(prompt_blocked)}."
                            
                            api_error = f'Gemini API response was blocked ({reason_text}).{safety_info} This may be a false positive. Try: 1) Using a different model (e.g., gemini-1.5-pro), 2) Simplifying the prompt, or 3) Using a different provider.'
                            if total_attempts >= max_retries:
                                return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                            continue  # Continue while loop
                    
                    # Check for RECITATION (finish_reason 4 or "RECITATION")
                    if finish_reason_int == 4 or "RECITATION" in finish_reason_str:
                        api_error = 'Gemini API response was blocked (RECITATION - blocked due to potential recitation). Try adjusting the prompt or using a different provider.'
                        if total_attempts >= max_retries:
                            return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                        continue  # Continue while loop
                    
                    # Check for OTHER reasons
                    if finish_reason_int and finish_reason_int > 4:
                        api_error = f'Gemini API response was blocked (finish_reason: {finish_reason}). Try adjusting the prompt or using a different provider.'
                        if total_attempts >= max_retries:
                            return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                        continue  # Continue while loop
                
                # Now safely try to access response.text
                try:
                    if not response or not hasattr(response, 'text') or not response.text:
                        api_error = f'Gemini API returned empty or invalid response'
                        if total_attempts >= max_retries:
                            return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                        continue  # Continue while loop
                    response_text = response.text.strip()
                except ValueError as e:
                    # This exception occurs when response.text is accessed but there's no valid Part
                    # Usually happens when finish_reason indicates a block
                    error_str = str(e)
                    if "valid `Part`" in error_str or "finish_reason" in error_str:
                        # Try to get safety information from the response
                        safety_info = ""
                        if hasattr(response, 'candidates') and response.candidates and len(response.candidates) > 0:
                            candidate = response.candidates[0]
                            blocked_categories = []
                            if hasattr(candidate, 'safety_ratings') and candidate.safety_ratings:
                                for rating in candidate.safety_ratings:
                                    prob_name = rating.probability.name if hasattr(rating.probability, 'name') else str(rating.probability)
                                    cat_name = rating.category.name if hasattr(rating.category, 'name') else str(rating.category)
                                    if prob_name not in ['NEGLIGIBLE', 'LOW']:
                                        blocked_categories.append(f"{cat_name}: {prob_name}")
                                if blocked_categories:
                                    safety_info = f" Blocked categories: {', '.join(blocked_categories)}."
                            
                            # Check prompt_feedback
                            if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                                if hasattr(response.prompt_feedback, 'safety_ratings') and response.prompt_feedback.safety_ratings:
                                    prompt_blocked = []
                                    for rating in response.prompt_feedback.safety_ratings:
                                        prob_name = rating.probability.name if hasattr(rating.probability, 'name') else str(rating.probability)
                                        cat_name = rating.category.name if hasattr(rating.category, 'name') else str(rating.category)
                                        if prob_name not in ['NEGLIGIBLE', 'LOW']:
                                            prompt_blocked.append(f"{cat_name}: {prob_name}")
                                    if prompt_blocked:
                                        safety_info += f" Prompt blocked categories: {', '.join(prompt_blocked)}."
                        
                        api_error = f'Gemini API response was blocked or filtered.{safety_info} This may be a false positive. Try: 1) Using a different model (e.g., gemini-1.5-pro), 2) Simplifying the prompt, or 3) Using a different provider. Error: {error_str}'
                    else:
                        api_error = f'Gemini API error: {error_str}'
                    if total_attempts >= max_retries:
                        return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                    continue  # Continue while loop
            else:
                api_error = f'Invalid provider: {provider}'
                if total_attempts >= max_retries:
                    return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                continue  # Continue while loop
            
            # Parse JSON response
        
        except requests.exceptions.ConnectionError as e:
            if provider == 'ollama':
                ollama_url = getattr(settings, 'OLLAMA_URL', 'http://localhost:11434')
                api_error = f'Could not connect to Ollama at {ollama_url}. Make sure Ollama is running.'
            else:
                api_error = f'Connection error: {str(e)}'
            if total_attempts >= max_retries:
                return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
            continue  # Continue while loop
        except Exception as e:
            error_str = str(e)
            # Check for API key errors
            if 'api_key' in error_str.lower() or 'authentication' in error_str.lower() or 'unauthorized' in error_str.lower():
                if provider == 'openai':
                    api_error = f'OpenAI API key error: {error_str}. Please check your OPENAI_API_KEY setting.'
                elif provider == 'gemini':
                    api_error = f'Gemini API key error: {error_str}. Please check your GEMINI_API_KEY setting.'
                else:
                    api_error = f'Authentication error: {error_str}'
            else:
                api_error = f'Error generating ideas with {provider.upper()}: {error_str}'
            if total_attempts >= max_retries:
                return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
            continue  # Continue while loop
        
        if response_text:
            # Try to extract JSON from response
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}')
            
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx + 1]
                try:
                    parsed = json.loads(json_str)
                    ideas = parsed.get('ideas', [])
                    
                    # Process ideas: check similarity and create valid ones
                    batch_created_count = 0
                    batch_created_ideas = []
                    batch_skipped_similar = 0
                    batch_skipped_titles = []
                    
                    for idea_data in ideas:
                        title = idea_data.get('title', '').strip()
                        description = idea_data.get('description', '').strip()
                        primary_keyword = idea_data.get('primary_keyword', '').strip() or None  # Use None instead of empty string
                        
                        if not title:
                            continue
                        
                        # Check for similarity using embeddings if available
                        too_similar = False
                        if embedding_service and existing_ideas_with_embeddings:
                            # Enable debug logging on retries to see similarity scores
                            debug_mode = total_attempts > 1
                            too_similar = is_idea_too_similar_with_embeddings(
                                title, 
                                existing_ideas_with_embeddings,
                                embedding_service,
                                similarity_threshold,
                                debug=debug_mode
                            )
                        
                        if too_similar:
                            batch_skipped_similar += 1
                            batch_skipped_titles.append(title)
                            if total_attempts == 1:
                                print(f"Skipping similar idea: {title}")
                            # Debug output is already printed by the function
                            continue
                        
                        # Generate embedding for the new idea
                        new_embedding = None
                        if embedding_service:
                            try:
                                new_embedding = embedding_service.generate_embedding(title)
                            except Exception as e:
                                print(f"Warning: Could not generate embedding for '{title}': {str(e)}")
                        
                        # Create the post idea
                        post_idea = PostIdea.objects.create(
                            title=title,
                            description=description,
                            primary_keyword=primary_keyword,
                            title_embedding=new_embedding
                        )
                        batch_created_count += 1
                        batch_created_ideas.append({'id': post_idea.id, 'title': post_idea.title})
                        
                        # Add to existing list for checking against in the same batch
                        if new_embedding:
                            existing_ideas_with_embeddings.append(post_idea)
                        
                        # Stop if we have enough ideas
                        if batch_created_count >= ideas_still_needed:
                            break
                        
                    # Update totals
                    total_created_count += batch_created_count
                    total_created_ideas.extend(batch_created_ideas)
                    total_skipped_similar += batch_skipped_similar
                    total_skipped_titles.extend(batch_skipped_titles)
                    ideas_still_needed -= batch_created_count
                    
                    # If we got some valid ideas but not enough, continue the loop
                    if ideas_still_needed > 0:
                        print(f"Generated {batch_created_count} valid ideas, {ideas_still_needed} still needed. Retrying...")
                        # Break out of try to continue while loop
                        break
                    else:
                        # We have enough ideas, break out of retry loop
                        break
                    
                except json.JSONDecodeError:
                    api_error = f'Failed to parse {provider.upper()} response as JSON. Response: {response_text[:200]}'
                    if total_attempts >= max_retries:
                        return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                    continue  # Continue while loop
            else:
                api_error = f'Invalid response format from {provider.upper()}. Response: {response_text[:200]}'
                if total_attempts >= max_retries:
                    return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
                continue  # Continue while loop
        else:
            api_error = f'No response received from {provider.upper()}.'
            if total_attempts >= max_retries:
                return False, total_created_count, total_created_ideas, api_error, total_skipped_similar
            continue  # Continue while loop
    
    # After retry loop completes
    if total_created_count > 0 or total_skipped_similar > 0:
        # Log activity
        message = f'{total_created_count} post idea(s) were generated using AI'
        if total_skipped_similar > 0:
            message += f' ({total_skipped_similar} similar ideas skipped)'
        if total_attempts > 1:
            message += f' (after {total_attempts} generation attempts)'
        
        log_activity(
            'post_ideas_generated',
            message,
            user=user,
            metadata={
                'count': total_created_count,
                'skipped_similar': total_skipped_similar,
                'num_requested': num_ideas,
                'total_attempts': total_attempts,
                'provider': provider,
                'model': model,
                'similarity_check': embedding_service is not None,
                'similarity_threshold': similarity_threshold if embedding_service else None,
                'tags_selected': [tag.id for tag in selected_tags] if selected_tags else [],
                'tags_names': [tag.name for tag in selected_tags] if selected_tags else [],
                'contents_selected': [content.id for content in selected_contents] if selected_contents else [],
                'created_ideas': total_created_ideas,
                'skipped_titles': total_skipped_titles if total_skipped_titles else [],
                'api_generated': user is None
            }
        )
    
    # Return success if we got at least some ideas, or if we exhausted retries
    if total_created_count > 0:
        return True, total_created_count, total_created_ideas, None, total_skipped_similar
    elif total_attempts >= max_retries:
        # All retries exhausted, return with error
        error_msg = api_error if 'api_error' in locals() and api_error else f'Could not generate valid ideas after {max_retries} attempts. {total_skipped_similar} ideas were skipped as duplicates.'
        return False, 0, [], error_msg, total_skipped_similar
    else:
        # Should not reach here, but handle it
        return False, 0, [], 'Unexpected error during idea generation', total_skipped_similar


@csrf_exempt
def post_idea_generate_api(request):
    """API endpoint to generate post ideas (for n8n or other automation)"""
    if request.method not in ['POST', 'GET']:
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate API token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    # Get parameters (support both POST and GET)
    if request.method == 'POST':
        try:
            import json as json_lib
            if request.content_type == 'application/json':
                data = json_lib.loads(request.body)
                num_ideas = data.get('num_ideas')
                provider = data.get('provider')
                model = data.get('model')
                similarity_threshold = data.get('similarity_threshold')
            else:
                num_ideas = request.POST.get('num_ideas')
                provider = request.POST.get('provider')
                model = request.POST.get('model')
                similarity_threshold = request.POST.get('similarity_threshold')
        except:
            num_ideas = request.POST.get('num_ideas')
            provider = request.POST.get('provider')
            model = request.POST.get('model')
            similarity_threshold = request.POST.get('similarity_threshold')
    else:
        num_ideas = request.GET.get('num_ideas')
        provider = request.GET.get('provider')
        model = request.GET.get('model')
        similarity_threshold = request.GET.get('similarity_threshold')
    
    # Validate required parameters
    if not num_ideas:
        return JsonResponse({'error': 'num_ideas parameter is required'}, status=400)
    
    if not provider:
        return JsonResponse({'error': 'provider parameter is required (ollama, openai, or gemini)'}, status=400)
    
    if not model:
        return JsonResponse({'error': 'model parameter is required'}, status=400)
    
    # Validate and convert num_ideas
    try:
        num_ideas = int(num_ideas)
        if num_ideas < 1 or num_ideas > 50:
            return JsonResponse({'error': 'num_ideas must be between 1 and 50'}, status=400)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'num_ideas must be a valid integer'}, status=400)
    
    # Validate provider
    if provider not in ['ollama', 'openai', 'gemini']:
        return JsonResponse({'error': 'provider must be one of: ollama, openai, gemini'}, status=400)
    
    # Validate and convert similarity_threshold (optional, default 0.70)
    if similarity_threshold is not None:
        try:
            similarity_threshold = float(similarity_threshold)
            if not 0.0 <= similarity_threshold <= 1.0:
                return JsonResponse({'error': 'similarity_threshold must be between 0.0 and 1.0'}, status=400)
        except (ValueError, TypeError):
            return JsonResponse({'error': 'similarity_threshold must be a valid number between 0.0 and 1.0'}, status=400)
    else:
        similarity_threshold = 0.70  # Default value
    
    # Generate ideas
    success, created_count, created_ideas, error_message, skipped_similar = _generate_post_ideas(
        num_ideas=num_ideas,
        provider=provider,
        model=model,
        selected_tags=None,
        selected_contents=None,
        user=None,  # API calls are system-generated
        similarity_threshold=similarity_threshold
    )
    
    if success:
        response_data = {
            'success': True,
            'created_count': created_count,
            'num_requested': num_ideas,
            'provider': provider,
            'model': model,
            'similarity_threshold': similarity_threshold,
            'ideas': created_ideas
        }
        if skipped_similar > 0:
            response_data['skipped_similar'] = skipped_similar
            response_data['message'] = f'Generated {created_count} idea(s), {skipped_similar} similar idea(s) were skipped'
        return JsonResponse(response_data)
    else:
        return JsonResponse({
            'success': False,
            'error': error_message
        }, status=500)


def agent_models_api(request):
    """API endpoint to fetch available models for a given provider"""
    provider = request.GET.get('provider', 'ollama').strip().lower()
    
    if provider == 'ollama':
        try:
            import requests
        except ImportError:
            return JsonResponse({'error': 'requests library required'}, status=500)
        
        ollama_url = getattr(settings, 'OLLAMA_URL', 'http://localhost:11434')
        url = f"{ollama_url}/api/tags"
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Extract model names
            models = []
            if 'models' in data:
                for model_info in data['models']:
                    model_name = model_info.get('name', '')
                    if model_name:
                        models.append(model_name)
            
            # Sort models (prefer Chinese models first, then smaller models)
            chinese_models = [m for m in models if any(keyword in m.lower() for keyword in ['qwen', 'chinese', 'zh', 'cn'])]
            other_models = [m for m in models if m not in chinese_models]
            
            # Within each group, prefer smaller models (3b, 4b) first
            def sort_key(model_name):
                name_lower = model_name.lower()
                # Smaller models first
                if ':3b' in name_lower or '3b' in name_lower:
                    return (0, name_lower)
                elif ':4b' in name_lower or '4b' in name_lower:
                    return (1, name_lower)
                elif ':7b' in name_lower or '7b' in name_lower:
                    return (2, name_lower)
                elif ':8b' in name_lower or '8b' in name_lower:
                    return (3, name_lower)
                else:
                    return (4, name_lower)
            
            chinese_models.sort(key=sort_key)
            other_models.sort(key=sort_key)
            sorted_models = chinese_models + other_models
            
            return JsonResponse({'models': sorted_models})
        except requests.exceptions.ConnectionError:
            return JsonResponse({'error': 'Could not connect to Ollama. Make sure Ollama is running.'}, status=503)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    
    elif provider == 'openai':
        # OpenAI models
        api_key = getattr(settings, 'OPENAI_API_KEY', None)
        if not api_key:
            return JsonResponse({'error': 'OPENAI_API_KEY is not set in settings'}, status=400)
        
        # Manual list of OpenAI models
        models = [
            'gpt-5.1',
            'gpt-5',
            'gpt-5-mini',
            'gpt-5-nano',
            'gpt-4o',
            'gpt-4o-mini',
            'gpt-4-turbo',
            'gpt-4',
            'gpt-3.5-turbo',
        ]
        return JsonResponse({'models': models})
    
    elif provider == 'gemini':
        # Gemini models
        api_key = getattr(settings, 'GEMINI_API_KEY', None)
        if not api_key:
            return JsonResponse({'error': 'GEMINI_API_KEY is not set in settings'}, status=400)
        
        # Common Gemini models
        models = [
            'gemini-3-pro-preview',
            'gemini-2.5-pro',
            'gemini-2.5-flash',
            'gemini-2.5-flash-lite'
        ]
        return JsonResponse({'models': models})
    
    else:
        return JsonResponse({'error': 'Invalid provider'}, status=400)


@login_required
def post_idea_models_api(request):
    """API endpoint to fetch available models for a given provider"""
    provider = request.GET.get('provider', 'ollama').strip().lower()
    
    if provider == 'ollama':
        # Use existing agent_models_api logic
        try:
            import requests
        except ImportError:
            return JsonResponse({'error': 'requests library required'}, status=500)
        
        ollama_url = getattr(settings, 'OLLAMA_URL', 'http://localhost:11434')
        url = f"{ollama_url}/api/tags"
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            models = []
            if 'models' in data:
                for model_info in data['models']:
                    model_name = model_info.get('name', '')
                    if model_name:
                        models.append(model_name)
            
            # Sort models
            chinese_models = [m for m in models if any(keyword in m.lower() for keyword in ['qwen', 'chinese', 'zh', 'cn'])]
            other_models = [m for m in models if m not in chinese_models]
            
            def sort_key(model_name):
                name_lower = model_name.lower()
                if ':3b' in name_lower or '3b' in name_lower:
                    return (0, name_lower)
                elif ':4b' in name_lower or '4b' in name_lower:
                    return (1, name_lower)
                elif ':7b' in name_lower or '7b' in name_lower:
                    return (2, name_lower)
                elif ':8b' in name_lower or '8b' in name_lower:
                    return (3, name_lower)
                else:
                    return (4, name_lower)
            
            chinese_models.sort(key=sort_key)
            other_models.sort(key=sort_key)
            sorted_models = chinese_models + other_models
            
            return JsonResponse({'models': sorted_models})
        except requests.exceptions.ConnectionError:
            return JsonResponse({'error': 'Could not connect to Ollama. Make sure Ollama is running.'}, status=503)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    
    elif provider == 'openai':
        # OpenAI models
        api_key = getattr(settings, 'OPENAI_API_KEY', None)
        if not api_key:
            return JsonResponse({'error': 'OPENAI_API_KEY is not set in settings'}, status=400)
        
        # Manual list of OpenAI models
        models = [
            'gpt-5.1',
            'gpt-5',
            'gpt-5-mini',
            'gpt-5-nano',
            'gpt-4o',
            'gpt-4o-mini',
            'gpt-4-turbo',
            'gpt-4',
            'gpt-3.5-turbo',
        ]
        return JsonResponse({'models': models})
    
    elif provider == 'gemini':
        # Gemini models
        api_key = getattr(settings, 'GEMINI_API_KEY', None)
        if not api_key:
            return JsonResponse({'error': 'GEMINI_API_KEY is not set in settings'}, status=400)
        
        # Common Gemini models
        models = [
            'gemini-3-pro-preview',
            'gemini-2.5-pro',
            'gemini-2.5-flash',
            'gemini-2.5-flash-lite'
        ]
        return JsonResponse({'models': models})
    
    else:
        return JsonResponse({'error': 'Invalid provider'}, status=400)


def _validate_api_token(request):
    """Helper function to validate API token from request"""
    api_token = settings.API_TOKEN
    if not api_token:
        return None, JsonResponse({
            'success': False,
            'error': 'API token not configured'
        }, status=500)
    
    # Get token from Authorization header or query parameter
    provided_token = None
    
    # Check Authorization header: "Token <token>" or "Bearer <token>"
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if auth_header:
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() in ('token', 'bearer'):
            provided_token = parts[1]
    
    # Check query parameter
    if not provided_token:
        provided_token = request.GET.get('token', None)
    
    # Validate token
    if not provided_token or provided_token != api_token:
        return None, JsonResponse({
            'success': False,
            'error': 'Invalid or missing authentication token'
        }, status=401)
    
    return True, None


@csrf_exempt
def youtube_channels_api(request):
    """API endpoint to get all YouTube channel sources (token-based authentication)"""
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    try:
        # Get all YouTube channel sources
        youtube_sources = Source.objects.filter(
            source_type='youtube',
            channel_id__isnull=False
        ).exclude(channel_id='').order_by('name')
        
        # Build response data
        channels = []
        for source in youtube_sources:
            channels.append({
                'id': source.id,
                'name': source.name,
                'channel_id': source.channel_id,
                'include_shorts': source.include_shorts
            })
        
        return JsonResponse({
            'success': True,
            'channels': channels,
            'count': len(channels)
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@csrf_exempt
def blog_sources_api(request):
    """API endpoint to get all blog sources with sitemap links (token-based authentication)"""
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    try:
        # Get all blog sources that have a sitemap link
        blog_sources = Source.objects.filter(
            source_type='blog',
            sitemap__isnull=False
        ).exclude(sitemap='').order_by('name')
        
        # Build response data
        sources = []
        for source in blog_sources:
            sources.append({
                'id': source.id,
                'name': source.name,
                'sitemap': source.sitemap,
                'filter_china': source.filter_china
            })
        
        return JsonResponse({
            'success': True,
            'sources': sources,
            'count': len(sources)
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@csrf_exempt
def create_video_content_api(request):
    """API endpoint to create video content (token-based authentication)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    try:
        data = json.loads(request.body)
        
        # Required fields
        source_id = data.get('source_id')
        external_id = data.get('external_id')  # Video ID
        title = data.get('title')
        link = data.get('link')  # YouTube URL
        
        # Optional fields
        date = data.get('date')  # YYYY-MM-DD format
        auto_process = data.get('auto_process', True)  # Whether to automatically process (extract transcript, tag, embed)
        description = data.get('description', '')  # Video description (for filtering)
        tags = data.get('tags', [])  # Video tags (for filtering) - can be list or comma-separated string
        
        # Validate required fields
        if not source_id:
            return JsonResponse({
                'success': False,
                'error': 'source_id is required'
            }, status=400)
        
        if not external_id:
            return JsonResponse({
                'success': False,
                'error': 'external_id (video ID) is required'
            }, status=400)
        
        if not title:
            return JsonResponse({
                'success': False,
                'error': 'title is required'
            }, status=400)
        
        # Get source
        try:
            source = Source.objects.get(pk=source_id, source_type='youtube')
        except Source.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': f'Source with id {source_id} not found or is not a YouTube source'
            }, status=404)
        
        # Check if content already exists
        if Content.objects.filter(source=source, external_id=external_id).exists():
            return JsonResponse({
                'success': False,
                'error': f'Video with external_id "{external_id}" already exists for this source'
            }, status=409)
        
        # Apply China filter if enabled for this source
        if source.filter_videos:
            from .youtube_service import is_video_relevant_to_china_with_details
            
            # Normalize tags - handle both list and comma-separated string
            if isinstance(tags, str):
                tags_list = [tag.strip() for tag in tags.split(',') if tag.strip()]
            elif isinstance(tags, list):
                tags_list = tags
            else:
                tags_list = []
            
            # Check if video is relevant to China
            print(f"\n{'='*60}", flush=True)
            print(f"Filtering video via API: {title}", flush=True)
            print(f"Video ID: {external_id}", flush=True)
            print(f"Source: {source.name} (Filter enabled: {source.filter_videos})", flush=True)
            print(f"Title: {title}", flush=True)
            print(f"Description: {description[:100] if description else '(none)'}...", flush=True)
            print(f"Tags: {tags_list if tags_list else '(none)'}", flush=True)
            print(f"{'='*60}\n", flush=True)
            
            is_relevant, matched_keywords, _ = is_video_relevant_to_china_with_details(
                title=title,
                description=description or '',
                tags=tags_list,
                video_id=external_id
            )
            
            if not is_relevant:
                # Extract reason from matched_keywords (may contain Ollama reasoning or be empty)
                reason_text = matched_keywords[0] if matched_keywords and len(matched_keywords) > 0 else 'Not China-related'
                
                # Log the filtering result
                log_activity(
                    'content_created',
                    f'Video "{title}" (ID: {external_id}) was filtered out - not China-related',
                    user=None,  # API request, no user
                    source=source,
                    metadata={
                        'external_id': external_id,
                        'filtered': True,
                        'reason': reason_text,
                        'title': title,
                        'description': description[:200] if description else '',
                        'tags': tags_list,
                        'matched_keywords': matched_keywords
                    }
                )
                
                print(f"  [FILTER] ✗ Video filtered out: Not China-related", flush=True)
                print(f"  [FILTER] Title: {title}", flush=True)
                if matched_keywords and len(matched_keywords) > 0:
                    # If Ollama was used, matched_keywords contains reasoning
                    if 'transcript' in matched_keywords[0].lower() or 'ollama' in matched_keywords[0].lower() or len(matched_keywords[0]) > 50:
                        print(f"  [FILTER] Reason: {matched_keywords[0][:200]}...", flush=True)
                    else:
                        print(f"  [FILTER] Matched keywords: {', '.join(matched_keywords)}", flush=True)
                else:
                    print(f"  [FILTER] No China-related keywords found in title, description, or tags", flush=True)
                    print(f"  [FILTER] Matched keywords: (none)", flush=True)
                    print(f"{'='*60}\n", flush=True)
                
                return JsonResponse({
                    'success': False,
                    'error': f'Video was filtered out - not China-related',
                    'filtered': True,
                    'reason': 'Video does not appear to be relevant to China based on title, description, and tags',
                    'matched_keywords': []
                }, status=200)  # 200 because it's not an error, just filtered
            
            # Log successful filter pass
            print(f"  [FILTER] ✓ Video passed filter: China-related", flush=True)
            print(f"  [FILTER] Title: {title}", flush=True)
            print(f"  [FILTER] Matched keywords: {', '.join(matched_keywords)}", flush=True)
            print(f"  [FILTER] Total matches: {len(matched_keywords)}", flush=True)
            print(f"{'='*60}\n", flush=True)
            
            log_activity(
                'content_created',
                f'Video "{title}" (ID: {external_id}) passed China filter',
                user=None,  # API request, no user
                source=source,
                metadata={
                    'external_id': external_id,
                    'filtered': False,
                    'filter_passed': True,
                    'title': title,
                    'matched_keywords': matched_keywords,
                    'match_count': len(matched_keywords)
                }
            )
        
        # Parse date if provided
        parsed_date = None
        if date:
            try:
                from datetime import datetime
                # Try ISO 8601 format first (with time and timezone)
                try:
                    dt = datetime.fromisoformat(date.replace('Z', '+00:00'))
                    parsed_date = dt.date()
                except ValueError:
                    # Fallback to YYYY-MM-DD format
                    parsed_date = datetime.strptime(date, '%Y-%m-%d').date()
            except ValueError:
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid date format. Use YYYY-MM-DD or ISO 8601 format (e.g., 2025-11-07T15:15:00+00:00)'
                }, status=400)
        
        # Create content
        # Note: date might be required, so use today's date if not provided
        if not parsed_date:
            from datetime import date
            parsed_date = date.today()
        
        content = Content.objects.create(
            source=source,
            external_id=external_id,
            title=title,
            link=link or f"https://www.youtube.com/watch?v={external_id}",
            content_type='video',
            date=parsed_date,
            content='',  # Empty - will be filled during processing
            processed=False,
        )
        
        # Update last_collected timestamp on source
        from django.utils import timezone
        source.last_collected = timezone.now()
        source.save(update_fields=['last_collected'])
        
        # Log content creation
        log_activity(
            'content_created',
            f'Content "{content.title}" (Video) was created via API',
            user=None,  # API request, no user
            content=content,
            source=content.source
        )
        
        # Process content if requested
        processing_results = {}
        if auto_process:
            try:
                print(f"\n{'='*60}", flush=True)
                print(f"Processing video content via API: {content.title}", flush=True)
                print(f"Video ID: {content.external_id}", flush=True)
                print(f"{'='*60}\n", flush=True)
                
                processing_service = ContentProcessingService(use_proxy=True)
                processing_results = processing_service.process_content(
                    content,
                    extract=True,
                    translate=True,
                    tag=True,
                    embed=True
                )
                content.refresh_from_db()
                
                print(f"\n{'='*60}", flush=True)
                print(f"Processing results: {processing_results}", flush=True)
                print(f"Has content: {content.has_content}, Processed: {content.processed}", flush=True)
                print(f"{'='*60}\n", flush=True)
            except Exception as e:
                # Log error but don't fail the request
                import traceback
                error_trace = traceback.format_exc()
                print(f"Error processing content {content.id}: {str(e)}", flush=True)
                print(error_trace, flush=True)
                processing_results = {'error': str(e), 'traceback': error_trace}
        
        return JsonResponse({
            'success': True,
            'content': {
                'id': content.id,
                'title': content.title,
                'external_id': content.external_id,
                'link': content.link,
                'date': str(content.date) if content.date else None,
                'has_content': content.has_content,
                'processed': content.processed,
            },
            'processing': processing_results
        }, status=201)
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON in request body'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@csrf_exempt
def create_blog_post_api(request):
    """API endpoint to create blog post content (token-based authentication)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Validate token
    token_valid, error_response = _validate_api_token(request)
    if not token_valid:
        return error_response
    
    try:
        data = json.loads(request.body)
        
        # Required fields
        source_id = data.get('source_id')
        title = data.get('title')
        link = data.get('link')
        
        # Optional fields
        date = data.get('date')  # YYYY-MM-DD format or ISO 8601
        auto_process = data.get('auto_process', True)  # Whether to automatically process (extract content, translate, tag, embed)
        
        # Validate required fields
        if not source_id:
            return JsonResponse({
                'success': False,
                'error': 'source_id is required'
            }, status=400)
        
        if not title:
            return JsonResponse({
                'success': False,
                'error': 'title is required'
            }, status=400)
        
        if not link:
            return JsonResponse({
                'success': False,
                'error': 'link is required'
            }, status=400)
        
        # Get source
        try:
            source = Source.objects.get(pk=source_id, source_type='blog')
        except Source.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': f'Source with id {source_id} not found or is not a blog source'
            }, status=404)
        
        # Use link as external_id for blog posts (URL is unique identifier)
        external_id = link
        
        # Check if content already exists
        if Content.objects.filter(source=source, external_id=external_id).exists():
            return JsonResponse({
                'success': False,
                'error': f'Blog post with link "{link}" already exists for this source'
            }, status=409)
        
        # Parse date if provided
        parsed_date = None
        if date:
            try:
                from datetime import datetime
                # Try ISO 8601 format first (with time and timezone)
                try:
                    dt = datetime.fromisoformat(date.replace('Z', '+00:00'))
                    parsed_date = dt.date()
                except ValueError:
                    # Fallback to YYYY-MM-DD format
                    parsed_date = datetime.strptime(date, '%Y-%m-%d').date()
            except ValueError:
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid date format. Use YYYY-MM-DD or ISO 8601 format (e.g., 2025-11-07T15:15:00+00:00)'
                }, status=400)
        
        # Use today's date if not provided
        if not parsed_date:
            from datetime import date
            parsed_date = date.today()
        
        # Create content
        content = Content.objects.create(
            source=source,
            external_id=external_id,
            title=title,
            link=link,
            content_type='blog_post',
            date=parsed_date,
            content='',  # Empty - will be filled during processing
            processed=False,
        )
        
        # Update last_collected timestamp on source
        from django.utils import timezone
        source.last_collected = timezone.now()
        source.save(update_fields=['last_collected'])
        
        # Log content creation
        log_activity(
            'content_created',
            f'Content "{content.title}" (Blog Post) was created via API',
            user=None,  # API request, no user
            content=content,
            source=content.source
        )
        
        # Process content if requested
        processing_results = {}
        if auto_process:
            try:
                print(f"\n{'='*60}", flush=True)
                print(f"Processing blog post content via API: {content.title}", flush=True)
                print(f"Link: {content.link}", flush=True)
                print(f"{'='*60}\n", flush=True)
                
                processing_service = ContentProcessingService(use_proxy=True)
                processing_results = processing_service.process_content(
                    content,
                    extract=True,
                    translate=True,
                    tag=True,
                    embed=True
                )
                
                print(f"\n{'='*60}", flush=True)
                print(f"Processing completed for: {content.title}", flush=True)
                print(f"Extracted: {processing_results.get('extracted', False)}", flush=True)
                print(f"Translated: {processing_results.get('translated', False)}", flush=True)
                print(f"Tagged: {processing_results.get('tagged', False)}", flush=True)
                print(f"Embedded: {processing_results.get('embedded', False)}", flush=True)
                print(f"{'='*60}\n", flush=True)
                
            except Exception as e:
                print(f"  [API] ⚠️  Error during processing: {str(e)}", flush=True)
                import traceback
                print(f"  [API] Traceback: {traceback.format_exc()}", flush=True)
                processing_results = {
                    'error': str(e),
                    'extracted': False,
                    'translated': False,
                    'tagged': False,
                    'embedded': False
                }
        
        return JsonResponse({
            'success': True,
            'content_id': content.id,
            'message': 'Blog post created successfully',
            'processing': processing_results if auto_process else None
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON in request body'
        }, status=400)
    except Exception as e:
        import traceback
        print(f"  [API] ❌ Error creating blog post: {str(e)}", flush=True)
        print(f"  [API] Traceback: {traceback.format_exc()}", flush=True)
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
def agent_chat_api(request):
    """API endpoint for chat messages"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    try:
        data = json.loads(request.body)
        question = data.get('question', '').strip()
        provider = data.get('provider', 'ollama').strip().lower()
        model = data.get('model', '').strip()
        num_chunks = int(data.get('num_chunks', 5))
        source_id = data.get('source_id')
        tag_ids = data.get('tag_ids', [])
        content_type = data.get('content_type')
        conversation_history = data.get('conversation_history', [])
        web_search_enabled = data.get('web_search_enabled', False)
        
        # Validate inputs
        if not question:
            return JsonResponse({'error': 'Question is required'}, status=400)
        
        if provider not in ['ollama', 'openai', 'gemini']:
            return JsonResponse({'error': 'Invalid provider. Must be one of: ollama, openai, gemini'}, status=400)
        
        if not model:
            return JsonResponse({'error': 'Model is required'}, status=400)
        
        if num_chunks < 1 or num_chunks > 20:
            num_chunks = 5
        
        # Convert source_id to int if provided
        if source_id:
            try:
                source_id = int(source_id)
            except (ValueError, TypeError):
                source_id = None
        
        # Convert tag_ids to list of ints
        if tag_ids:
            try:
                tag_ids = [int(tid) for tid in tag_ids if tid]
            except (ValueError, TypeError):
                tag_ids = []
        
        # Initialize RAG service
        try:
            rag_service = RAGService()
        except ValueError as e:
            # Handle missing OpenAI API key
            if "OPENAI_API_KEY" in str(e):
                return JsonResponse({
                    'error': str(e),
                    'success': False,
                    'error_type': 'missing_openai_key'
                }, status=400)
            raise
        
        # Generate response
        try:
            answer, sources = rag_service.generate_response(
                question=question,
                provider=provider,
                model=model,
                num_chunks=num_chunks,
                source_id=source_id,
                tag_ids=tag_ids if tag_ids else None,
                content_type=content_type,
                conversation_history=conversation_history,
                web_search_enabled=web_search_enabled
            )
            
            return JsonResponse({
                'answer': answer,
                'sources': sources,
                'success': True
            })
        except Exception as e:
            return JsonResponse({
                'error': str(e),
                'success': False
            }, status=500)
    
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# Logs View
@login_required
def logs_view(request):
    """Display activity logs"""
    logs = ActivityLog.objects.select_related('source', 'content').all()
    
    # Filtering
    activity_type_filter = request.GET.get('activity_type', '').strip()
    if activity_type_filter:
        logs = logs.filter(activity_type=activity_type_filter)
    
    # Pagination
    paginator = Paginator(logs, 50)  # Show 50 logs per page
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    # Get activity type choices for filter
    activity_types = ActivityLog.ACTIVITY_TYPE_CHOICES
    
    context = {
        'logs': page_obj,
        'activity_types': activity_types,
        'activity_type_filter': activity_type_filter,
    }
    return render(request, 'sources/logs.html', context)


@login_required
def settings_view(request):
    """View and edit application settings"""
    settings = Settings.get_settings()
    
    if request.method == 'POST':
        form = SettingsForm(request.POST, instance=settings)
        if form.is_valid():
            form.save()
            log_activity(
                'settings_updated',
                'Application settings were updated',
                user=request.user,
                metadata={
                    'tagging_provider': form.cleaned_data.get('default_tagging_provider'),
                    'tagging_model': form.cleaned_data.get('default_tagging_model'),
                }
            )
            messages.success(request, 'Settings updated successfully!')
            return redirect('sources:settings')
    else:
        form = SettingsForm(instance=settings)
    
    context = {
        'form': form,
        'settings': settings,
    }
    return render(request, 'sources/settings.html', context)


def _parse_and_create_blog_post_images(blog_post):
    """
    Parse images from blog post content and create BlogPostImage records.
    Looks for <img> tags with data-filename attribute.
    """
    import re
    content = blog_post.content
    
    # Pattern to match <img> tags with data-filename attribute
    # Matches: <img src="#" alt="..." class="..." data-filename="filename.jpg">
    img_pattern = r'<img[^>]*data-filename=["\']([^"\']+)["\'][^>]*>'
    
    matches = re.finditer(img_pattern, content, re.IGNORECASE)
    
    for match in matches:
        filename = match.group(1)
        img_tag = match.group(0)
        
        # Extract alt text from the img tag
        alt_match = re.search(r'alt=["\']([^"\']*)["\']', img_tag, re.IGNORECASE)
        alt_text = alt_match.group(1) if alt_match else ''
        
        # Create or update BlogPostImage record
        BlogPostImage.objects.get_or_create(
            blog_post=blog_post,
            filename=filename,
            defaults={
                'alt_text': alt_text,
                'is_featured': False
            }
        )
    
    # Also create a record for featured image if it exists
    if blog_post.featured_image:
        BlogPostImage.objects.get_or_create(
            blog_post=blog_post,
            filename='featured_image',
            defaults={
                'alt_text': blog_post.featured_image_description or '',
                'image_file': blog_post.featured_image,
                'is_featured': True
            }
        )


@login_required
def blog_post_images_list(request):
    """Display list of all blog post images with upload status"""
    # Get query parameters
    status_filter = request.GET.get('status', '').strip()
    post_id = request.GET.get('post', '').strip()
    
    # Get all blog posts with their images
    blog_posts = BlogPost.objects.prefetch_related('images').select_related('post_idea').order_by('-created_at')
    
    # Collect all images
    all_images = []
    for post in blog_posts:
        # Get images from content
        content_images = post.images.filter(is_featured=False)
        for img in content_images:
            all_images.append({
                'id': img.id,
                'blog_post': post,
                'filename': img.filename,
                'alt_text': img.alt_text,
                'image_file': img.image_file,
                'is_featured': False,
                'has_file': bool(img.image_file),
            })
        
        # Add featured image if it exists or if there's a description
        if post.featured_image or post.featured_image_description:
            featured_img = post.images.filter(is_featured=True).first()
            all_images.append({
                'id': featured_img.id if featured_img else None,
                'blog_post': post,
                'filename': 'featured_image',
                'alt_text': post.featured_image_description or '',
                'image_file': post.featured_image if post.featured_image else (featured_img.image_file if featured_img else None),
                'is_featured': True,
                'has_file': bool(post.featured_image or (featured_img and featured_img.image_file)),
            })
    
    # If no filters are provided, default to oldest post with missing images
    if not post_id and not status_filter:
        # Get all blog posts ordered by created_at (oldest first)
        all_posts_ordered = BlogPost.objects.prefetch_related('images').select_related('post_idea').order_by('created_at')
        
        # Find the oldest post that has missing images
        for post in all_posts_ordered:
            # Check if this post has any missing images
            post_images = [img for img in all_images if img['blog_post'].id == post.id]
            has_missing = any(not img['has_file'] for img in post_images)
            
            if has_missing:
                post_id = str(post.id)
                all_images = post_images
                break
    
    # Filter by status if provided
    if status_filter == 'missing':
        all_images = [img for img in all_images if not img['has_file']]
    elif status_filter == 'uploaded':
        all_images = [img for img in all_images if img['has_file']]
    
    # Filter by blog post if provided
    if post_id:
        try:
            post = BlogPost.objects.get(pk=post_id)
            all_images = [img for img in all_images if img['blog_post'].id == post.id]
        except BlogPost.DoesNotExist:
            pass
    
    # Pagination
    paginator = Paginator(all_images, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Get all blog posts for filter dropdown
    all_blog_posts = BlogPost.objects.all().order_by('-created_at')
    
    # Statistics
    total_images = len(all_images)
    missing_images = len([img for img in all_images if not img['has_file']])
    uploaded_images = len([img for img in all_images if img['has_file']])
    
    context = {
        'page_obj': page_obj,
        'images': page_obj,
        'all_blog_posts': all_blog_posts,
        'status_filter': status_filter,
        'selected_post_id': post_id,
        'total_images': total_images,
        'missing_images': missing_images,
        'uploaded_images': uploaded_images,
    }
    return render(request, 'sources/blog_post_images_list.html', context)


@login_required
def blog_post_image_upload(request, pk):
    """Upload or update an image for a blog post"""
    blog_post = get_object_or_404(BlogPost, pk=pk)
    
    if request.method == 'POST':
        filename = request.POST.get('filename', '').strip()
        is_featured = request.POST.get('is_featured', 'false') == 'true'
        image_file = request.FILES.get('image_file')
        
        if not filename:
            messages.error(request, 'Filename is required.')
            return redirect('sources:blog_post_images_list')
        
        if not image_file:
            messages.error(request, 'Please select an image file to upload.')
            return redirect('sources:blog_post_images_list')
        
        try:
            if is_featured:
                # Update featured image
                blog_post.featured_image = image_file
                blog_post.save(update_fields=['featured_image'])
                
                # Update or create BlogPostImage record
                blog_post_image, created = BlogPostImage.objects.get_or_create(
                    blog_post=blog_post,
                    filename='featured_image',
                    defaults={'is_featured': True}
                )
                blog_post_image.image_file = image_file
                blog_post_image.is_featured = True
                blog_post_image.save()
                
                messages.success(request, 'Featured image uploaded successfully!')
            else:
                # Update content image
                blog_post_image = get_object_or_404(
                    BlogPostImage,
                    blog_post=blog_post,
                    filename=filename
                )
                blog_post_image.image_file = image_file
                blog_post_image.save()
                
                # Update the content to replace the image src
                import re
                old_pattern = rf'<img([^>]*data-filename=["\']{re.escape(filename)}["\'][^>]*)>'
                new_src = blog_post_image.image_file.url
                
                def replace_img_src(match):
                    img_tag = match.group(0)
                    # Replace src="#" with actual URL
                    if 'src="#"' in img_tag or 'src=\'#\'' in img_tag:
                        img_tag = re.sub(r'src=["\']#["\']', f'src="{new_src}"', img_tag)
                    elif 'src=' not in img_tag:
                        # Add src if it doesn't exist
                        img_tag = img_tag.replace('<img', f'<img src="{new_src}"')
                    else:
                        # Replace existing src
                        img_tag = re.sub(r'src=["\'][^"\']*["\']', f'src="{new_src}"', img_tag)
                    return img_tag
                
                blog_post.content = re.sub(old_pattern, replace_img_src, blog_post.content)
                blog_post.save(update_fields=['content'])
                
                messages.success(request, f'Image "{filename}" uploaded successfully!')
            
            log_activity(
                'blog_post_image_uploaded',
                f'Image "{filename}" uploaded for blog post "{blog_post.title}"',
                user=request.user,
                metadata={
                    'blog_post_id': blog_post.id,
                    'filename': filename,
                    'is_featured': is_featured
                }
            )
        except Exception as e:
            messages.error(request, f'Error uploading image: {str(e)}')
        
        return redirect('sources:blog_post_images_list')
    
    # Get all images for this blog post
    images = blog_post.images.all()
    
    context = {
        'blog_post': blog_post,
        'images': images,
    }
    return render(request, 'sources/blog_post_image_upload.html', context)