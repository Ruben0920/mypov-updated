from django.urls import path

from .views import (
    ChatRoomDetailView,
    ChatRoomMessagesView,
    CreateChatMessageView,
    ListCreateChatsView,
    RequestRevealView,
    RetrieveChatMessagesView,
)

urlpatterns = [
    path('', ListCreateChatsView.as_view(), name='list_create_chats'),
    path('messages/send/', CreateChatMessageView.as_view(), name='create_chat_message'),
    path('<int:chat_id>/messages/', RetrieveChatMessagesView.as_view(), name='retrieve_chat_messages'),
    path('rooms/', ListCreateChatsView.as_view(), name='chat_room_list'),
    path('rooms/<str:room_id>/', ChatRoomDetailView.as_view(), name='chat_room_detail'),
    path('rooms/<str:room_id>/messages/', ChatRoomMessagesView.as_view(), name='chat_room_messages'),
    path('rooms/<str:room_id>/request-reveal/', RequestRevealView.as_view(), name='chat_room_request_reveal'),
]
