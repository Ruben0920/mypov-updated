from datetime import timedelta

from django.core.management import call_command
from django.utils import timezone
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from chat.models import Chat, ChatMessage
from matches.models import Match
from posts.constants import TimeWindow
from posts.models import Post
from users.models import CustomUser
from venues.models import ActivityBucket, City


@pytest.mark.django_db
class TestE2EEChatAndMutualReveal:
    def test_mutual_consent_identity_reveal_gate_and_e2ee_messages(self):
        user_a = CustomUser.objects.create_user(
            username='real_alice_ig',
            password='Password123!',
            ephemeral_handle='@limassol_padel_42',
            is_verified_human=True,
            is_verified_adult_18_plus=True,
            e2ee_public_key='pub_key_alice_001',
        )
        user_b = CustomUser.objects.create_user(
            username='real_bob_ig',
            password='Password123!',
            ephemeral_handle='@molos_coffee_99',
            is_verified_human=True,
            is_verified_adult_18_plus=True,
            e2ee_public_key='pub_key_bob_002',
        )

        Post.objects.create(
            user=user_a,
            activity_bucket=ActivityBucket.SPORTS_PADEL,
            time_window=TimeWindow.TONIGHT,
            confirmed_tags=['padel_doubles'],
            city=City.LIMASSOL,
        )
        Post.objects.create(
            user=user_b,
            activity_bucket=ActivityBucket.SPORTS_PADEL,
            time_window=TimeWindow.TONIGHT,
            confirmed_tags=['padel_doubles'],
            city=City.LIMASSOL,
        )

        match = Match.objects.create(
            user1=user_a,
            user2=user_b,
            status='matched',
            match_type='AI',
            activity_bucket=ActivityBucket.SPORTS_PADEL,
        )
        room = Chat.objects.create(match=match)

        client_a = APIClient()
        client_b = APIClient()
        client_a.force_authenticate(user=user_a)
        client_b.force_authenticate(user=user_b)

        # Send E2EE encrypted message from User B
        msg_resp = client_b.post(
            f'/api/chat/rooms/{room.room_uuid}/messages/',
            {
                'ciphertext': 'base64_encrypted_payload_xyz',
                'nonce': 'nonce_24bytes_abc',
            },
            format='json',
        )
        assert msg_resp.status_code == status.HTTP_201_CREATED
        assert msg_resp.data['ciphertext'] == 'base64_encrypted_payload_xyz'
        assert msg_resp.data['nonce'] == 'nonce_24bytes_abc'

        # Before consent: User A sees User B's ephemeral_handle, tags, badge, pubkey, but real username is masked
        room_before = client_a.get(f'/api/chat/rooms/{room.room_uuid}/')
        assert room_before.status_code == status.HTTP_200_OK
        participants_before = {p['id']: p for p in room_before.data['participants']}
        bob_view_before = participants_before[user_b.id]
        assert bob_view_before['ephemeral_handle'] == '@molos_coffee_99'
        assert bob_view_before['verification_badge'] == 'Verified 18+ Human'
        assert bob_view_before['confirmed_tags'] == ['padel_doubles']
        assert bob_view_before['e2ee_public_key'] == 'pub_key_bob_002'
        assert bob_view_before['is_revealed'] is False
        assert bob_view_before['username'] is None
        assert bob_view_before['revealed_username'] is None
        assert room_before.data['messages'][0]['sender']['username'] is None

        # User A consents to reveal -> User B's real identity is STILL masked!
        reveal_a = client_a.post(f'/api/chat/rooms/{room.room_uuid}/request-reveal/', {}, format='json')
        assert reveal_a.status_code == status.HTTP_200_OK
        assert reveal_a.data['consented_to_reveal'] is True
        assert reveal_a.data['mutually_revealed'] is False

        room_half = client_a.get(f'/api/chat/rooms/{room.room_uuid}/')
        bob_view_half = {p['id']: p for p in room_half.data['participants']}[user_b.id]
        assert bob_view_half['is_revealed'] is False
        assert bob_view_half['username'] is None

        # User B also consents -> mutual reveal unlocks BOTH real usernames!
        reveal_b = client_b.post(f'/api/chat/rooms/{room.room_uuid}/request-reveal/', {}, format='json')
        assert reveal_b.status_code == status.HTTP_200_OK
        assert reveal_b.data['mutually_revealed'] is True

        room_after_a = client_a.get(f'/api/chat/rooms/{room.room_uuid}/')
        bob_view_after = {p['id']: p for p in room_after_a.data['participants']}[user_b.id]
        assert bob_view_after['is_revealed'] is True
        assert bob_view_after['username'] == 'real_bob_ig'
        assert bob_view_after['revealed_username'] == 'real_bob_ig'
        assert room_after_a.data['messages'][0]['sender']['username'] == 'real_bob_ig'

        room_after_b = client_b.get(f'/api/chat/rooms/{room.room_uuid}/')
        alice_view_after = {p['id']: p for p in room_after_b.data['participants']}[user_a.id]
        assert alice_view_after['is_revealed'] is True
        assert alice_view_after['username'] == 'real_alice_ig'

    def test_cleanup_expired_chats_purges_rooms_and_messages(self):
        u1 = CustomUser.objects.create_user(username='exp_c1', password='Password123!')
        u2 = CustomUser.objects.create_user(username='exp_c2', password='Password123!')
        u3 = CustomUser.objects.create_user(username='act_c3', password='Password123!')

        m_exp = Match.objects.create(user1=u1, user2=u2, status='matched')
        m_act = Match.objects.create(user1=u1, user2=u3, status='matched')

        expired_room = Chat.objects.create(
            match=m_exp,
            last_activity=timezone.now() - timedelta(hours=25),
        )
        active_room = Chat.objects.create(
            match=m_act,
            last_activity=timezone.now(),
        )
        ChatMessage.objects.create(chat=expired_room, sender=u1, ciphertext='old', nonce='n1')
        ChatMessage.objects.create(chat=active_room, sender=u1, ciphertext='new', nonce='n2')

        call_command('cleanup_expired_chats')

        assert not Chat.objects.filter(pk=expired_room.pk).exists()
        assert not ChatMessage.objects.filter(chat_id=expired_room.pk).exists()
        assert Chat.objects.filter(pk=active_room.pk).exists()
        assert ChatMessage.objects.filter(chat_id=active_room.pk).count() == 1
