from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from chat.models import Chat
from matches.models import (
    Match,
    MicroTable,
    MicroTableSeat,
    MicroTableStatus,
    TableSafetyMode,
)
from matches.services import generate_ai_matches_for_user, run_auto_table_matchmaker
from posts.constants import TimeWindow, VibePreference
from posts.models import Post
from users.models import CustomUser, MatchSafetyFilter, VerifiedGenderGroup
from venues.models import (
    ActivityBucket,
    City,
    Venue,
    VenueTableQR,
    VenueTier,
    VenueWalkInRedemption,
)


@pytest.mark.django_db
class TestMicroTablesAndSafetyMatching:
    def test_bucket_and_safety_filter_matching_with_suggested_venue(self):
        padel_venue = Venue.objects.create(
            name='Molos Padel Club',
            slug='molos-padel-test',
            city=City.LIMASSOL,
            district_corridor='LIMASSOL_MOLOS',
            latitude=Decimal('34.674200'),
            longitude=Decimal('33.050800'),
            address='Molos East',
            supported_buckets=[ActivityBucket.SPORTS_PADEL],
            interest_tags=['padel_doubles', 'outdoor_courts'],
            tier=VenueTier.BIG_129,
        )

        verified_a = CustomUser.objects.create_user(
            username='padel_alice',
            password='Password123!',
            ephemeral_handle='@limassol_padel_01',
            is_verified_human=True,
            is_verified_adult_18_plus=True,
            match_safety_filter=MatchSafetyFilter.VERIFIED_ONLY,
        )
        verified_b = CustomUser.objects.create_user(
            username='padel_bob',
            password='Password123!',
            ephemeral_handle='@limassol_padel_02',
            is_verified_human=True,
            is_verified_adult_18_plus=True,
            match_safety_filter=MatchSafetyFilter.VERIFIED_ONLY,
        )
        unverified_c = CustomUser.objects.create_user(
            username='padel_unverified',
            password='Password123!',
            ephemeral_handle='@unverified_03',
            is_verified_human=False,
            match_safety_filter=MatchSafetyFilter.ANY,
        )

        for usr in (verified_a, verified_b, unverified_c):
            Post.objects.create(
                user=usr,
                activity_bucket=ActivityBucket.SPORTS_PADEL,
                time_window=TimeWindow.TONIGHT,
                confirmed_tags=['padel_doubles', 'outdoor_courts'],
                vibe_preference=VibePreference.CHILL_1ON1,
                city=City.LIMASSOL,
            )

        created = generate_ai_matches_for_user(verified_a)
        assert len(created) == 1

        match = Match.objects.get(user1=verified_a, user2=verified_b)
        assert match.suggested_venue_id == padel_venue.id
        assert match.activity_bucket == ActivityBucket.SPORTS_PADEL
        assert match.time_window == TimeWindow.TONIGHT

        # Unverified user C is blocked from matching with VERIFIED_ONLY users
        assert not Match.objects.filter(user1=unverified_c).exists()
        assert not Match.objects.filter(user2=unverified_c).exists()

    def test_micro_table_formation_and_capacity_conflict(self):
        venue = Venue.objects.create(
            name='Saripolou Meeples',
            slug='saripolou-meeples-test',
            city=City.LIMASSOL,
            district_corridor='LIMASSOL_OLD_TOWN_SARIPOLOU',
            latitude=Decimal('34.675000'),
            longitude=Decimal('33.041000'),
            address='8 Agiou Andreou St',
            supported_buckets=[ActivityBucket.BOARD_GAMES_GEEK],
            interest_tags=['board_games', 'catan'],
            tier=VenueTier.STARTER_29,
        )

        table = MicroTable.objects.create(
            venue=venue,
            activity_bucket=ActivityBucket.BOARD_GAMES_GEEK,
            time_window=TimeWindow.TONIGHT,
            scheduled_for=timezone.now() + timedelta(hours=3),
            min_seats=3,
            max_seats=6,
            safety_mode=TableSafetyMode.VERIFIED_HUMANS_ONLY,
            status=MicroTableStatus.FORMING,
            perk_description='15% off table bill when 3+ members scan table QR',
        )

        verified_users = [
            CustomUser.objects.create_user(
                username=f'player_{i}',
                password='Password123!',
                is_verified_human=True,
                is_verified_adult_18_plus=True,
            )
            for i in range(7)
        ]

        client = APIClient()

        # First 2 users keep table in FORMING state
        for i in range(2):
            client.force_authenticate(user=verified_users[i])
            resp = client.post(f'/api/matches/micro-tables/{table.id}/claim-seat/', {}, format='json')
            assert resp.status_code == status.HTTP_201_CREATED
            assert resp.data['status'] == MicroTableStatus.FORMING
            assert resp.data['seat_count'] == i + 1
            assert resp.data['chat_room_id'] is None

        # 3rd user reaches min_seats -> transitions to LOCKED_READY & creates group ChatRoom
        client.force_authenticate(user=verified_users[2])
        resp3 = client.post(f'/api/matches/micro-tables/{table.id}/claim-seat/', {}, format='json')
        assert resp3.status_code == status.HTTP_201_CREATED
        assert resp3.data['status'] == MicroTableStatus.LOCKED_READY
        assert resp3.data['seat_count'] == 3
        assert resp3.data['chat_room_id'] is not None
        assert Chat.objects.filter(micro_table=table, room_uuid=resp3.data['chat_room_id']).exists()

        # Users 4, 5, 6 fill the remaining seats up to max_seats=6
        for i in range(3, 6):
            client.force_authenticate(user=verified_users[i])
            r = client.post(f'/api/matches/micro-tables/{table.id}/claim-seat/', {}, format='json')
            assert r.status_code == status.HTTP_201_CREATED

        # 7th user attempting to join a 6-seat table receives 409 Conflict
        client.force_authenticate(user=verified_users[6])
        resp7 = client.post(f'/api/matches/micro-tables/{table.id}/claim-seat/', {}, format='json')
        assert resp7.status_code == status.HTTP_409_CONFLICT
        assert table.seats.count() == 6

    def test_women_only_table_eligibility_and_auto_table_matchmaker(self):
        venue = Venue.objects.create(
            name='Nomad Espresso',
            slug='nomad-espresso-auto-test',
            city=City.LIMASSOL,
            district_corridor='LIMASSOL_OLD_TOWN_SARIPOLOU',
            latitude=Decimal('34.675200'),
            longitude=Decimal('33.041100'),
            address='14 Saripolou St',
            supported_buckets=[ActivityBucket.COFFEE_COWORK],
            interest_tags=['specialty_coffee', 'deep_work_sprint'],
            tier=VenueTier.SMALL_59,
        )

        women_table = MicroTable.objects.create(
            venue=venue,
            activity_bucket=ActivityBucket.COFFEE_COWORK,
            time_window=TimeWindow.RIGHT_NOW,
            scheduled_for=timezone.now() + timedelta(hours=2),
            safety_mode=TableSafetyMode.VERIFIED_WOMEN_ONLY,
        )
        man_user = CustomUser.objects.create_user(
            username='verified_man',
            password='Password123!',
            is_verified_human=True,
            verified_gender_group=VerifiedGenderGroup.MAN,
        )
        client = APIClient()
        client.force_authenticate(user=man_user)
        denied = client.post(f'/api/matches/micro-tables/{women_table.id}/claim-seat/', {}, format='json')
        assert denied.status_code == status.HTTP_403_FORBIDDEN

        # Auto-Table Matchmaker spawns a MicroTable when 3+ users post SMALL_GROUP_3_6
        for idx in range(3):
            u = CustomUser.objects.create_user(
                username=f'study_group_{idx}',
                password='Password123!',
                is_verified_human=True,
                is_verified_adult_18_plus=True,
            )
            Post.objects.create(
                user=u,
                activity_bucket=ActivityBucket.COFFEE_COWORK,
                time_window=TimeWindow.THIS_WEEKEND,
                confirmed_tags=['deep_work_sprint', 'specialty_coffee'],
                vibe_preference=VibePreference.SMALL_GROUP_3_6,
                city=City.LIMASSOL,
            )

        spawned = run_auto_table_matchmaker(
            city=City.LIMASSOL,
            activity_bucket=ActivityBucket.COFFEE_COWORK,
            time_window=TimeWindow.THIS_WEEKEND,
        )
        assert len(spawned) == 1
        auto_table = spawned[0]
        assert auto_table.venue_id == venue.id
        assert auto_table.status == MicroTableStatus.LOCKED_READY
        assert auto_table.seats.count() == 3
        assert auto_table.chat_room_id is not None

    def test_venue_qr_safe_arrival_checks_in_match_and_micro_table_seat(self):
        venue = Venue.objects.create(
            name='Kato Harbor Roasters',
            slug='kato-harbor-qr-test',
            city=City.PAPHOS,
            district_corridor='PAPHOS_KATO',
            latitude=Decimal('34.756800'),
            longitude=Decimal('32.408900'),
            address='19 Poseidonos Ave',
            supported_buckets=[ActivityBucket.COFFEE_COWORK],
            interest_tags=['specialty_coffee'],
            tier=VenueTier.FREE,
            avg_spend_estimate_eur=Decimal('12.00'),
        )
        qr = VenueTableQR.objects.create(
            venue=venue,
            table_label='Table 2',
            qr_secret_token='qr-kato-safe-arrival-t2',
        )

        u1 = CustomUser.objects.create_user(username='arrival_u1', password='Password123!', is_verified_human=True)
        u2 = CustomUser.objects.create_user(username='arrival_u2', password='Password123!', is_verified_human=True)
        match = Match.objects.create(
            user1=u1,
            user2=u2,
            status='matched',
            match_type='AI',
            suggested_venue=venue,
        )

        table = MicroTable.objects.create(
            venue=venue,
            activity_bucket=ActivityBucket.COFFEE_COWORK,
            time_window=TimeWindow.RIGHT_NOW,
            scheduled_for=timezone.now() + timedelta(hours=2),
            min_seats=1,
            max_seats=4,
            safety_mode=TableSafetyMode.VERIFIED_HUMANS_ONLY,
        )
        seat = MicroTableSeat.objects.create(table=table, user=u1, ephemeral_alias='Anon Fox')

        client = APIClient()
        client.force_authenticate(user=u1)

        # 1. Safe arrival for 1-on-1 match
        resp_match = client.post(
            '/api/venues/qr-checkin/',
            {'qr_secret_token': qr.qr_secret_token, 'match_id': str(match.id), 'party_size': 2},
            format='json',
        )
        assert resp_match.status_code == status.HTTP_201_CREATED
        assert resp_match.data['match_checked_in'] is True
        match.refresh_from_db()
        assert match.user1_checked_in_at is not None
        assert match.user2_checked_in_at is None

        # 2. Safe arrival for Micro-Table seat
        resp_table = client.post(
            '/api/venues/qr-checkin/',
            {'qr_secret_token': qr.qr_secret_token, 'micro_table_id': str(table.id), 'party_size': 1},
            format='json',
        )
        assert resp_table.status_code == status.HTTP_201_CREATED
        assert resp_table.data['micro_table_checked_in'] is True
        seat.refresh_from_db()
        table.refresh_from_db()
        assert seat.checked_in_via_qr is True
        assert table.status == MicroTableStatus.CHECKED_IN
        assert VenueWalkInRedemption.objects.filter(venue=venue, table_qr=qr).count() == 2
