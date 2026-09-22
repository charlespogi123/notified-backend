from django.urls import path
from . import views

urlpatterns = [
    path('fetch/', views.fetch_from_source, name='fetch_from_source'),
    path('interpret/', views.interpret_pending_content, name='interpret_pending_content'),
    path('webhook/event-approved/', views.event_approved_webhook, name='event_approved_webhook'),
]