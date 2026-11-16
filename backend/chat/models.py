from datetime import timedelta
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Chat(models.Model):
    """
    Represents an ephemeral encrypted conversation room for either a 1-on-1 Match
    or a 3-6 person MicroTable.
    """

    room_uuid = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    match = models.OneToOneField(
        'matches.Match',
        on_delete=models.CASCADE,
        related_name='chat_session',
        null=True,
        blank=True,
    )
    micro_table = models.OneToOneField(
        'matches.MicroTable',
        on_delete=models.CASCADE,
        related_name='chat_room',
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)

    def get_participants(self):
        if self.match_id and self.match:
            return [self.match.user1, self.match.user2]
        if self.micro_table_id and self.micro_table:
            return [
                seat.user
                for seat in self.micro_table.seats.select_related('user').order_by('joined_at')
            ]
        return []

    @property
    def is_expired(self) -> bool:
        now = timezone.now()
        if self.expires_at is not None:
            return now > self.expires_at
        return now > self.last_activity + timedelta(hours=24)

    def __str__(self):
        if self.match_id and self.match:
            return f"Chat for Match ID {self.match_id}"
        if self.micro_table_id:
            return f"Group Chat for MicroTable {self.micro_table_id}"
        return f"Chat ID {self.id}"

    class Meta:
        ordering = ['-last_activity']


ChatRoom = Chat


class ChatParticipantConsent(models.Model):
    """
    Tracks two-way mutual-consent identity reveal per participant (or pair inside a Micro-Table).
    """

    chat = models.ForeignKey(
        Chat,
        on_delete=models.CASCADE,
        related_name='reveal_consents',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='chat_reveal_consents',
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='targeted_reveal_consents',
    )
    consented_to_reveal = models.BooleanField(default=False)
    consented_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"RevealConsent(chat={self.chat_id}, user={self.user_id}, consented={self.consented_to_reveal})"


class ChatMessage(models.Model):
    """
    Represents a single E2EE or legacy message within a chat room.
    """

    chat = models.ForeignKey(Chat, related_name='messages', on_delete=models.CASCADE)
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='sent_messages',
        on_delete=models.CASCADE,
    )
    content = models.TextField(null=True, blank=True)
    ciphertext = models.TextField(blank=True, default='')
    nonce = models.CharField(max_length=128, blank=True, default='')
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Message from {self.sender.ephemeral_handle} in Chat {self.chat_id}"

    class Meta:
        ordering = ['timestamp']


def is_identity_revealed(chat: Chat, viewer, subject_user) -> bool:
    """
    Returns True ONLY when viewer == subject_user OR BOTH participants have mutually
    consented to reveal their real identities in this room.
    """
    if viewer is None or subject_user is None:
        return False
    if getattr(viewer, 'pk', None) == getattr(subject_user, 'pk', None):
        return True

    if chat.match_id:
        viewer_consented = ChatParticipantConsent.objects.filter(
            chat=chat,
            user=viewer,
            consented_to_reveal=True,
        ).exists()
        subject_consented = ChatParticipantConsent.objects.filter(
            chat=chat,
            user=subject_user,
            consented_to_reveal=True,
        ).exists()
        return viewer_consented and subject_consented

    viewer_consented = ChatParticipantConsent.objects.filter(
        chat=chat,
        user=viewer,
        consented_to_reveal=True,
    ).filter(Q(target_user=subject_user) | Q(target_user__isnull=True)).exists()
    subject_consented = ChatParticipantConsent.objects.filter(
        chat=chat,
        user=subject_user,
        consented_to_reveal=True,
    ).filter(Q(target_user=viewer) | Q(target_user__isnull=True)).exists()
    return viewer_consented and subject_consented
