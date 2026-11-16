from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Venue, VenueTableQR
from .serializers import (
    QRCheckinInputSerializer,
    VenueDiscoveryItemSerializer,
    VenueVibeReportSerializer,
    VibeSubmissionSerializer,
)
from .services import (
    PerkRedemptionConflict,
    RateLimitExceeded,
    get_venue_roi_summary,
    process_qr_checkin,
    rank_venues,
    submit_vibe_report,
)


def _parse_optional_float(raw_value, field_name: str):
    if raw_value is None or raw_value == '':
        return None
    try:
        return float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid coordinate for '{field_name}'.") from exc


class VenueDiscoverView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, *args, **kwargs):
        city = request.query_params.get('city') or None
        bucket = request.query_params.get('bucket') or None
        raw_tags = request.query_params.get('tags', '')
        tags = [t.strip() for t in raw_tags.split(',') if t.strip()] if raw_tags else []

        try:
            lat = _parse_optional_float(request.query_params.get('lat'), 'lat')
            lng = _parse_optional_float(request.query_params.get('lng'), 'lng')
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        ranked = rank_venues(city=city, bucket=bucket, tags=tags, lat=lat, lng=lng)

        payload = []
        for item in ranked:
            venue_obj = item['venue']
            # Attach computed attributes for serializer
            venue_obj.score = item['score']
            venue_obj.tag_overlap_count = item['tag_overlap_count']
            venue_obj.distance_km = item['distance_km']
            venue_obj.live_vibe = item['live_vibe']
            venue_obj.active_flash_perks = item['active_flash_perks']
            payload.append(venue_obj)

        serializer = VenueDiscoveryItemSerializer(payload, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class VenueVibeSubmitView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, venue_id, *args, **kwargs):
        venue = get_object_or_404(Venue, pk=venue_id, is_active=True)
        serializer = VibeSubmissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            report = submit_vibe_report(
                venue=venue,
                user=request.user,
                crowd_level=data['crowd_level'],
                noise_level=data['noise_level'],
                current_best_for=data['current_best_for'],
                vibe_preset_badge=data['vibe_preset_badge'],
            )
        except RateLimitExceeded as exc:
            return Response({'error': str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        return Response(VenueVibeReportSerializer(report).data, status=status.HTTP_201_CREATED)


class VenueQRCheckinView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = QRCheckinInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            outcome = process_qr_checkin(
                user=request.user,
                qr_secret_token=data['qr_secret_token'],
                match_id=data.get('match_id'),
                micro_table_id=data.get('micro_table_id'),
                party_size=data.get('party_size', 1),
                flash_perk_id=data.get('flash_perk_id'),
                vibe_data=data.get('vibe'),
            )
        except VenueTableQR.DoesNotExist:
            return Response({'error': 'Invalid or inactive table QR token.'}, status=status.HTTP_404_NOT_FOUND)
        except PerkRedemptionConflict as exc:
            return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            return Response({'error': exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST)

        redemption = outcome['redemption']
        redeemed_perk = outcome['redeemed_perk']
        return Response(
            {
                'redemption_id': redemption.id,
                'venue_id': str(outcome['venue'].id),
                'venue_name': outcome['venue'].name,
                'table_label': outcome['table_qr'].table_label,
                'party_size': redemption.party_size,
                'estimated_revenue_eur': float(redemption.estimated_revenue_eur),
                'perk_redeemed': redeemed_perk is not None,
                'flash_perk_id': redeemed_perk.id if redeemed_perk else None,
                'flash_perk_title': redeemed_perk.title if redeemed_perk else None,
                'vibe_recorded': outcome['vibe_report'] is not None,
                'monthly_verified_walk_ins': outcome['monthly_verified_walk_ins'],
                'upgrade_prompt_eligible': outcome['upgrade_prompt_eligible'],
                'match_checked_in': outcome['match_checked_in'],
                'micro_table_checked_in': outcome['micro_table_checked_in'],
            },
            status=status.HTTP_201_CREATED,
        )


class VenueROISummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, venue_id, *args, **kwargs):
        venue = get_object_or_404(Venue, pk=venue_id)
        return Response(get_venue_roi_summary(venue), status=status.HTTP_200_OK)
