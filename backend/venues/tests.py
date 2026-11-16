from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.utils import timezone
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from users.models import CustomUser
from venues.models import (
    ActivityBucket,
    City,
    CrowdLevel,
    NoiseLevel,
    Venue,
    VenueFlashPerk,
    VenueTableQR,
    VenueTier,
    VenueVibeReport,
    VibePresetBadge,
)


@pytest.mark.django_db
class TestVenuesAndTelemetry:
    def test_venue_ranking_respects_bucket_gate_over_paid_tier_boost(self):
        free_coffee = Venue.objects.create(
            name='Quiet Bean',
            slug='quiet-bean',
            city=City.LIMASSOL,
            district_corridor='LIMASSOL_OLD_TOWN_SARIPOLOU',
            latitude=Decimal('34.675000'),
            longitude=Decimal('33.041000'),
            address='10 Saripolou St',
            supported_buckets=[ActivityBucket.COFFEE_COWORK],
            interest_tags=['specialty_coffee', 'quiet_wifi'],
            tier=VenueTier.FREE,
        )
        paid_coffee = Venue.objects.create(
            name='Starter Roast',
            slug='starter-roast',
            city=City.LIMASSOL,
            district_corridor='LIMASSOL_OLD_TOWN_SARIPOLOU',
            latitude=Decimal('34.675100'),
            longitude=Decimal('33.041200'),
            address='12 Saripolou St',
            supported_buckets=[ActivityBucket.COFFEE_COWORK],
            interest_tags=['specialty_coffee', 'quiet_wifi'],
            tier=VenueTier.BIG_129,
        )
        # Paid BIG_129 venue in a DIFFERENT bucket must never be boosted into COFFEE_COWORK
        paid_nightclub = Venue.objects.create(
            name='Mega Club',
            slug='mega-club',
            city=City.LIMASSOL,
            district_corridor='LIMASSOL_MOLOS',
            latitude=Decimal('34.674000'),
            longitude=Decimal('33.050000'),
            address='99 Molos Ave',
            supported_buckets=[ActivityBucket.DRINKS_NIGHTLIFE],
            interest_tags=['specialty_coffee', 'quiet_wifi', 'cocktails'],
            tier=VenueTier.BIG_129,
        )

        client = APIClient()
        resp = client.get(
            '/api/venues/discover/',
            {
                'city': City.LIMASSOL,
                'bucket': ActivityBucket.COFFEE_COWORK,
                'tags': 'specialty_coffee,quiet_wifi',
            },
        )
        assert resp.status_code == status.HTTP_200_OK
        returned_ids = [item['id'] for item in resp.data]
        assert str(paid_nightclub.id) not in returned_ids
        assert returned_ids == [str(paid_coffee.id), str(free_coffee.id)]

    def test_vibe_telemetry_two_hour_decay_and_rate_limit(self):
        user = CustomUser.objects.create_user(username='vibe_user', password='Password123!')
        venue = Venue.objects.create(
            name='Molos Brew',
            slug='molos-brew',
            city=City.LIMASSOL,
            district_corridor='LIMASSOL_MOLOS',
            latitude=Decimal('34.673000'),
            longitude=Decimal('33.049000'),
            address='1 Molos',
            supported_buckets=[ActivityBucket.COFFEE_COWORK],
            interest_tags=['specialty_coffee'],
            tier=VenueTier.FREE,
        )

        now = timezone.now()
        # Expired report from > 2 hours ago should be excluded from live vibe aggregation
        VenueVibeReport.objects.create(
            venue=venue,
            user=None,
            crowd_level=CrowdLevel.PACKED,
            noise_level=NoiseLevel.LOUD_MUSIC,
            current_best_for=ActivityBucket.DRINKS_NIGHTLIFE,
            vibe_preset_badge=VibePresetBadge.GREAT_MUSIC,
            expires_at=now - timedelta(minutes=5),
        )

        client = APIClient()
        discover_before = client.get('/api/venues/discover/', {'city': City.LIMASSOL})
        assert discover_before.status_code == status.HTTP_200_OK
        assert discover_before.data[0]['live_vibe']['active_report_count'] == 0
        assert discover_before.data[0]['live_vibe']['dominant_crowd_level'] is None

        # Reject free-text / unsupported fields
        client.force_authenticate(user=user)
        bad_vibe = client.post(
            f'/api/venues/{venue.id}/vibe/',
            {
                'crowd_level': CrowdLevel.QUIET,
                'noise_level': NoiseLevel.STUDY_QUIET,
                'current_best_for': ActivityBucket.COFFEE_COWORK,
                'vibe_preset_badge': VibePresetBadge.LAPTOP_FRIENDLY,
                'comment': 'free text spam',
            },
            format='json',
        )
        assert bad_vibe.status_code == status.HTTP_400_BAD_REQUEST

        # Submit valid pre-selected vibe report
        good_vibe = client.post(
            f'/api/venues/{venue.id}/vibe/',
            {
                'crowd_level': CrowdLevel.QUIET,
                'noise_level': NoiseLevel.STUDY_QUIET,
                'current_best_for': ActivityBucket.COFFEE_COWORK,
                'vibe_preset_badge': VibePresetBadge.LAPTOP_FRIENDLY,
            },
            format='json',
        )
        assert good_vibe.status_code == status.HTTP_201_CREATED

        # Rate-limited if submitted again within 60 minutes
        second_vibe = client.post(
            f'/api/venues/{venue.id}/vibe/',
            {
                'crowd_level': CrowdLevel.MODERATE,
                'noise_level': NoiseLevel.CONVERSATIONAL,
                'current_best_for': ActivityBucket.COFFEE_COWORK,
                'vibe_preset_badge': VibePresetBadge.FAST_SERVICE,
            },
            format='json',
        )
        assert second_vibe.status_code == status.HTTP_429_TOO_MANY_REQUESTS

        discover_after = client.get('/api/venues/discover/', {'city': City.LIMASSOL})
        assert discover_after.status_code == status.HTTP_200_OK
        live_vibe = discover_after.data[0]['live_vibe']
        assert live_vibe['active_report_count'] == 1
        assert live_vibe['dominant_crowd_level'] == CrowdLevel.QUIET
        assert live_vibe['dominant_noise_level'] == NoiseLevel.STUDY_QUIET
        assert live_vibe['dominant_vibe_preset_badge'] == VibePresetBadge.LAPTOP_FRIENDLY

    def test_atomic_qr_checkin_flash_perk_cap_and_roi_summary(self):
        user = CustomUser.objects.create_user(username='qr_guest', password='Password123!')
        venue = Venue.objects.create(
            name='Saripolou Tap',
            slug='saripolou-tap',
            city=City.LIMASSOL,
            district_corridor='LIMASSOL_OLD_TOWN_SARIPOLOU',
            latitude=Decimal('34.675500'),
            longitude=Decimal('33.041500'),
            address='22 Saripolou',
            supported_buckets=[ActivityBucket.DRINKS_NIGHTLIFE],
            interest_tags=['craft_beer'],
            tier=VenueTier.FREE,
            avg_spend_estimate_eur=Decimal('15.00'),
            view_count=120,
            routed_matches_count=14,
        )
        table_qr = VenueTableQR.objects.create(
            venue=venue,
            table_label='Table 4',
            qr_secret_token='secret-saripolou-t4',
        )
        now = timezone.now()
        perk = VenueFlashPerk.objects.create(
            venue=venue,
            title='Free Craft Sampler',
            target_bucket=ActivityBucket.DRINKS_NIGHTLIFE,
            valid_from=now - timedelta(minutes=10),
            valid_until=now + timedelta(hours=3),
            max_redemptions=1,
            requires_companion_or_table=False,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        # First QR check-in redeems the single perk slot (party_size=3 -> €45.00)
        checkin1 = client.post(
            '/api/venues/qr-checkin/',
            {
                'qr_secret_token': 'secret-saripolou-t4',
                'party_size': 3,
                'flash_perk_id': perk.id,
                'vibe': {
                    'crowd_level': CrowdLevel.LIVELY,
                    'noise_level': NoiseLevel.CONVERSATIONAL,
                    'current_best_for': ActivityBucket.DRINKS_NIGHTLIFE,
                    'vibe_preset_badge': VibePresetBadge.SOCIAL_CROWD,
                },
            },
            format='json',
        )
        assert checkin1.status_code == status.HTTP_201_CREATED
        assert checkin1.data['perk_redeemed'] is True
        assert checkin1.data['estimated_revenue_eur'] == 45.0
        assert checkin1.data['vibe_recorded'] is True
        assert checkin1.data['upgrade_prompt_eligible'] is False

        perk.refresh_from_db()
        assert perk.redeemed_count == 1

        # Explicit attempt to redeem the capped perk returns 409 Conflict
        capped_attempt = client.post(
            '/api/venues/qr-checkin/',
            {
                'qr_secret_token': 'secret-saripolou-t4',
                'party_size': 2,
                'flash_perk_id': perk.id,
            },
            format='json',
        )
        assert capped_attempt.status_code == status.HTTP_409_CONFLICT
        perk.refresh_from_db()
        assert perk.redeemed_count == 1

        # Complete 4 more standard check-ins so FREE venue hits 5 monthly walk-ins -> upgrade_prompt_eligible
        for _ in range(4):
            resp = client.post(
                '/api/venues/qr-checkin/',
                {'qr_secret_token': 'secret-saripolou-t4', 'party_size': 1},
                format='json',
            )
            assert resp.status_code == status.HTTP_201_CREATED
            assert resp.data['perk_redeemed'] is False

        roi = client.get(f'/api/venues/{venue.id}/roi-summary/')
        assert roi.status_code == status.HTTP_200_OK
        assert roi.data['views'] == 120
        assert roi.data['intention_matches_routed'] == 14
        assert roi.data['verified_walk_ins'] == 5
        assert roi.data['total_party_size_seated'] == 7
        assert roi.data['estimated_revenue_eur'] == 105.0
        assert roi.data['upgrade_prompt_eligible'] is True

    def test_seed_cyprus_venues_command_is_idempotent(self):
        call_command('seed_cyprus_venues')
        assert Venue.objects.count() == 15
        assert Venue.objects.filter(district_corridor='LIMASSOL_OLD_TOWN_SARIPOLOU').count() == 5
        assert Venue.objects.filter(district_corridor='LIMASSOL_MOLOS').count() == 5
        assert Venue.objects.filter(district_corridor='PAPHOS_KATO').count() == 5

        # Re-running does not create duplicate venues
        call_command('seed_cyprus_venues')
        assert Venue.objects.count() == 15
