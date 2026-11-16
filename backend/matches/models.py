import uuid

from django.conf import settings
from django.db import models

from posts.constants import TimeWindow
from venues.models import ActivityBucket, Venue


class TableSafetyMode(models.TextChoices):
    OPEN = 'OPEN', 'Open'
    VERIFIED_HUMANS_ONLY = 'VERIFIED_HUMANS_ONLY', 'Verified Humans Only'
    VERIFIED_WOMEN_ONLY = 'VERIFIED_WOMEN_ONLY', 'Verified Women Only'
    COMMUNITY_ONLY = 'COMMUNITY_ONLY', 'Verified Community Only'


class MicroTableStatus(models.TextChoices):
    FORMING = 'FORMING', 'Forming'
    LOCKED_READY = 'LOCKED_READY', 'Locked Ready'
    CHECKED_IN = 'CHECKED_IN', 'Checked In'
    COMPLETED = 'COMPLETED', 'Completed'
    EXPIRED = 'EXPIRED', 'Expired'


class Match(models.Model):
    MATCH_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('pending_ai', 'Pending AI'),
        ('matched', 'Matched'),
        ('declined', 'Declined'),
        ('blocked', 'Blocked'),
    ]

    MATCH_TYPE_CHOICES = [
        ('MANUAL', 'Manual'),
        ('AI', 'AI Generated'),
    ]

    user1 = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='matches_as_user1',
        on_delete=models.CASCADE,
    )
    user2 = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='matches_as_user2',
        on_delete=models.CASCADE,
    )
    status = models.CharField(max_length=20, choices=MATCH_STATUS_CHOICES, default='pending')
    score = models.FloatField(null=True, blank=True, help_text="Compatibility score, if applicable")
    match_type = models.CharField(max_length=10, choices=MATCH_TYPE_CHOICES, default='MANUAL')
    activity_bucket = models.CharField(
        max_length=32,
        choices=ActivityBucket.choices,
        blank=True,
        default='',
    )
    time_window = models.CharField(
        max_length=24,
        choices=TimeWindow.choices,
        blank=True,
        default='',
    )
    suggested_venue = models.ForeignKey(
        Venue,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='suggested_matches',
    )
    user1_checked_in_at = models.DateTimeField(null=True, blank=True)
    user2_checked_in_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user1', 'user2')
        ordering = ['-updated_at']

    def __str__(self):
        return f"Match between {self.user1.username} and {self.user2.username} - {self.status} ({self.match_type})"


class MicroTable(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    venue = models.ForeignKey(
        Venue,
        on_delete=models.CASCADE,
        related_name='micro_tables',
    )
    activity_bucket = models.CharField(
        max_length=32,
        choices=ActivityBucket.choices,
        db_index=True,
    )
    time_window = models.CharField(
        max_length=24,
        choices=TimeWindow.choices,
        db_index=True,
    )
    scheduled_for = models.DateTimeField()
    min_seats = models.PositiveSmallIntegerField(default=3)
    max_seats = models.PositiveSmallIntegerField(default=6)
    safety_mode = models.CharField(
        max_length=32,
        choices=TableSafetyMode.choices,
        default=TableSafetyMode.VERIFIED_HUMANS_ONLY,
    )
    status = models.CharField(
        max_length=24,
        choices=MicroTableStatus.choices,
        default=MicroTableStatus.FORMING,
        db_index=True,
    )
    perk_description = models.CharField(max_length=255, blank=True, default='')
    chat_room_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['scheduled_for', '-created_at']

    def __str__(self):
        return f"MicroTable({self.venue.name}, {self.activity_bucket}, {self.status})"


class MicroTableSeat(models.Model):
    table = models.ForeignKey(
        MicroTable,
        on_delete=models.CASCADE,
        related_name='seats',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='micro_table_seats',
    )
    post = models.ForeignKey(
        'posts.Post',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='micro_table_seats',
    )
    ephemeral_alias = models.CharField(max_length=64, blank=True)
    checked_in_via_qr = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('table', 'user')
        ordering = ['joined_at']

    def __str__(self):
        return f"Seat({self.ephemeral_alias} @ {self.table_id})"
