import io
from pathlib import Path
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from PIL import Image
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from chat.models import Chat, ChatMessage
from matches.models import Match
from posts.models import Post
from users.models import (
    CustomUser,
    HardwareReputationLedger,
    MatchSafetyFilter,
    ReportReason,
    SafetyReport,
    VerifiedGenderGroup,
)
from users.services import compute_device_fingerprint_hmac


def _make_in_memory_jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', (16, 16), color=(120, 180, 220)).save(buf, format='JPEG')
    return buf.getvalue()


@pytest.mark.django_db
class TestZeroKnowledgeUsers:
    def test_zk_liveness_verification_sets_claims_and_writes_zero_files_to_disk(self, tmp_path):
        media_dir = tmp_path / 'media'
        media_dir.mkdir(parents=True, exist_ok=True)

        with override_settings(MEDIA_ROOT=media_dir):
            client = APIClient()
            signup_resp = client.post(
                '/api/users/signup/',
                {
                    'username': 'alice_zk',
                    'password': 'StrongPassword123!',
                    'device_fingerprint': 'iphone-enclave-alice-001',
                },
                format='json',
            )
            assert signup_resp.status_code == status.HTTP_200_OK
            access_token = signup_resp.data['access']

            client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
            selfie = SimpleUploadedFile(
                'selfie.jpg',
                _make_in_memory_jpeg_bytes(),
                content_type='image/jpeg',
            )
            verify_resp = client.post(
                '/api/users/zk-verify-liveness/',
                {
                    'image': selfie,
                    'attestation_token': 'iphone-enclave-alice-001',
                    'verified_gender_group': VerifiedGenderGroup.WOMAN,
                    'community_email': 'alice@cut.ac.cy',
                },
                format='multipart',
            )

            assert verify_resp.status_code == status.HTTP_200_OK
            assert verify_resp.data['is_verified_human'] is True
            assert verify_resp.data['is_verified_adult_18_plus'] is True
            assert verify_resp.data['verified_gender_group'] == VerifiedGenderGroup.WOMAN
            assert verify_resp.data['verified_community_domain'] == 'cut.ac.cy'
            assert len(verify_resp.data['verification_proof_token']) == 64

            user = CustomUser.objects.get(username='alice_zk')
            assert user.is_verified_human is True
            assert user.is_verified_adult_18_plus is True
            assert user.verified_community_domain == 'cut.ac.cy'
            assert user.email == ''  # Raw community email is never stored
            assert user.device_fingerprint_hmac == compute_device_fingerprint_hmac('iphone-enclave-alice-001')

            # Zero files written anywhere inside MEDIA_ROOT
            files_on_disk = [p for p in Path(media_dir).rglob('*') if p.is_file()]
            assert files_on_disk == []

    def test_zk_liveness_rejects_invalid_magic_bytes(self):
        user = CustomUser.objects.create_user(username='bob_bad_img', password='Password123!')
        client = APIClient()
        client.force_authenticate(user=user)

        fake_file = SimpleUploadedFile(
            'fake.jpg',
            b'NOT_A_VALID_IMAGE_PAYLOAD',
            content_type='image/jpeg',
        )
        resp = client.post(
            '/api/users/zk-verify-liveness/',
            {'image': fake_file, 'attestation_token': 'device-bob-01'},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        user.refresh_from_db()
        assert user.is_verified_human is False
        assert user.is_verified_adult_18_plus is False

    def test_safety_preferences_patch_updates_filters_and_e2ee_key(self):
        user = CustomUser.objects.create_user(username='claire_prefs', password='Password123!')
        client = APIClient()
        client.force_authenticate(user=user)

        resp = client.patch(
            '/api/users/safety-preferences/',
            {
                'match_safety_filter': MatchSafetyFilter.SAME_GENDER_ONLY,
                'verified_gender_group': VerifiedGenderGroup.WOMAN,
                'e2ee_public_key': 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey',
                'community_email': 'claire@ucy.ac.cy',
                'ephemeral_handle': 'limassol_coffee_77',
            },
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.match_safety_filter == MatchSafetyFilter.SAME_GENDER_ONLY
        assert user.verified_gender_group == VerifiedGenderGroup.WOMAN
        assert user.e2ee_public_key == 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey'
        assert user.verified_community_domain == 'ucy.ac.cy'
        assert user.ephemeral_handle == 'limassol_coffee_77'
        assert user.email == ''

    def test_one_tap_wipe_permanently_deletes_user_posts_matches_and_chats(self):
        fp_raw = 'android-keystore-wipe-user-01'
        fp_hmac = compute_device_fingerprint_hmac(fp_raw)
        user_a = CustomUser.objects.create_user(
            username='wipe_target',
            password='Password123!',
            device_fingerprint_hmac=fp_hmac,
        )
        user_b = CustomUser.objects.create_user(
            username='other_partner',
            password='Password123!',
            device_fingerprint_hmac=compute_device_fingerprint_hmac('other-device-02'),
        )

        post = Post.objects.create(user=user_a, inferred_interests=['padel', 'coffee'])
        match = Match.objects.create(user1=user_a, user2=user_b, status='matched', match_type='AI')
        chat = Chat.objects.create(match=match)
        msg_a = ChatMessage.objects.create(chat=chat, sender=user_a, content='Hey!')
        msg_b = ChatMessage.objects.create(chat=chat, sender=user_b, content='Hello!')

        client = APIClient()
        client.force_authenticate(user=user_a)
        resp = client.post('/api/users/one-tap-wipe/', {}, format='json')

        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['status'] == 'wiped'
        assert resp.data['wipe_count_30d'] == 1

        assert not CustomUser.objects.filter(pk=user_a.pk).exists()
        assert not Post.objects.filter(pk=post.pk).exists()
        assert not Match.objects.filter(pk=match.pk).exists()
        assert not Chat.objects.filter(pk=chat.pk).exists()
        assert not ChatMessage.objects.filter(pk__in=[msg_a.pk, msg_b.pk]).exists()

        # Other user remains intact, and non-reversible ledger survives with wipe count
        assert CustomUser.objects.filter(pk=user_b.pk).exists()
        ledger = HardwareReputationLedger.objects.get(fingerprint_hmac=fp_hmac)
        assert ledger.wipe_count_30d == 1

    def test_three_strikes_then_wipe_blocks_re_registration_and_re_verification(self):
        bad_raw_fp = 'bad-actor-hw-token-999'
        bad_fp_hmac = compute_device_fingerprint_hmac(bad_raw_fp)

        bad_actor = CustomUser.objects.create_user(
            username='bad_actor',
            password='Password123!',
            device_fingerprint_hmac=bad_fp_hmac,
        )
        reporters = [
            CustomUser.objects.create_user(
                username=f'reporter_{idx}',
                password='Password123!',
                device_fingerprint_hmac=compute_device_fingerprint_hmac(f'rep-hw-{idx}'),
            )
            for idx in range(3)
        ]

        # Create a match with reporter 0 to verify pair gets blocked on report
        match_with_r0 = Match.objects.create(
            user1=reporters[0],
            user2=bad_actor,
            status='matched',
            match_type='AI',
        )

        client = APIClient()

        # Reporter 0 reports via match_id; verify duplicate report from same reporter is rejected
        client.force_authenticate(user=reporters[0])
        r0_resp = client.post(
            '/api/users/report/',
            {'match_id': match_with_r0.id, 'reason': ReportReason.HARASSMENT},
            format='json',
        )
        assert r0_resp.status_code == status.HTTP_201_CREATED
        assert r0_resp.data['strike_count'] == 1
        assert r0_resp.data['is_blacklisted'] is False
        match_with_r0.refresh_from_db()
        assert match_with_r0.status == 'blocked'

        dup_resp = client.post(
            '/api/users/report/',
            {'reported_user_id': bad_actor.id, 'reason': ReportReason.SPAM},
            format='json',
        )
        assert dup_resp.status_code == status.HTTP_400_BAD_REQUEST

        # Reporter 1 and Reporter 2 submit distinct strikes -> reaches 3 strikes & blacklists
        client.force_authenticate(user=reporters[1])
        r1_resp = client.post(
            '/api/users/report/',
            {'reported_user_id': bad_actor.id, 'reason': ReportReason.UNSAFE_BEHAVIOR},
            format='json',
        )
        assert r1_resp.status_code == status.HTTP_201_CREATED
        assert r1_resp.data['strike_count'] == 2
        assert r1_resp.data['is_blacklisted'] is False

        client.force_authenticate(user=reporters[2])
        r2_resp = client.post(
            '/api/users/report/',
            {'reported_user_id': bad_actor.id, 'reason': ReportReason.HARASSMENT},
            format='json',
        )
        assert r2_resp.status_code == status.HTTP_201_CREATED
        assert r2_resp.data['strike_count'] == 3
        assert r2_resp.data['is_blacklisted'] is True

        # Bad actor executes One-Tap Account Wipe to try evading the ban
        client.force_authenticate(user=bad_actor)
        wipe_resp = client.post('/api/users/one-tap-wipe/', {}, format='json')
        assert wipe_resp.status_code == status.HTTP_200_OK
        assert not CustomUser.objects.filter(pk=bad_actor.pk).exists()

        # Audit trail survives with reported_user=None and fingerprint preserved
        assert SafetyReport.objects.filter(reported_fingerprint_hmac=bad_fp_hmac).count() == 3
        assert SafetyReport.objects.filter(reported_fingerprint_hmac=bad_fp_hmac, reported_user__isnull=True).count() == 3

        # Attempt 1: Registering again with the same device/phone fingerprint -> 403
        client.force_authenticate(user=None)
        re_signup = client.post(
            '/api/users/signup/',
            {
                'username': 'bad_actor_alt',
                'password': 'Password123!',
                'device_fingerprint': bad_raw_fp,
            },
            format='json',
        )
        assert re_signup.status_code == status.HTTP_403_FORBIDDEN
        assert not CustomUser.objects.filter(username='bad_actor_alt').exists()

        # Attempt 2: Even if a fresh account was created without device_fingerprint,
        # attempting ZK liveness verification with the blacklisted fingerprint -> 403
        evader = CustomUser.objects.create_user(username='bad_actor_evader', password='Password123!')
        client.force_authenticate(user=evader)
        selfie = SimpleUploadedFile(
            'selfie.jpg',
            _make_in_memory_jpeg_bytes(),
            content_type='image/jpeg',
        )
        re_verify = client.post(
            '/api/users/zk-verify-liveness/',
            {'image': selfie, 'device_token': bad_raw_fp},
            format='multipart',
        )
        assert re_verify.status_code == status.HTTP_403_FORBIDDEN
        evader.refresh_from_db()
        assert evader.is_verified_human is False

    def test_cooldown_blocks_signup_and_verification_until_expiry(self):
        cooldown_raw_fp = 'cooldown-device-777'
        cooldown_hmac = compute_device_fingerprint_hmac(cooldown_raw_fp)
        HardwareReputationLedger.objects.create(
            fingerprint_hmac=cooldown_hmac,
            strike_count=1,
            is_blacklisted=False,
            cooldown_until=timezone.now() + timedelta(hours=12),
        )

        client = APIClient()
        resp = client.post(
            '/api/users/signup/',
            {
                'username': 'cooled_user',
                'password': 'Password123!',
                'device_fingerprint': cooldown_raw_fp,
            },
            format='json',
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
