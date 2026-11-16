from collections import defaultdict
import os
import tempfile

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from chat.models import Chat, ChatParticipantConsent
from posts.models import Post

from .controllers import detect_objects
from .models import Match, MicroTable, MicroTableStatus
from .serializers import MatchSerializer, MicroTableSeatSerializer, MicroTableSerializer
from .services import (
    TableFullConflict,
    claim_micro_table_seat,
    generate_ai_matches_for_user,
    is_user_eligible_for_table,
    run_auto_table_matchmaker,
)


class ImageUploadView(APIView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, *args, **kwargs):
        if 'file' not in request.FILES:
            return JsonResponse({'error': 'No file part'}, status=400)

        file_obj = request.FILES['file']
        if file_obj.name == '':
            return JsonResponse({'error': 'No selected file'}, status=400)

        temp_file_path = self.save_temp_file(file_obj)
        detected_objects_list_of_dicts = detect_objects(temp_file_path)

        label_count = defaultdict(int)
        for item in detected_objects_list_of_dicts:
            label = item.get('label')
            if label:
                label_count[label] += 1

        counted_detected_objects = [
            {'label': label, 'count': count} for label, count in label_count.items()
        ]
        os.remove(temp_file_path)
        return JsonResponse(counted_detected_objects, safe=False)

    def save_temp_file(self, file_obj):
        original_suffix = os.path.splitext(file_obj.name)[1] or '.jpg'
        with tempfile.NamedTemporaryFile(delete=False, suffix=original_suffix) as temp_file:
            for chunk in file_obj.chunks():
                temp_file.write(chunk)
            temp_file_path = temp_file.name
        return temp_file_path


class GetAIMatchesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        ai_matches = (
            Match.objects.filter(
                (Q(user1=user) | Q(user2=user))
                & Q(match_type='AI')
                & Q(status__in=['pending_ai', 'matched'])
            )
            .select_related('user1', 'user2', 'suggested_venue')
            .order_by('-score', '-updated_at')
        )

        serializer = MatchSerializer(ai_matches, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class GenerateMatchesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        matches_info = generate_ai_matches_for_user(request.user)
        spawned_tables = run_auto_table_matchmaker(
            city=request.data.get('city') or None,
            activity_bucket=request.data.get('activity_bucket') or None,
            time_window=request.data.get('time_window') or None,
        )
        return Response(
            {
                'matches': matches_info,
                'spawned_micro_tables': [str(t.id) for t in spawned_tables],
            },
            status=status.HTTP_200_OK,
        )


class InteractAIMatchView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, match_id, *args, **kwargs):
        user = request.user
        action = request.data.get('action')

        try:
            match_instance = Match.objects.get(
                pk=match_id,
                match_type='AI',
                status='pending_ai',
            )
            if match_instance.user1 != user and match_instance.user2 != user:
                return Response(
                    {'error': 'You are not part of this match to interact.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        except Match.DoesNotExist:
            return Response(
                {'error': 'AI Match not found or not in a state to be acted upon.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if action == 'accept':
            match_instance.status = 'matched'
            match_instance.save()
            chat_room, _ = Chat.objects.get_or_create(
                match=match_instance,
                defaults={'last_activity': timezone.now()},
            )
            ChatParticipantConsent.objects.get_or_create(
                chat=chat_room,
                user=match_instance.user1,
                target_user=None,
            )
            ChatParticipantConsent.objects.get_or_create(
                chat=chat_room,
                user=match_instance.user2,
                target_user=None,
            )
            data = MatchSerializer(match_instance, context={'request': request}).data
            data['chat_room_id'] = str(chat_room.room_uuid)
            return Response(data, status=status.HTTP_200_OK)
        if action == 'decline':
            match_instance.status = 'declined'
            match_instance.save()
            return Response({'message': 'AI Match declined.'}, status=status.HTTP_200_OK)
        return Response({'error': 'Invalid action provided.'}, status=status.HTTP_400_BAD_REQUEST)


class MicroTableListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        city = request.query_params.get('city') or None
        bucket = request.query_params.get('activity_bucket') or request.query_params.get('bucket') or None

        if not city:
            latest_post = Post.objects.filter(user=request.user).order_by('-created_at').first()
            if latest_post:
                city = latest_post.city

        qs = MicroTable.objects.filter(
            status__in=[MicroTableStatus.FORMING, MicroTableStatus.LOCKED_READY],
        ).select_related('venue').prefetch_related('seats__user')

        if city:
            qs = qs.filter(venue__city=city)
        if bucket:
            qs = qs.filter(activity_bucket=bucket)

        eligible_tables = [t for t in qs if is_user_eligible_for_table(request.user, t)]
        return Response(MicroTableSerializer(eligible_tables, many=True).data, status=status.HTTP_200_OK)


class MicroTableClaimSeatView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, table_id, *args, **kwargs):
        post_id = request.data.get('post_id')
        post = None
        if post_id:
            post = get_object_or_404(Post, pk=post_id, user=request.user)

        try:
            table, seat, created = claim_micro_table_seat(
                table_id=table_id,
                user=request.user,
                post=post,
            )
        except MicroTable.DoesNotExist:
            return Response({'error': 'Micro-Table not found.'}, status=status.HTTP_404_NOT_FOUND)
        except PermissionDenied as exc:
            return Response({'error': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except TableFullConflict as exc:
            return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
        except ValidationError as exc:
            return Response({'error': exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST)

        table_data = MicroTableSerializer(table).data
        table_data['claimed_seat'] = MicroTableSeatSerializer(seat).data
        return Response(
            table_data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
