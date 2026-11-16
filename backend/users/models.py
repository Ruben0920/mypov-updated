from datetime import timedelta
import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class VerifiedGenderGroup(models.TextChoices):
    UNSPECIFIED = 'UNSPECIFIED', 'Unspecified'
    WOMAN = 'WOMAN', 'Woman'
    MAN = 'MAN', 'Man'
    NON_BINARY = 'NON_BINARY', 'Non-Binary'


class MatchSafetyFilter(models.TextChoices):
    ANY = 'ANY', 'Any'
    VERIFIED_ONLY = 'VERIFIED_ONLY', 'Verified Humans Only'
    SAME_GENDER_ONLY = 'SAME_GENDER_ONLY', 'Same Verified Gender Only'
    COMMUNITY_ONLY = 'COMMUNITY_ONLY', 'Verified Community Only'


class ReportReason(models.TextChoices):
    HARASSMENT = 'HARASSMENT', 'Harassment'
    NO_SHOW = 'NO_SHOW', 'No Show'
    UNSAFE_BEHAVIOR = 'UNSAFE_BEHAVIOR', 'Unsafe Behavior'
    SPAM = 'SPAM', 'Spam'


# Architectural aliases for downstream apps
MATCH_FILTER_ANY = MatchSafetyFilter.ANY
MATCH_FILTER_VERIFIED_HUMANS_ONLY = MatchSafetyFilter.VERIFIED_ONLY
MATCH_FILTER_SAME_VERIFIED_GENDER = MatchSafetyFilter.SAME_GENDER_ONLY
MATCH_FILTER_VERIFIED_COMMUNITY_ONLY = MatchSafetyFilter.COMMUNITY_ONLY


def generate_ephemeral_handle() -> str:
    return f"anon_{uuid.uuid4().hex[:10]}"


class CustomUser(AbstractUser):
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)

    groups = models.ManyToManyField(
        'auth.Group',
        related_name='customuser_groups',
        blank=True,
        help_text='The groups this user belongs to.',
        verbose_name='groups',
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        related_name='customuser_permissions',
        blank=True,
        help_text='Specific permissions for this user.',
        verbose_name='user permissions',
    )
    otp_code = models.CharField(max_length=6, null=True, blank=True)
    otp_expiration = models.DateTimeField(null=True, blank=True)
    otp_attempts = models.IntegerField(default=3)

    # Zero-knowledge identity & safety matching claims (zero biometrics/public profile)
    ephemeral_handle = models.CharField(max_length=64, unique=True, blank=True)
    is_verified_human = models.BooleanField(default=False)
    is_verified_adult_18_plus = models.BooleanField(default=False)
    verified_gender_group = models.CharField(
        max_length=16,
        choices=VerifiedGenderGroup.choices,
        default=VerifiedGenderGroup.UNSPECIFIED,
    )
    verified_community_domain = models.CharField(max_length=120, blank=True, default='')
    match_safety_filter = models.CharField(
        max_length=24,
        choices=MatchSafetyFilter.choices,
        default=MatchSafetyFilter.VERIFIED_ONLY,
    )
    device_fingerprint_hmac = models.CharField(
        max_length=64,
        blank=True,
        default='',
        db_index=True,
    )
    e2ee_public_key = models.TextField(blank=True, default='')
    verification_proof_token = models.CharField(max_length=128, blank=True, default='')

    def save(self, *args, **kwargs):
        if not self.pk and self.expires_at is None:
            self.expires_at = timezone.now() + timedelta(hours=24)
        if not self.ephemeral_handle:
            self.ephemeral_handle = generate_ephemeral_handle()
        super().save(*args, **kwargs)


class HardwareReputationLedger(models.Model):
    """
    Irreversibly hashed hardware/phone reputation ledger that survives One-Tap Account Wipe
    to prevent ban evasion without retaining PII.
    """

    fingerprint_hmac = models.CharField(max_length=64, unique=True, db_index=True)
    strike_count = models.PositiveIntegerField(default=0)
    is_blacklisted = models.BooleanField(default=False)
    cooldown_until = models.DateTimeField(null=True, blank=True)
    wipe_count_30d = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Ledger({self.fingerprint_hmac[:12]}..., strikes={self.strike_count}, blacklisted={self.is_blacklisted})"


class SafetyReport(models.Model):
    """
    Safety incident audit log. Reported user FK is SET_NULL so audit trail and
    reported_fingerprint_hmac survive account wipes.
    """

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='safety_reports_filed',
    )
    reported_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='safety_reports_received',
    )
    reported_fingerprint_hmac = models.CharField(
        max_length=64,
        blank=True,
        default='',
        db_index=True,
    )
    reason = models.CharField(max_length=32, choices=ReportReason.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"SafetyReport({self.reason}, fp={self.reported_fingerprint_hmac[:10]})"
