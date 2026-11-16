from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from matches.controllers import detect_objects, infer_interests_from_objects
from users.verification import ImageValidationError
from venues.models import Venue
from venues.serializers import VenueDiscoveryItemSerializer
from venues.services import rank_venues

from .constants import compute_expires_at
from .models import Post
from .serializers import (
    ConfirmIntentSerializer,
    ExtractIntentSerializer,
    PostSerializer,
)
from .services import extract_intent_in_memory


class PostViewSet(viewsets.ModelViewSet):
    serializer_class = PostSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        now = timezone.now()
        return (
            Post.objects.filter(
                Q(expires_at__gt=now) | Q(expires_at__isnull=True),
            )
            .filter(
                Q(user__expires_at__gte=now) | Q(user__expires_at__isnull=True),
                user__is_active=True,
            )
            .order_by('-created_at')
        )

    def perform_create(self, serializer):
        post_instance = serializer.save(user=self.request.user)
        if post_instance.image:
            try:
                image_path = post_instance.image.path
                detected_obj_list_of_dicts = detect_objects(image_path)
                detected_labels = [
                    obj['label'] for obj in detected_obj_list_of_dicts if 'label' in obj
                ]
                inferred_interest_list = (
                    infer_interests_from_objects(detected_labels) if detected_labels else []
                )
                post_instance.detected_objects = detected_obj_list_of_dicts
                post_instance.inferred_interests = inferred_interest_list
                post_instance.save()
            except Exception as exc:
                print(f"Error during AI processing for post {post_instance.id}: {exc}")


class ExtractIntentView(APIView):
    """
    Step 1 of Ephemeral Intent Creation:
    Accepts an optional in-memory photo + optional short text line (<=140 chars),
    suggests canonical bucket & tags, and immediately discards the image buffer from RAM.
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def post(self, request, *args, **kwargs):
        serializer = ExtractIntentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        uploaded_file = request.FILES.get('image') or request.FILES.get('file')
        image_bytes = None
        try:
            if uploaded_file is not None:
                image_bytes = uploaded_file.read()
            result = extract_intent_in_memory(
                image_bytes=image_bytes,
                text=validated.get('text', ''),
                activity_bucket=validated.get('activity_bucket') or None,
            )
            return Response(result, status=status.HTTP_200_OK)
        except ImageValidationError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        finally:
            if uploaded_file is not None:
                try:
                    uploaded_file.close()
                except Exception:
                    pass
            if image_bytes is not None:
                del image_bytes


class ConfirmIntentView(APIView):
    """
    Step 2 of Ephemeral Intent Creation:
    Confirms canonical bucket, time window, and 1-5 tags, persists the zero-media Post,
    and returns top 5 ranked Venue recommendations for that bucket + tag set.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = ConfirmIntentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        preferred_venue = None
        if data.get('preferred_venue_id'):
            preferred_venue = Venue.objects.get(pk=data['preferred_venue_id'])

        expires_at = compute_expires_at(data['time_window'])
        post = Post.objects.create(
            user=request.user,
            activity_bucket=data['activity_bucket'],
            time_window=data['time_window'],
            confirmed_tags=data['confirmed_tags'],
            inferred_interests=data['confirmed_tags'],
            vibe_preference=data['vibe_preference'],
            city=data['city'],
            preferred_venue=preferred_venue,
            expires_at=expires_at,
        )

        ranked = rank_venues(
            city=post.city,
            bucket=post.activity_bucket,
            tags=post.confirmed_tags,
            limit=5,
        )
        recommended_objs = []
        for item in ranked:
            v = item['venue']
            v.score = item['score']
            v.tag_overlap_count = item['tag_overlap_count']
            v.distance_km = item['distance_km']
            v.live_vibe = item['live_vibe']
            v.active_flash_perks = item['active_flash_perks']
            recommended_objs.append(v)

        return Response(
            {
                'post': PostSerializer(post).data,
                'recommended_venues': VenueDiscoveryItemSerializer(recommended_objs, many=True).data,
            },
            status=status.HTTP_201_CREATED,
        )
