from django.urls import path
from . import views

app_name = 'sources'

urlpatterns = [
    path('', views.source_list, name='source_list'),
    path('add/', views.source_add, name='source_add'),
    path('<int:pk>/edit/', views.source_edit, name='source_edit'),
    path('<int:pk>/delete/', views.source_delete, name='source_delete'),
]


