from datetime import timedelta
from decimal import Decimal
import io
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from PIL import Image
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from posts.constants import DEFAULT_LOCAL_TIMEZONE, TimeWindow, VibePreference
from posts.models import Post
from users.models import CustomUser
from venues.models import ActivityBucket, City, Venue, VenueTier


def _make_jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', (24, 24), color=(50, 140, 200)).save(buf, format='JPEG')
    return buf.getvalue()


@pytest.mark.django_db
class TestEphemeralIntentPosts:
    def test_extract_intent_returns_bucket_and_tags_with_zero_disk_writes(self, tmp_path):
        media_dir = tmp_path / 'media'
        media_dir.mkdir(parents=True, exist_ok=True)
        user = CustomUser.objects.create_user(username='intent_extractor', password='Password123!')

        with override_settings(MEDIA_ROOT=media_dir):
            client = APIClient()
            client.force_authenticate(user=user)
            photo = SimpleUploadedFile(
                'padel.jpg',
                _make_jpeg_bytes(),
                content_type='image/jpeg',
            )
            resp = client.post(
                '/api/posts/extract-intent/',
                {
                    'image': photo,
                    'text': 'Looking for padel doubles tonight near Molos',
                },
                format='multipart',
            )

            assert resp.status_code == status.HTTP_200_OK
            assert resp.data['suggested_bucket'] == ActivityBucket.SPORTS_PADEL
            assert 'padel_doubles' in resp.data['suggested_tags']
            assert 3 <= len(resp.data['suggested_tags']) <= 5
            assert resp.data['media_retained'] is False

            files_on_disk = [p for p in Path(media_dir).rglob('*') if p.is_file()]
            assert files_on_disk == []

    def test_extract_intent_rejects_invalid_image_magic_bytes(self):
        user = CustomUser.objects.create_user(username='bad_intent_img', password='Password123!')
        client = APIClient()
        client.force_authenticate(user=user)

        bad_file = SimpleUploadedFile('bad.jpg', b'not-a-jpeg', content_type='image/jpeg')
        resp = client.post(
            '/api/posts/extract-intent/',
            {'image': bad_file, 'text': 'coffee sprint'},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_confirm_intent_sets_time_window_expiry_and_returns_ranked_venues(self):
        user = CustomUser.objects.create_user(username='intent_creator', password='Password123!')
        coffee_venue = Venue.objects.create(
            name='Saripolou Study Roast',
            slug='saripolou-study-roast',
            city=City.LIMASSOL,
            district_corridor='LIMASSOL_OLD_TOWN_SARIPOLOU',
            latitude=Decimal('34.675000'),
            longitude=Decimal('33.041000'),
            address='14 Saripolou St',
            supported_buckets=[ActivityBucket.COFFEE_COWORK],
            interest_tags=['specialty_coffee', 'deep_work_sprint', 'quiet_wifi'],
            tier=VenueTier.SMALL_59,
        )
        unrelated_venue = Venue.objects.create(
            name='Night Club X',
            slug='night-club-x',
            city=City.LIMASSOL,
            district_corridor='LIMASSOL_MOLOS',
            latitude=Decimal('34.674000'),
            longitude=Decimal('33.050000'),
            address='99 Molos Ave',
            supported_buckets=[ActivityBucket.DRINKS_NIGHTLIFE],
            interest_tags=['specialty_coffee', 'deep_work_sprint'],
            tier=VenueTier.BIG_129,
        )

        client = APIClient()
        client.force_authenticate(user=user)

        before = timezone.now()
        resp = client.post(
            '/api/posts/confirm-intent/',
            {
                'activity_bucket': ActivityBucket.COFFEE_COWORK,
                'time_window': TimeWindow.RIGHT_NOW,
                'confirmed_tags': ['deep_work_sprint', 'specialty_coffee'],
                'vibe_preference': VibePreference.SMALL_GROUP_3_6,
                'city': City.LIMASSOL,
                'preferred_venue_id': str(coffee_venue.id),
            },
            format='json',
        )
        assert resp.status_code == status.HTTP_201_CREATED
        post_data = resp.data['post']
        assert post_data['activity_bucket'] == ActivityBucket.COFFEE_COWORK
        assert post_data['time_window'] == TimeWindow.RIGHT_NOW
        assert post_data['confirmed_tags'] == ['deep_work_sprint', 'specialty_coffee']

        post = Post.objects.get(pk=post_data['id'])
        assert not post.image
        # RIGHT_NOW expires ~3 hours after creation
        delta_seconds = (post.expires_at - before).total_seconds()
        assert 3 * 3600 - 15 <= delta_seconds <= 3 * 3600 + 15

        venue_ids = [v['id'] for v in resp.data['recommended_venues']]
        assert str(coffee_venue.id) in venue_ids
        assert str(unrelated_venue.id) not in venue_ids

        # Also check TONIGHT and THIS_WEEKEND expiry windows
        tonight_resp = client.post(
            '/api/posts/confirm-intent/',
            {
                'activity_bucket': ActivityBucket.DRINKS_NIGHTLIFE,
                'time_window': TimeWindow.TONIGHT,
                'confirmed_tags': ['craft_beer'],
                'city': City.LIMASSOL,
            },
            format='json',
        )
        assert tonight_resp.status_code == status.HTTP_201_CREATED
        tonight_post = Post.objects.get(pk=tonight_resp.data['post']['id'])
        local_exp = tonight_post.expires_at.astimezone(DEFAULT_LOCAL_TIMEZONE)
        assert local_exp.hour == 2 and local_exp.minute == 0

        weekend_resp = client.post(
            '/api/posts/confirm-intent/',
            {
                'activity_bucket': ActivityBucket.WALK_BEACH_SUNSET,
                'time_window': TimeWindow.THIS_WEEKEND,
                'confirmed_tags': ['sunset_walk'],
                'city': City.LIMASSOL,
            },
            format='json',
        )
        assert weekend_resp.status_code == status.HTTP_201_CREATED
        weekend_post = Post.objects.get(pk=weekend_resp.data['post']['id'])
        weekend_local = weekend_post.expires_at.astimezone(DEFAULT_LOCAL_TIMEZONE)
        assert weekend_local.weekday() == 6  # Sunday
        assert weekend_local.hour == 23 and weekend_local.minute == 59

    def test_expired_intentions_excluded_from_queries_and_cleaned_up(self):
        user = CustomUser.objects.create_user(username='expiry_user', password='Password123!')
        now = timezone.now()

        active_post = Post.objects.create(
            user=user,
            activity_bucket=ActivityBucket.COFFEE_COWORK,
            time_window=TimeWindow.RIGHT_NOW,
            confirmed_tags=['specialty_coffee'],
            expires_at=now + timedelta(hours=2),
        )
        expired_post = Post.objects.create(
            user=user,
            activity_bucket=ActivityBucket.COFFEE_COWORK,
            time_window=TimeWindow.RIGHT_NOW,
            confirmed_tags=['exam_study'],
            expires_at=now - timedelta(minutes=10),
        )

        client = APIClient()
        client.force_authenticate(user=user)
        list_resp = client.get('/api/posts/posts/')
        assert list_resp.status_code == status.HTTP_200_OK
        returned_ids = [item['id'] for item in list_resp.data]
        assert active_post.id in returned_ids
        assert expired_post.id not in returned_ids

        call_command('cleanup_expiry_data')
        assert Post.objects.filter(pk=active_post.id).exists()
        assert not Post.objects.filter(pk=expired_post.id).exists()

    def test_post_save_strips_any_legacy_image_field(self):
        user = CustomUser.objects.create_user(username='strip_img_user', password='Password123!')
        fake_upload = SimpleUploadedFile('secret.jpg', _make_jpeg_bytes(), content_type='image/jpeg')
        post = Post.objects.create(
            user=user,
            image=fake_upload,
            activity_bucket=ActivityBucket.FOOD_DINING,
            confirmed_tags=['tacos'],
        )
        post.refresh_from_db()
        assert not post.image
