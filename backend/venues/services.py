from collections import Counter
from datetime import timedelta
from decimal import Decimal
import math
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    Venue,
    VenueFlashPerk,
    VenueTableQR,
    VenueTier,
    VenueVibeReport,
    VenueWalkInRedemption,
)

TIER_BOOST_WEIGHTS = {
    VenueTier.FREE: 0.0,
    VenueTier.STARTER_29: 0.5,
    VenueTier.SMALL_59: 1.0,
    VenueTier.BIG_129: 1.5,
    VenueTier.ENTERPRISE: 2.0,
}

VIBE_RATE_LIMIT_MINUTES = 60
FREE_TIER_MONTHLY_UPGRADE_THRESHOLD = 5


class RateLimitExceeded(Exception):
    """Raised when a user submits vibe telemetry faster than the cooldown window."""


class PerkRedemptionConflict(Exception):
    """Raised when a requested flash perk has reached its redemption cap or expired."""


def _dominant_choice(reports, field_name: str):
    values = [getattr(report, field_name) for report in reports if getattr(report, field_name)]
    if not values:
        return None
    counts = Counter(values)
    # Reports are ordered newest-first; max by count then earliest appearance (newest)
    return max(counts.keys(), key=lambda key: (counts[key], -values.index(key)))


def aggregate_live_vibe(venue: Venue, now=None) -> dict:
    """
    Aggregates non-expired (2-hour window) VenueVibeReport telemetry for a venue.
    """
    now = now or timezone.now()
    active_reports = list(
        venue.vibe_reports.filter(expires_at__gt=now).order_by('-created_at')
    )
    if not active_reports:
        return {
            'active_report_count': 0,
            'dominant_crowd_level': None,
            'dominant_noise_level': None,
            'dominant_vibe_preset_badge': None,
            'dominant_best_for': None,
        }

    return {
        'active_report_count': len(active_reports),
        'dominant_crowd_level': _dominant_choice(active_reports, 'crowd_level'),
        'dominant_noise_level': _dominant_choice(active_reports, 'noise_level'),
        'dominant_vibe_preset_badge': _dominant_choice(active_reports, 'vibe_preset_badge'),
        'dominant_best_for': _dominant_choice(active_reports, 'current_best_for'),
    }


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def rank_venues(
    *,
    city: str | None = None,
    bucket: str | None = None,
    tags: list[str] | None = None,
    lat: float | None = None,
    lng: float | None = None,
    limit: int | None = None,
) -> list[dict]:
    """
    Deterministically ranks active venues by bucket & tag fit, live 2-hour vibe telemetry,
    active flash perks, and strictly capped paid tier boost.
    Paid boost never allows an irrelevant venue into an unrelated activity bucket.
    """
    now = timezone.now()
    qs = Venue.objects.filter(is_active=True)
    if city:
        qs = qs.filter(city=city)

    normalized_tags = {t.strip().lower() for t in (tags or []) if t and t.strip()}

    ranked = []
    for venue in qs.prefetch_related('vibe_reports', 'flash_perks'):
        supported = set(venue.supported_buckets or [])
        if bucket and bucket not in supported:
            continue

        venue_tags = {str(t).strip().lower() for t in (venue.interest_tags or []) if t}
        tag_overlap = len(normalized_tags.intersection(venue_tags))

        live_vibe = aggregate_live_vibe(venue, now=now)
        active_perks = [
            perk
            for perk in venue.flash_perks.all()
            if perk.valid_from <= now <= perk.valid_until
            and perk.redeemed_count < perk.max_redemptions
            and (not bucket or perk.target_bucket == bucket)
        ]

        bucket_score = 10.0 if bucket and bucket in supported else 0.0
        tag_score = tag_overlap * 3.0
        vibe_score = 2.0 if live_vibe['active_report_count'] > 0 else 0.0
        perk_score = 1.5 if active_perks else 0.0
        anchor_score = 0.25 if venue.is_weekly_map_anchor else 0.0
        tier_boost = TIER_BOOST_WEIGHTS.get(venue.tier, 0.0)

        distance_km = None
        distance_penalty = 0.0
        if lat is not None and lng is not None:
            distance_km = round(
                _haversine_km(lat, lng, float(venue.latitude), float(venue.longitude)),
                3,
            )
            distance_penalty = min(distance_km * 0.05, 1.0)

        total_score = round(
            bucket_score + tag_score + vibe_score + perk_score + anchor_score + tier_boost - distance_penalty,
            4,
        )

        ranked.append(
            {
                'venue': venue,
                'score': total_score,
                'tag_overlap_count': tag_overlap,
                'live_vibe': live_vibe,
                'active_flash_perks': active_perks,
                'distance_km': distance_km,
            }
        )

    ranked.sort(key=lambda item: (-item['score'], item['venue'].name))
    if limit is not None:
        return ranked[:limit]
    return ranked


def submit_vibe_report(
    *,
    venue: Venue,
    user,
    crowd_level: str,
    noise_level: str,
    current_best_for: str,
    vibe_preset_badge: str,
    enforce_rate_limit: bool = True,
) -> VenueVibeReport:
    now = timezone.now()
    if enforce_rate_limit and user is not None:
        cutoff = now - timedelta(minutes=VIBE_RATE_LIMIT_MINUTES)
        recent_exists = VenueVibeReport.objects.filter(
            venue=venue,
            user=user,
            created_at__gte=cutoff,
        ).exists()
        if recent_exists:
            raise RateLimitExceeded(
                "Rate limit: 1 vibe report per user per venue every 60 minutes."
            )

    return VenueVibeReport.objects.create(
        venue=venue,
        user=user,
        crowd_level=crowd_level,
        noise_level=noise_level,
        current_best_for=current_best_for,
        vibe_preset_badge=vibe_preset_badge,
        expires_at=now + timedelta(hours=2),
    )


def _parse_match_identifiers(match_id=None, micro_table_id=None):
    parsed_uuid = None
    legacy_int = None

    raw_uuid = micro_table_id or match_id
    if raw_uuid is not None and raw_uuid != '':
        if isinstance(raw_uuid, uuid.UUID):
            parsed_uuid = raw_uuid
        else:
            raw_str = str(raw_uuid).strip()
            try:
                parsed_uuid = uuid.UUID(raw_str)
            except ValueError:
                try:
                    legacy_int = int(raw_str)
                except ValueError as exc:
                    raise ValidationError("Invalid match_id or micro_table_id format.") from exc

    return parsed_uuid, legacy_int


@transaction.atomic
def process_qr_checkin(
    *,
    user,
    qr_secret_token: str,
    match_id=None,
    micro_table_id=None,
    party_size: int = 1,
    flash_perk_id: int | None = None,
    vibe_data: dict | None = None,
) -> dict:
    if party_size < 1:
        raise ValidationError("party_size must be at least 1.")

    table_qr = (
        VenueTableQR.objects.select_for_update()
        .select_related('venue')
        .filter(qr_secret_token=qr_secret_token, is_active=True, venue__is_active=True)
        .first()
    )
    if table_qr is None:
        raise VenueTableQR.DoesNotExist("Active table QR token not found.")

    venue = Venue.objects.select_for_update().get(pk=table_qr.venue_id)
    now = timezone.now()
    parsed_uuid, legacy_int = _parse_match_identifiers(
        match_id=match_id,
        micro_table_id=micro_table_id,
    )
    has_companion_or_table = (
        party_size >= 2 or parsed_uuid is not None or legacy_int is not None
    )

    redeemed_perk = None
    if flash_perk_id is not None:
        perk = (
            VenueFlashPerk.objects.select_for_update()
            .filter(pk=flash_perk_id, venue=venue)
            .first()
        )
        if perk is None:
            raise ValidationError("Flash perk does not belong to this venue.")
        if not (perk.valid_from <= now <= perk.valid_until):
            raise PerkRedemptionConflict("Flash perk is outside its valid time window.")
        if perk.redeemed_count >= perk.max_redemptions:
            raise PerkRedemptionConflict("Flash perk redemption cap has been reached.")
        if perk.requires_companion_or_table and not has_companion_or_table:
            raise ValidationError("This flash perk requires a companion or group table.")
        perk.redeemed_count += 1
        perk.save(update_fields=['redeemed_count'])
        redeemed_perk = perk
    else:
        candidate_perks = VenueFlashPerk.objects.select_for_update().filter(
            venue=venue,
            valid_from__lte=now,
            valid_until__gte=now,
        ).order_by('valid_until', 'id')
        for perk in candidate_perks:
            if perk.redeemed_count >= perk.max_redemptions:
                continue
            if perk.requires_companion_or_table and not has_companion_or_table:
                continue
            perk.redeemed_count += 1
            perk.save(update_fields=['redeemed_count'])
            redeemed_perk = perk
            break

    estimated_revenue = venue.avg_spend_estimate_eur * Decimal(party_size)
    redemption = VenueWalkInRedemption.objects.create(
        venue=venue,
        table_qr=table_qr,
        user=user,
        flash_perk=redeemed_perk,
        match_id=parsed_uuid,
        legacy_match_id=legacy_int,
        party_size=party_size,
        estimated_revenue_eur=estimated_revenue,
    )

    match_checked_in = False
    micro_table_checked_in = False
    if parsed_uuid is not None and user is not None:
        from matches.models import MicroTable, MicroTableSeat, MicroTableStatus

        micro_table = MicroTable.objects.filter(pk=parsed_uuid).first()
        if micro_table is not None:
            seat_updated = MicroTableSeat.objects.filter(
                table=micro_table,
                user=user,
            ).update(checked_in_via_qr=True)
            if seat_updated:
                micro_table_checked_in = True
                if micro_table.status in (MicroTableStatus.FORMING, MicroTableStatus.LOCKED_READY):
                    micro_table.status = MicroTableStatus.CHECKED_IN
                    micro_table.save(update_fields=['status'])

    if legacy_int is not None and user is not None:
        from matches.models import Match

        match_obj = Match.objects.filter(pk=legacy_int).first()
        if match_obj is not None:
            if match_obj.user1_id == user.pk:
                match_obj.user1_checked_in_at = now
                match_obj.save(update_fields=['user1_checked_in_at', 'updated_at'])
                match_checked_in = True
            elif match_obj.user2_id == user.pk:
                match_obj.user2_checked_in_at = now
                match_obj.save(update_fields=['user2_checked_in_at', 'updated_at'])
                match_checked_in = True

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_walk_ins = VenueWalkInRedemption.objects.filter(
        venue=venue,
        created_at__gte=month_start,
    ).count()
    if venue.tier == VenueTier.FREE and monthly_walk_ins >= FREE_TIER_MONTHLY_UPGRADE_THRESHOLD:
        if not venue.upgrade_prompt_eligible:
            venue.upgrade_prompt_eligible = True
            venue.save(update_fields=['upgrade_prompt_eligible'])

    vibe_report = None
    if vibe_data:
        try:
            vibe_report = submit_vibe_report(
                venue=venue,
                user=user,
                crowd_level=vibe_data['crowd_level'],
                noise_level=vibe_data['noise_level'],
                current_best_for=vibe_data['current_best_for'],
                vibe_preset_badge=vibe_data['vibe_preset_badge'],
                enforce_rate_limit=True,
            )
        except RateLimitExceeded:
            vibe_report = None

    return {
        'redemption': redemption,
        'venue': venue,
        'table_qr': table_qr,
        'redeemed_perk': redeemed_perk,
        'vibe_report': vibe_report,
        'monthly_verified_walk_ins': monthly_walk_ins,
        'upgrade_prompt_eligible': venue.upgrade_prompt_eligible,
        'match_checked_in': match_checked_in,
        'micro_table_checked_in': micro_table_checked_in,
    }


def get_venue_roi_summary(venue: Venue) -> dict:
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    agg = VenueWalkInRedemption.objects.filter(venue=venue).aggregate(
        total_party=Sum('party_size'),
        total_revenue=Sum('estimated_revenue_eur'),
    )
    verified_walk_ins = VenueWalkInRedemption.objects.filter(venue=venue).count()
    monthly_walk_ins = VenueWalkInRedemption.objects.filter(
        venue=venue,
        created_at__gte=month_start,
    ).count()

    total_revenue = agg['total_revenue'] or Decimal('0.00')
    upgrade_eligible = (
        venue.tier == VenueTier.FREE
        and (monthly_walk_ins >= FREE_TIER_MONTHLY_UPGRADE_THRESHOLD or venue.upgrade_prompt_eligible)
    )

    return {
        'venue_id': str(venue.id),
        'venue_name': venue.name,
        'tier': venue.tier,
        'views': venue.view_count,
        'intention_matches_routed': venue.routed_matches_count,
        'verified_walk_ins': verified_walk_ins,
        'monthly_verified_walk_ins': monthly_walk_ins,
        'total_party_size_seated': agg['total_party'] or 0,
        'estimated_revenue_eur': float(total_revenue),
        'upgrade_prompt_eligible': upgrade_eligible,
    }
