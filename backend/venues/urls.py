from django.urls import path

from .views import (
    VenueDiscoverView,
    VenueQRCheckinView,
    VenueROISummaryView,
    VenueVibeSubmitView,
)

urlpatterns = [
    path('discover/', VenueDiscoverView.as_view(), name='venue_discover'),
    path('qr-checkin/', VenueQRCheckinView.as_view(), name='venue_qr_checkin'),
    path('<uuid:venue_id>/vibe/', VenueVibeSubmitView.as_view(), name='venue_vibe_submit'),
    path('<uuid:venue_id>/roi-summary/', VenueROISummaryView.as_view(), name='venue_roi_summary'),
]
