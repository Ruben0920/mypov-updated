from collections import defaultdict
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from posts.constants import TimeWindow, VibePreference, compute_expires_at
from posts.models import Post
from users.models import (
    CustomUser,
    MatchSafetyFilter,
    SafetyReport,
    VerifiedGenderGroup,
)
from venues.services import rank_venues

from .models import (
    Match,
    MicroTable,
    MicroTableSeat,
    MicroTableStatus,
    TableSafetyMode,
)

INTEREST_WEIGHT = 0.7
OBJECT_WEIGHT = 0.3
MATCH_THRESHOLD = 0.3
RECENT_POST_DAYS = 7

EPHEMERAL_ALIAS_POOL = [
    'Anon Fox',
    'Anon Falcon',
    'Anon Otter',
    'Anon Lynx',
    'Anon Owl',
    'Anon Hawk',
    'Anon Dolphin',
    'Anon Ibex',
]


class TableFullConflict(Exception):
    """Raised when a user tries to join a MicroTable that has reached max_seats."""


def _calculate_jaccard_similarity(set1_items, set2_items) -> float:
    set1 = set(set1_items or [])
    set2 = set(set2_items or [])
    if not set1 and not set2:
        return 0.0
    intersection = len(set1.intersection(set2))
    union = len(set1.union(set2))
    return intersection / union if union > 0 else 0.0


def _post_tags(post: Post) -> set[str]:
    tags = post.confirmed_tags or post.inferred_interests or []
    return {str(t).strip().lower() for t in tags if t}


def _time_windows_overlap(tw1: str, tw2: str) -> bool:
    if tw1 == tw2:
        return True
    near_term = {TimeWindow.RIGHT_NOW, TimeWindow.TONIGHT}
    return tw1 in near_term and tw2 in near_term


def _users_have_block_or_report(user_a: CustomUser, user_b: CustomUser) -> bool:
    if Match.objects.filter(
        Q(user1=user_a, user2=user_b) | Q(user1=user_b, user2=user_a),
        status='blocked',
    ).exists():
        return True

    fp_a = user_a.device_fingerprint_hmac or ''
    fp_b = user_b.device_fingerprint_hmac or ''

    report_q = Q(reporter=user_a, reported_user=user_b) | Q(reporter=user_b, reported_user=user_a)
    if fp_b:
        report_q |= Q(reporter=user_a, reported_fingerprint_hmac=fp_b)
    if fp_a:
        report_q |= Q(reporter=user_b, reported_fingerprint_hmac=fp_a)

    return SafetyReport.objects.filter(report_q).exists()


def _satisfies_safety_filter(requester: CustomUser, candidate: CustomUser) -> bool:
    safety_filter = requester.match_safety_filter
    if safety_filter == MatchSafetyFilter.ANY:
        return True
    if safety_filter == MatchSafetyFilter.VERIFIED_ONLY:
        return bool(candidate.is_verified_human)
    if safety_filter == MatchSafetyFilter.SAME_GENDER_ONLY:
        return (
            bool(candidate.is_verified_human)
            and requester.verified_gender_group != VerifiedGenderGroup.UNSPECIFIED
            and requester.verified_gender_group == candidate.verified_gender_group
        )
    if safety_filter == MatchSafetyFilter.COMMUNITY_ONLY:
        return (
            bool(requester.verified_community_domain)
            and requester.verified_community_domain == candidate.verified_community_domain
        )
    return False


def are_users_safety_compatible(user_a: CustomUser, user_b: CustomUser) -> bool:
    if user_a.pk == user_b.pk:
        return False
    if _users_have_block_or_report(user_a, user_b):
        return False
    return _satisfies_safety_filter(user_a, user_b) and _satisfies_safety_filter(user_b, user_a)


def _calculate_post_pair_score(post1: Post, post2: Post) -> float:
    tags1 = _post_tags(post1)
    tags2 = _post_tags(post2)
    tag_similarity = _calculate_jaccard_similarity(tags1, tags2)

    objects1 = {
        obj.get('label')
        for obj in (post1.detected_objects or [])
        if isinstance(obj, dict) and obj.get('label')
    }
    objects2 = {
        obj.get('label')
        for obj in (post2.detected_objects or [])
        if isinstance(obj, dict) and obj.get('label')
    }
    if not objects1 and not objects2:
        return tag_similarity
    object_similarity = _calculate_jaccard_similarity(objects1, objects2)
    return (tag_similarity * INTEREST_WEIGHT) + (object_similarity * OBJECT_WEIGHT)


def _best_compatible_post_pair(user1_posts: list[Post], user2_posts: list[Post]):
    best_score = 0.0
    best_pair = None
    for p1 in user1_posts:
        if not _post_tags(p1) and not p1.detected_objects:
            continue
        for p2 in user2_posts:
            if not _post_tags(p2) and not p2.detected_objects:
                continue
            if p1.city != p2.city or p1.activity_bucket != p2.activity_bucket:
                continue
            if not _time_windows_overlap(p1.time_window, p2.time_window):
                continue
            score = _calculate_post_pair_score(p1, p2)
            if score > best_score:
                best_score = score
                best_pair = (p1, p2)
    return best_score, best_pair


def _calculate_user_pair_score(user1_posts, user2_posts):
    score, _ = _best_compatible_post_pair(list(user1_posts), list(user2_posts))
    return score


def _pick_suggested_venue(city: str, activity_bucket: str, tags: list[str]):
    ranked = rank_venues(city=city, bucket=activity_bucket, tags=tags, limit=1)
    if not ranked:
        return None
    return ranked[0]['venue']


def _active_posts_for_user(user: CustomUser, now=None) -> list[Post]:
    now = now or timezone.now()
    return list(
        Post.objects.filter(user=user)
        .filter(Q(expires_at__gt=now) | Q(expires_at__isnull=True, created_at__gte=now - timedelta(days=RECENT_POST_DAYS)))
        .order_by('-created_at')
    )


def generate_ai_matches_for_user(user: CustomUser) -> list[dict]:
    """
    Generates safety-filtered 1-on-1 intention matches for a given user,
    attaching a top-ranked public suggested_venue to each Match.
    """
    now = timezone.now()
    user_posts = _active_posts_for_user(user, now=now)
    if not user_posts:
        return []

    active_users = (
        CustomUser.objects.filter(is_active=True)
        .filter(Q(expires_at__gte=now) | Q(expires_at__isnull=True))
        .exclude(pk=user.pk)
    )

    created_matches_info = []
    for other_user in active_users:
        if not are_users_safety_compatible(user, other_user):
            continue

        existing_match = Match.objects.filter(
            Q(user1=user, user2=other_user) | Q(user1=other_user, user2=user),
            status__in=['pending', 'pending_ai', 'matched', 'blocked'],
        ).exists()
        if existing_match:
            continue

        other_posts = _active_posts_for_user(other_user, now=now)
        if not other_posts:
            continue

        match_score, best_pair = _best_compatible_post_pair(user_posts, other_posts)
        if match_score < MATCH_THRESHOLD or best_pair is None:
            continue

        p1, p2 = best_pair
        shared_tags = sorted(_post_tags(p1).union(_post_tags(p2)))
        suggested_venue = _pick_suggested_venue(p1.city, p1.activity_bucket, shared_tags)

        u1, u2 = (user, other_user) if user.pk < other_user.pk else (other_user, user)
        match_obj, created = Match.objects.update_or_create(
            user1=u1,
            user2=u2,
            defaults={
                'score': match_score,
                'status': 'pending_ai',
                'match_type': 'AI',
                'activity_bucket': p1.activity_bucket,
                'time_window': p1.time_window,
                'suggested_venue': suggested_venue,
            },
        )
        if suggested_venue is not None and created:
            suggested_venue.routed_matches_count += 1
            suggested_venue.save(update_fields=['routed_matches_count'])

        created_matches_info.append(
            {
                'match_id': match_obj.id,
                'user1': match_obj.user1.username,
                'user2': match_obj.user2.username,
                'score': match_score,
                'suggested_venue_id': str(suggested_venue.id) if suggested_venue else None,
                'status': 'created' if created else 'updated',
            }
        )

    return created_matches_info


def generate_all_ai_matches() -> list[dict]:
    now = timezone.now()
    active_users = list(
        CustomUser.objects.filter(is_active=True)
        .filter(Q(expires_at__gte=now) | Q(expires_at__isnull=True))
        .distinct()
    )
    all_matches = []
    seen_pairs = set()
    for u in active_users:
        for info in generate_ai_matches_for_user(u):
            pair = tuple(sorted((info['user1'], info['user2'])))
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                all_matches.append(info)
    return all_matches


def is_user_eligible_for_table(user: CustomUser, table: MicroTable) -> bool:
    if table.safety_mode == TableSafetyMode.VERIFIED_HUMANS_ONLY:
        if not user.is_verified_human:
            return False
    elif table.safety_mode == TableSafetyMode.VERIFIED_WOMEN_ONLY:
        if not (user.is_verified_human and user.verified_gender_group == VerifiedGenderGroup.WOMAN):
            return False
    elif table.safety_mode == TableSafetyMode.COMMUNITY_ONLY:
        if not user.verified_community_domain:
            return False
        first_seat = table.seats.select_related('user').first()
        if first_seat and first_seat.user.verified_community_domain != user.verified_community_domain:
            return False

    for seat in table.seats.select_related('user'):
        if seat.user_id == user.pk:
            continue
        if _users_have_block_or_report(user, seat.user):
            return False

    return True


def _next_ephemeral_alias(table: MicroTable) -> str:
    used = set(table.seats.values_list('ephemeral_alias', flat=True))
    for alias in EPHEMERAL_ALIAS_POOL:
        if alias not in used:
            return alias
    return f"Anon Guest {table.seats.count() + 1}"


def _ensure_micro_table_chat_room(table: MicroTable):
    from chat.models import Chat, ChatParticipantConsent

    chat_room, _ = Chat.objects.get_or_create(
        micro_table=table,
        defaults={
            'last_activity': timezone.now(),
            'expires_at': table.scheduled_for + timedelta(hours=6),
        },
    )
    if table.chat_room_id != chat_room.room_uuid:
        table.chat_room_id = chat_room.room_uuid
        table.save(update_fields=['chat_room_id'])

    for seat in table.seats.all():
        ChatParticipantConsent.objects.get_or_create(
            chat=chat_room,
            user_id=seat.user_id,
            target_user=None,
        )
    return chat_room


@transaction.atomic
def claim_micro_table_seat(
    *,
    table_id,
    user: CustomUser,
    post: Post | None = None,
) -> tuple[MicroTable, MicroTableSeat, bool]:
    """
    Atomically claims a seat on a MicroTable using select_for_update().
    Transitions FORMING -> LOCKED_READY and provisions a group ChatRoom once min_seats is reached.
    Raises TableFullConflict if table already holds max_seats.
    """
    table = MicroTable.objects.select_for_update().select_related('venue').get(pk=table_id)

    if table.status in (MicroTableStatus.COMPLETED, MicroTableStatus.EXPIRED):
        raise ValidationError("This Micro-Table is no longer active.")

    existing_seat = MicroTableSeat.objects.filter(table=table, user=user).first()
    if existing_seat is not None:
        return table, existing_seat, False

    if not is_user_eligible_for_table(user, table):
        raise PermissionDenied("You do not meet the safety eligibility requirements for this table.")

    if table.seats.count() >= table.max_seats:
        raise TableFullConflict("This Micro-Table is full.")

    alias = _next_ephemeral_alias(table)
    seat = MicroTableSeat.objects.create(
        table=table,
        user=user,
        post=post,
        ephemeral_alias=alias,
    )

    if table.seats.count() >= table.min_seats and table.status == MicroTableStatus.FORMING:
        table.status = MicroTableStatus.LOCKED_READY
        table.save(update_fields=['status'])
        _ensure_micro_table_chat_room(table)
    elif table.chat_room_id is not None:
        _ensure_micro_table_chat_room(table)

    return table, seat, True


@transaction.atomic
def run_auto_table_matchmaker(
    *,
    city: str | None = None,
    activity_bucket: str | None = None,
    time_window: str | None = None,
) -> list[MicroTable]:
    """
    Spawns MicroTables automatically at the highest-ranked partner Venue when 3+ eligible
    users share a (city, activity_bucket, time_window) with SMALL_GROUP_3_6 or EITHER preference
    and no existing active table exists.
    """
    now = timezone.now()
    qs = Post.objects.select_related('user').filter(
        vibe_preference__in=[VibePreference.SMALL_GROUP_3_6, VibePreference.EITHER],
        user__is_active=True,
    ).filter(Q(expires_at__gt=now) | Q(expires_at__isnull=True))

    if city:
        qs = qs.filter(city=city)
    if activity_bucket:
        qs = qs.filter(activity_bucket=activity_bucket)
    if time_window:
        qs = qs.filter(time_window=time_window)

    groups: dict[tuple[str, str, str], list[Post]] = defaultdict(list)
    for post in qs.order_by('created_at'):
        key = (post.city, post.activity_bucket, post.time_window)
        # Keep at most one post per user per group
        if not any(existing.user_id == post.user_id for existing in groups[key]):
            groups[key].append(post)

    spawned: list[MicroTable] = []
    for (g_city, g_bucket, g_window), group_posts in groups.items():
        if len(group_posts) < 3:
            continue

        existing_table = MicroTable.objects.filter(
            venue__city=g_city,
            activity_bucket=g_bucket,
            time_window=g_window,
            status__in=[MicroTableStatus.FORMING, MicroTableStatus.LOCKED_READY],
        ).exists()
        if existing_table:
            continue

        all_tags = sorted({tag for p in group_posts for tag in _post_tags(p)})
        venue = _pick_suggested_venue(g_city, g_bucket, all_tags)
        if venue is None:
            continue

        # Select compatible verified users if >=3 verified, else OPEN safety mode
        verified_posts = [p for p in group_posts if p.user.is_verified_human]
        if len(verified_posts) >= 3:
            safety_mode = TableSafetyMode.VERIFIED_HUMANS_ONLY
            selected_posts = verified_posts[:6]
        else:
            safety_mode = TableSafetyMode.OPEN
            selected_posts = group_posts[:6]

        scheduled_for = compute_expires_at(g_window, now=now)
        table = MicroTable.objects.create(
            venue=venue,
            activity_bucket=g_bucket,
            time_window=g_window,
            scheduled_for=scheduled_for,
            min_seats=3,
            max_seats=6,
            safety_mode=safety_mode,
            status=MicroTableStatus.FORMING,
            perk_description=venue.default_discount_text or 'Table QR group perk unlocked on arrival',
        )
        for p in selected_posts:
            try:
                claim_micro_table_seat(table_id=table.id, user=p.user, post=p)
            except (PermissionDenied, TableFullConflict, ValidationError):
                continue

        table.refresh_from_db()
        spawned.append(table)

    return spawned
