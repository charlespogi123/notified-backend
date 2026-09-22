from django.urls import path
from . import views

urlpatterns = [
    path('posts/', views.mock_posts, name='mock_posts'),
]