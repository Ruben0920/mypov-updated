from django.conf import settings
from django.db import models

from venues.models import ActivityBucket, City, Venue

from .constants import TimeWindow, VibePreference, compute_expires_at


class Post(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='posts',
    )
    # Legacy field kept nullable for backward-compatible migrations; binary media is never persisted.
    image = models.ImageField(upload_to='posts/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    detected_objects = models.JSONField(
        null=True,
        blank=True,
        help_text="Objects detected in the image by AI",
    )
    inferred_interests = models.JSONField(
        null=True,
        blank=True,
        help_text="Interests inferred from objects by AI",
    )

    activity_bucket = models.CharField(
        max_length=32,
        choices=ActivityBucket.choices,
        default=ActivityBucket.COFFEE_COWORK,
        db_index=True,
    )
    time_window = models.CharField(
        max_length=24,
        choices=TimeWindow.choices,
        default=TimeWindow.RIGHT_NOW,
        db_index=True,
    )
    confirmed_tags = models.JSONField(default=list, blank=True)
    vibe_preference = models.CharField(
        max_length=24,
        choices=VibePreference.choices,
        default=VibePreference.EITHER,
    )
    city = models.CharField(
        max_length=24,
        choices=City.choices,
        default=City.LIMASSOL,
        db_index=True,
    )
    preferred_venue = models.ForeignKey(
        Venue,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='preferred_posts',
    )
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    def save(self, *args, **kwargs):
        # Enforce zero-retained-media invariant: confirmed intent posts never persist binary images.
        if self.image:
            self.image = None
        if self.expires_at is None:
            self.expires_at = compute_expires_at(self.time_window)
        if not self.inferred_interests and self.confirmed_tags:
            self.inferred_interests = list(self.confirmed_tags)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Intent({self.user.username}: {self.activity_bucket} [{self.time_window}])"
