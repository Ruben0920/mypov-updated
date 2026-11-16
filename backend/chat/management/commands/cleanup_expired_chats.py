from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from chat.models import Chat, ChatMessage
from matches.models import MicroTable, MicroTableStatus


class Command(BaseCommand):
    help = 'Purges expired ephemeral chat rooms and their messages.'

    def handle(self, *args, **options):
        now = timezone.now()
        inactivity_cutoff = now - timedelta(hours=24)

        expired_chats = Chat.objects.filter(
            Q(expires_at__lt=now)
            | Q(expires_at__isnull=True, last_activity__lt=inactivity_cutoff)
        )

        count_chats = expired_chats.count()
        count_messages = ChatMessage.objects.filter(chat__in=expired_chats).count()
        micro_table_ids = list(
            expired_chats.filter(micro_table__isnull=False).values_list('micro_table_id', flat=True)
        )

        if count_chats > 0:
            ChatMessage.objects.filter(chat__in=expired_chats).delete()
            expired_chats.delete()
            if micro_table_ids:
                MicroTable.objects.filter(id__in=micro_table_ids).update(
                    chat_room_id=None,
                    status=MicroTableStatus.EXPIRED,
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'Purged {count_chats} expired chat rooms and {count_messages} messages.'
            )
        )
