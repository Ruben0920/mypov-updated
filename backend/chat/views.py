import uuid

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from matches.models import Match, MicroTableSeat

from .models import Chat, ChatMessage, ChatParticipantConsent, is_identity_revealed
from .serializers import ChatMessageSerializer, ChatSerializer, serialize_chat_participant
from .services.pusher_service import trigger_chat_updated, trigger_new_message


def _resolve_chat_room(room_id: str) -> Chat:
    try:
        parsed_uuid = uuid.UUID(str(room_id))
        return get_object_or_404(Chat, room_uuid=parsed_uuid)
    except ValueError:
        try:
            numeric_id = int(room_id)
        except ValueError:
            return get_object_or_404(Chat, pk=-1)
        return get_object_or_404(Chat, pk=numeric_id)


def _user_has_room_access(chat: Chat, user) -> bool:
    if chat.match_id and chat.match:
        if chat.match.status == 'blocked':
            return False
        return user.pk in (chat.match.user1_id, chat.match.user2_id)
    if chat.micro_table_id:
        return MicroTableSeat.objects.filter(
            table_id=chat.micro_table_id,
            user_id=user.pk,
        ).exists()
    return False


class ListCreateChatsView(generics.ListAPIView):
    """
    Lists active 1-on-1 and Micro-Table chat rooms for the current user.
    """

    serializer_class = ChatSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        now = timezone.now()
        if user.expires_at and user.expires_at < now:
            return Chat.objects.none()

        active_chats = (
            Chat.objects.filter(
                Q(match__user1=user, match__status='matched')
                | Q(match__user2=user, match__status='matched')
                | Q(micro_table__seats__user=user)
            )
            .select_related('match', 'micro_table')
            .distinct()
        )
        non_expired_chats = [chat for chat in active_chats if not chat.is_expired]
        return sorted(non_expired_chats, key=lambda c: c.last_activity, reverse=True)


class RetrieveChatMessagesView(generics.ListAPIView):
    serializer_class = ChatMessageSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        chat_id = self.kwargs.get('chat_id')
        now = timezone.now()

        if user.expires_at and user.expires_at < now:
            return ChatMessage.objects.none()

        chat = get_object_or_404(Chat, id=chat_id)
        if not _user_has_room_access(chat, user) or chat.is_expired:
            return ChatMessage.objects.none()

        return chat.messages.select_related('sender').order_by('timestamp')

    def list(self, request, *args, **kwargs):
        chat_id = self.kwargs.get('chat_id')
        chat = get_object_or_404(Chat, id=chat_id)
        if chat.is_expired:
            return Response({'detail': 'This chat has expired.'}, status=status.HTTP_403_FORBIDDEN)
        if not _user_has_room_access(chat, request.user):
            return Response({'detail': 'You do not have access to this chat.'}, status=status.HTTP_403_FORBIDDEN)

        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True, context={'request': request})
        return Response(serializer.data)


class CreateChatMessageView(generics.CreateAPIView):
    serializer_class = ChatMessageSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        user = self.request.user
        match_id = self.request.data.get('match_id')
        content = self.request.data.get('content')
        ciphertext = self.request.data.get('ciphertext', '')
        nonce = self.request.data.get('nonce', '')
        now = timezone.now()

        if user.expires_at and user.expires_at < now:
            raise PermissionDenied('Your account has expired.')

        if not match_id or (not content and not ciphertext):
            raise ValidationError('Match ID and message content or ciphertext are required.')

        match = get_object_or_404(Match, id=match_id, status='matched')
        if user != match.user1 and user != match.user2:
            raise PermissionDenied('You are not part of this match.')

        chat, _ = Chat.objects.get_or_create(
            match=match,
            defaults={'last_activity': now},
        )
        message = serializer.save(
            sender=user,
            chat=chat,
            content=content,
            ciphertext=ciphertext,
            nonce=nonce,
        )
        chat.last_activity = message.timestamp
        chat.save(update_fields=['last_activity'])

        # Never leak real usernames over Pusher room broadcasts
        message_data = ChatMessageSerializer(
            message,
            context={'mask_all_identities': True},
        ).data
        trigger_new_message(chat.id, message_data)

        chat_summary_data = ChatSerializer(
            chat,
            context={'mask_all_identities': True},
        ).data
        for participant in chat.get_participants():
            if participant != user:
                trigger_chat_updated(participant.id, chat_summary_data)

    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except PermissionDenied as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)


class ChatRoomDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, room_id, *args, **kwargs):
        chat = _resolve_chat_room(room_id)
        if chat.is_expired:
            return Response({'detail': 'This chat room has expired.'}, status=status.HTTP_403_FORBIDDEN)
        if not _user_has_room_access(chat, request.user):
            return Response({'detail': 'You do not have access to this chat room.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = ChatSerializer(
            chat,
            context={'request': request, 'include_messages': True},
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


class ChatRoomMessagesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, room_id, *args, **kwargs):
        chat = _resolve_chat_room(room_id)
        if chat.is_expired:
            return Response({'detail': 'This chat room has expired.'}, status=status.HTTP_403_FORBIDDEN)
        if not _user_has_room_access(chat, request.user):
            return Response({'detail': 'You do not have access to this chat room.'}, status=status.HTTP_403_FORBIDDEN)

        ciphertext = (request.data.get('ciphertext') or '').strip()
        nonce = (request.data.get('nonce') or '').strip()
        content = request.data.get('content')

        if not ciphertext and not content:
            return Response(
                {'error': 'Either ciphertext + nonce or content is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        message = ChatMessage.objects.create(
            chat=chat,
            sender=request.user,
            content=content,
            ciphertext=ciphertext,
            nonce=nonce,
        )
        chat.last_activity = message.timestamp
        chat.save(update_fields=['last_activity'])

        masked_payload = ChatMessageSerializer(
            message,
            context={'mask_all_identities': True},
        ).data
        trigger_new_message(str(chat.room_uuid), masked_payload)

        return Response(
            ChatMessageSerializer(message, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class RequestRevealView(APIView):
    """
    Two-way mutual identity reveal endpoint.
    Marks the calling user's consent; real usernames remain masked until BOTH
    participants mutually consent.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, room_id, *args, **kwargs):
        chat = _resolve_chat_room(room_id)
        if chat.is_expired:
            return Response({'detail': 'This chat room has expired.'}, status=status.HTTP_403_FORBIDDEN)
        if not _user_has_room_access(chat, request.user):
            return Response({'detail': 'You do not have access to this chat room.'}, status=status.HTTP_403_FORBIDDEN)

        target_user = None
        target_user_id = request.data.get('target_user_id')
        if target_user_id is not None:
            participants = chat.get_participants()
            target_user = next((p for p in participants if p.pk == int(target_user_id)), None)
            if target_user is None or target_user.pk == request.user.pk:
                return Response(
                    {'error': 'Invalid target_user_id for this chat room.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        consent, _ = ChatParticipantConsent.objects.get_or_create(
            chat=chat,
            user=request.user,
            target_user=target_user,
        )
        if not consent.consented_to_reveal:
            consent.consented_to_reveal = True
            consent.consented_at = timezone.now()
            consent.save(update_fields=['consented_to_reveal', 'consented_at'])

        other_participants = [p for p in chat.get_participants() if p.pk != request.user.pk]
        if target_user is not None:
            mutually_revealed = is_identity_revealed(chat, request.user, target_user)
        else:
            mutually_revealed = any(
                is_identity_revealed(chat, request.user, p) for p in other_participants
            )

        participants_data = [
            serialize_chat_participant(p, chat, viewer=request.user)
            for p in chat.get_participants()
        ]
        return Response(
            {
                'room_uuid': str(chat.room_uuid),
                'consented_to_reveal': True,
                'mutually_revealed': mutually_revealed,
                'participants': participants_data,
            },
            status=status.HTTP_200_OK,
        )
