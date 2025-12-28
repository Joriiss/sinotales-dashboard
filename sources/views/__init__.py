# Import all views for backward compatibility
from .auth import CustomLoginView, CustomLogoutView
from .dashboard import dashboard
from .sources import source_list, source_add, source_edit, source_delete
from .content import content_list, content_add, content_edit, content_detail, content_delete
from .post_ideas import (
    post_idea_list, post_idea_generate, post_idea_add, post_idea_edit,
    post_idea_delete, post_idea_search_content_api, post_idea_models_api,
    _generate_post_ideas
)
from .blog_posts import (
    blog_post_list, blog_post_detail, blog_post_edit, blog_post_delete,
    blog_post_generate_metadata, blog_post_generate,
    blog_post_images_list, blog_post_image_upload,
    _parse_and_create_blog_post_images
)
from .api import (
    youtube_channels_api, blog_sources_api, create_video_content_api,
    create_blog_post_api, post_ideas_api, blog_posts_api,
    blog_posts_export_wordpress_api, blog_post_update_status_api,
    check_idea_similarity_api, create_post_idea_api, get_idea_context_api,
    generate_blog_post_api, post_idea_generate_api, agent_models_api
)
from .misc import agent_view, agent_chat_api, logs_view, settings_view

# Export helper functions that might be used elsewhere
from .utils import (
    _validate_api_token, _parse_blog_content_sections,
    _format_acf_field, _format_faq_acf_fields
)

