from django.urls import path
from . import views

app_name = 'sources'

urlpatterns = [
    # Dashboard
    path('', views.dashboard, name='dashboard'),
    # Source URLs
    path('sources/', views.source_list, name='source_list'),
    path('sources/add/', views.source_add, name='source_add'),
    path('sources/<int:pk>/edit/', views.source_edit, name='source_edit'),
    path('sources/<int:pk>/delete/', views.source_delete, name='source_delete'),
    
    # Content URLs
    path('contents/', views.content_list, name='content_list'),
    path('contents/add/', views.content_add, name='content_add'),
    path('contents/<int:pk>/', views.content_detail, name='content_detail'),
    path('contents/<int:pk>/edit/', views.content_edit, name='content_edit'),
    path('contents/<int:pk>/delete/', views.content_delete, name='content_delete'),
    
    # Agent URLs
    path('agent/', views.agent_view, name='agent'),
    path('api/agent/chat/', views.agent_chat_api, name='agent_chat_api'),
    path('api/agent/models/', views.agent_models_api, name='agent_models_api'),
    
    # API URLs
    path('api/youtube-channels/', views.youtube_channels_api, name='youtube_channels_api'),
    path('api/blog-sources/', views.blog_sources_api, name='blog_sources_api'),
    path('api/video-content/', views.create_video_content_api, name='create_video_content_api'),
    path('api/video-content', views.create_video_content_api, name='create_video_content_api_no_slash'),  # Support both with and without trailing slash
    
    # Logs
    path('logs/', views.logs_view, name='logs'),
    
    # Settings
    path('settings/', views.settings_view, name='settings'),
]


