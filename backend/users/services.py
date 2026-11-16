import hashlib
import hmac

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import CustomUser, HardwareReputationLedger, SafetyReport

BLACKLIST_STRIKE_THRESHOLD = 3


def compute_device_fingerprint_hmac(raw_fingerprint: str) -> str:
    """
    Computes an irreversible HMAC-SHA256 hash of a raw device or phone fingerprint.
    Raw fingerprints are never persisted anywhere.
    """
    normalized = (raw_fingerprint or '').strip()
    if not normalized:
        return ''
    pepper = getattr(settings, 'DEVICE_HASH_PEPPER', '')
    pepper_bytes = pepper.encode('utf-8') if isinstance(pepper, str) else pepper
    return hmac.new(pepper_bytes, normalized.encode('utf-8'), hashlib.sha256).hexdigest()


def extract_community_domain(value: str) -> str:
    """
    Extracts only the domain/tag from a one-time community email check without
    retaining the raw email address.
    """
    cleaned = (value or '').strip().lower()
    if '@' in cleaned:
        _, _, domain = cleaned.partition('@')
        return domain.strip()
    return cleaned


def is_device_blocked(fingerprint_hmac: str) -> tuple[bool, HardwareReputationLedger | None]:
    """
    Returns (True, ledger) if the hardware fingerprint is blacklisted or under active cooldown.
    """
    if not fingerprint_hmac:
        return False, None
    ledger = HardwareReputationLedger.objects.filter(fingerprint_hmac=fingerprint_hmac).first()
    if not ledger:
        return False, None
    if ledger.is_blacklisted:
        return True, ledger
    if ledger.cooldown_until and ledger.cooldown_until > timezone.now():
        return True, ledger
    return False, ledger


@transaction.atomic
def submit_safety_report(
    *,
    reporter: CustomUser,
    reported_user: CustomUser,
    reason: str,
) -> tuple[SafetyReport, HardwareReputationLedger | None]:
    """
    Records a safety report, enforces distinct-reporter strike accumulation on the
    hardware reputation ledger (blacklisting at >= 3 strikes), and blocks/unmatches the pair.
    """
    if reporter.pk == reported_user.pk:
        raise ValidationError("You cannot report yourself.")

    target_fp_hmac = reported_user.device_fingerprint_hmac or ''

    duplicate_filter = Q(reported_user=reported_user)
    if target_fp_hmac:
        duplicate_filter |= Q(reported_fingerprint_hmac=target_fp_hmac)

    if SafetyReport.objects.filter(reporter=reporter).filter(duplicate_filter).exists():
        raise ValidationError("You have already submitted a safety report for this user.")

    report = SafetyReport.objects.create(
        reporter=reporter,
        reported_user=reported_user,
        reported_fingerprint_hmac=target_fp_hmac,
        reason=reason,
    )

    ledger = None
    if target_fp_hmac:
        ledger, _ = HardwareReputationLedger.objects.select_for_update().get_or_create(
            fingerprint_hmac=target_fp_hmac,
        )
        ledger.strike_count += 1
        if ledger.strike_count >= BLACKLIST_STRIKE_THRESHOLD:
            ledger.is_blacklisted = True
        ledger.save(update_fields=['strike_count', 'is_blacklisted', 'updated_at'])

    from matches.models import Match

    Match.objects.filter(
        Q(user1=reporter, user2=reported_user) | Q(user1=reported_user, user2=reporter)
    ).update(status='blocked')

    return report, ledger


@transaction.atomic
def execute_one_tap_wipe(*, user: CustomUser) -> HardwareReputationLedger | None:
    """
    Permanently deletes all user posts/intentions, matches, chats/messages, and the user
    account itself while preserving only the irreversible HardwareReputationLedger entry.
    """
    from chat.models import Chat, ChatMessage, ChatParticipantConsent
    from matches.models import Match, MicroTableSeat
    from posts.models import Post

    ledger = None
    if user.device_fingerprint_hmac:
        ledger, _ = HardwareReputationLedger.objects.select_for_update().get_or_create(
            fingerprint_hmac=user.device_fingerprint_hmac,
        )
        ledger.wipe_count_30d += 1
        ledger.save(update_fields=['wipe_count_30d', 'updated_at'])

    user_matches = Match.objects.filter(Q(user1=user) | Q(user2=user))
    user_chats = Chat.objects.filter(match__in=user_matches)

    ChatParticipantConsent.objects.filter(Q(user=user) | Q(target_user=user)).delete()
    MicroTableSeat.objects.filter(user=user).delete()
    ChatMessage.objects.filter(Q(sender=user) | Q(chat__in=user_chats)).delete()
    user_chats.delete()
    user_matches.delete()

    for post in Post.objects.filter(user=user):
        if post.image:
            post.image.delete(save=False)
        post.delete()

    user.delete()
    return ledger
