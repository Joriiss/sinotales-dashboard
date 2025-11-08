"""
URL configuration for china-blog-dashboard project.
"""
from django.contrib import admin
from django.urls import path, include
from sources.views import CustomLoginView, CustomLogoutView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', CustomLoginView.as_view(), name='login'),
    path('logout/', CustomLogoutView.as_view(), name='logout'),
    path('', include('sources.urls')),
]


