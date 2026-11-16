from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ConfirmIntentView, ExtractIntentView, PostViewSet

router = DefaultRouter()
router.register(r'posts', PostViewSet, basename='post')

urlpatterns = [
    path('extract-intent/', ExtractIntentView.as_view(), name='extract_intent'),
    path('confirm-intent/', ConfirmIntentView.as_view(), name='confirm_intent'),
    path('', include(router.urls)),
]
