from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers

from matches.serializers import MatchSerializer
from posts.models import Post

from .models import Chat, ChatMessage, is_identity_revealed


def _verification_badge(user) -> str:
    if user.is_verified_human and user.is_verified_adult_18_plus:
        return 'Verified 18+ Human'
    if user.is_verified_human:
        return 'Verified Human'
    return 'Unverified'


def _user_confirmed_tags(user, chat: Chat) -> list[str]:
    now = timezone.now()
    qs = Post.objects.filter(user=user).filter(Q(expires_at__gt=now) | Q(expires_at__isnull=True))
    if chat.match_id and chat.match and chat.match.activity_bucket:
        qs = qs.filter(activity_bucket=chat.match.activity_bucket)
    elif chat.micro_table_id and chat.micro_table:
        qs = qs.filter(activity_bucket=chat.micro_table.activity_bucket)
    post = qs.order_by('-created_at').first()
    if post:
        return list(post.confirmed_tags or post.inferred_interests or [])
    return []


def serialize_chat_participant(user, chat: Chat, viewer, mask_all: bool = False) -> dict:
    revealed = False if mask_all else is_identity_revealed(chat, viewer, user)
    return {
        'id': user.id,
        'ephemeral_handle': user.ephemeral_handle,
        'verification_badge': _verification_badge(user),
        'is_verified_human': user.is_verified_human,
        'is_verified_adult_18_plus': user.is_verified_adult_18_plus,
        'confirmed_tags': _user_confirmed_tags(user, chat),
        'e2ee_public_key': user.e2ee_public_key,
        'is_revealed': revealed,
        'username': user.username if revealed else None,
        'revealed_username': user.username if revealed else None,
    }


class ChatMessageSerializer(serializers.ModelSerializer):
    sender = serializers.SerializerMethodField()
    sender_id = serializers.IntegerField(write_only=True, required=False)

    class Meta:
        model = ChatMessage
        fields = [
            'id',
            'chat',
            'sender',
            'sender_id',
            'content',
            'ciphertext',
            'nonce',
            'timestamp',
        ]
        read_only_fields = ['id', 'chat', 'sender', 'timestamp']

    def get_sender(self, obj):
        request = self.context.get('request')
        mask_all = self.context.get('mask_all_identities', False)
        viewer = getattr(request, 'user', None) if request else None
        return serialize_chat_participant(
            obj.sender,
            obj.chat,
            viewer=viewer,
            mask_all=mask_all,
        )


class ChatSerializer(serializers.ModelSerializer):
    match = MatchSerializer(read_only=True)
    last_message = serializers.SerializerMethodField()
    is_expired = serializers.ReadOnlyField()
    participants = serializers.SerializerMethodField()
    messages = serializers.SerializerMethodField()

    match_id = serializers.IntegerField(write_only=True, required=False)

    class Meta:
        model = Chat
        fields = [
            'id',
            'room_uuid',
            'match',
            'match_id',
            'micro_table',
            'participants',
            'messages',
            'created_at',
            'last_activity',
            'expires_at',
            'is_expired',
            'last_message',
        ]
        read_only_fields = [
            'id',
            'room_uuid',
            'created_at',
            'last_activity',
            'expires_at',
            'is_expired',
            'last_message',
            'match',
            'micro_table',
            'participants',
            'messages',
        ]

    def get_participants(self, obj):
        request = self.context.get('request')
        mask_all = self.context.get('mask_all_identities', False)
        viewer = getattr(request, 'user', None) if request else None
        return [
            serialize_chat_participant(p, obj, viewer=viewer, mask_all=mask_all)
            for p in obj.get_participants()
        ]

    def get_messages(self, obj):
        if not self.context.get('include_messages', False):
            return []
        qs = obj.messages.select_related('sender').order_by('timestamp')
        return ChatMessageSerializer(qs, many=True, context=self.context).data

    def get_last_message(self, obj):
        last_msg = obj.messages.select_related('sender').order_by('-timestamp').first()
        if last_msg:
            return ChatMessageSerializer(last_msg, context=self.context).data
        return None
