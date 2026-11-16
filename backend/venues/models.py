from datetime import timedelta
from decimal import Decimal
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class City(models.TextChoices):
    LIMASSOL = 'LIMASSOL', 'Limassol'
    PAPHOS = 'PAPHOS', 'Paphos'
    NICOSIA = 'NICOSIA', 'Nicosia'


class ActivityBucket(models.TextChoices):
    COFFEE_COWORK = 'COFFEE_COWORK', 'Coffee, Study & Co-Work'
    DRINKS_NIGHTLIFE = 'DRINKS_NIGHTLIFE', 'Drinks, Craft Beer & Nightlife'
    SPORTS_PADEL = 'SPORTS_PADEL', 'Sports, Gym, Running & Padel'
    FOOD_DINING = 'FOOD_DINING', 'Food, Street Food & Casual Dining'
    BOARD_GAMES_GEEK = 'BOARD_GAMES_GEEK', 'Board Games, Gaming & Trivia'
    WALK_BEACH_SUNSET = 'WALK_BEACH_SUNSET', 'Walk, Beach, Sunset & Explore'


class VenueTier(models.TextChoices):
    FREE = 'FREE', 'Free'
    STARTER_29 = 'STARTER_29', 'Starter (€29/mo)'
    SMALL_59 = 'SMALL_59', 'Small (€59/mo)'
    BIG_129 = 'BIG_129', 'Big (€129/mo)'
    ENTERPRISE = 'ENTERPRISE', 'Enterprise'


class CrowdLevel(models.TextChoices):
    QUIET = 'QUIET', 'Quiet'
    MODERATE = 'MODERATE', 'Moderate'
    LIVELY = 'LIVELY', 'Lively'
    PACKED = 'PACKED', 'Packed'


class NoiseLevel(models.TextChoices):
    STUDY_QUIET = 'STUDY_QUIET', 'Study Quiet'
    CONVERSATIONAL = 'CONVERSATIONAL', 'Conversational'
    LOUD_MUSIC = 'LOUD_MUSIC', 'Loud Music'


class VibePresetBadge(models.TextChoices):
    LAPTOP_FRIENDLY = 'LAPTOP_FRIENDLY', 'Laptop Friendly'
    GREAT_MUSIC = 'GREAT_MUSIC', 'Great Music'
    FAST_SERVICE = 'FAST_SERVICE', 'Fast Service'
    SOCIAL_CROWD = 'SOCIAL_CROWD', 'Social Crowd'
    CHILL_TERRACE = 'CHILL_TERRACE', 'Chill Terrace'


class Venue(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True)
    city = models.CharField(max_length=24, choices=City.choices, db_index=True)
    district_corridor = models.CharField(max_length=64, db_index=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    address = models.CharField(max_length=255)
    supported_buckets = models.JSONField(default=list)
    interest_tags = models.JSONField(default=list)
    tier = models.CharField(max_length=24, choices=VenueTier.choices, default=VenueTier.FREE)
    is_weekly_map_anchor = models.BooleanField(default=False)
    default_discount_text = models.CharField(max_length=255, blank=True, default='')
    avg_spend_estimate_eur = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('15.00'),
    )
    view_count = models.PositiveIntegerField(default=0)
    routed_matches_count = models.PositiveIntegerField(default=0)
    upgrade_prompt_eligible = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.city})"


class VenueTableQR(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name='table_qrs')
    table_label = models.CharField(max_length=64)
    qr_secret_token = models.CharField(max_length=128, unique=True, db_index=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.venue.name} - {self.table_label}"


class VenueVibeReport(models.Model):
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name='vibe_reports')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='vibe_reports',
    )
    crowd_level = models.CharField(max_length=24, choices=CrowdLevel.choices)
    noise_level = models.CharField(max_length=24, choices=NoiseLevel.choices)
    current_best_for = models.CharField(max_length=32, choices=ActivityBucket.choices)
    vibe_preset_badge = models.CharField(max_length=32, choices=VibePresetBadge.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if self.expires_at is None:
            base_time = self.created_at or timezone.now()
            self.expires_at = base_time + timedelta(hours=2)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Vibe({self.venue.name}: {self.crowd_level}/{self.vibe_preset_badge})"


class VenueFlashPerk(models.Model):
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name='flash_perks')
    title = models.CharField(max_length=180)
    target_bucket = models.CharField(max_length=32, choices=ActivityBucket.choices)
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField()
    max_redemptions = models.PositiveIntegerField(default=20)
    redeemed_count = models.PositiveIntegerField(default=0)
    requires_companion_or_table = models.BooleanField(default=False)

    class Meta:
        ordering = ['valid_until']

    @property
    def is_available(self) -> bool:
        now = timezone.now()
        return (
            self.valid_from <= now <= self.valid_until
            and self.redeemed_count < self.max_redemptions
        )

    def __str__(self):
        return f"{self.title} ({self.venue.name})"


class VenueWalkInRedemption(models.Model):
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name='walk_in_redemptions')
    table_qr = models.ForeignKey(VenueTableQR, on_delete=models.CASCADE, related_name='redemptions')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='venue_walk_ins',
    )
    flash_perk = models.ForeignKey(
        VenueFlashPerk,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='redemptions',
    )
    match_id = models.UUIDField(null=True, blank=True)
    legacy_match_id = models.PositiveBigIntegerField(null=True, blank=True)
    party_size = models.PositiveSmallIntegerField(default=1)
    estimated_revenue_eur = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"WalkIn({self.venue.name}, party={self.party_size}, €{self.estimated_revenue_eur})"
