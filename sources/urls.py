from django.urls import path
from . import views

app_name = 'sources'

urlpatterns = [
    # Source URLs
    path('', views.source_list, name='source_list'),
    path('add/', views.source_add, name='source_add'),
    path('<int:pk>/edit/', views.source_edit, name='source_edit'),
    path('<int:pk>/delete/', views.source_delete, name='source_delete'),
    
    # Content URLs
    path('contents/', views.content_list, name='content_list'),
    path('contents/add/', views.content_add, name='content_add'),
    path('contents/<int:pk>/', views.content_detail, name='content_detail'),
    path('contents/<int:pk>/edit/', views.content_edit, name='content_edit'),
    path('contents/<int:pk>/delete/', views.content_delete, name='content_delete'),
]


