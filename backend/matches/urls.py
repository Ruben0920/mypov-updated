from django.urls import path

from .views import (
    GenerateMatchesView,
    GetAIMatchesView,
    ImageUploadView,
    InteractAIMatchView,
    MicroTableClaimSeatView,
    MicroTableListView,
)

urlpatterns = [
    path('get_post_objects/', ImageUploadView.as_view(), name='get_post_objects'),
    path('ai-matches/', GetAIMatchesView.as_view(), name='get_ai_matches'),
    path('ai-matches/<int:match_id>/interact/', InteractAIMatchView.as_view(), name='interact_ai_match'),
    path('generate/', GenerateMatchesView.as_view(), name='generate_matches'),
    path('micro-tables/', MicroTableListView.as_view(), name='micro_table_list'),
    path(
        'micro-tables/<uuid:table_id>/claim-seat/',
        MicroTableClaimSeatView.as_view(),
        name='micro_table_claim_seat',
    ),
]
