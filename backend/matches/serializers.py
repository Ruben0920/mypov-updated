from rest_framework import serializers

from .models import Match, MicroTable, MicroTableSeat


class MatchSerializer(serializers.ModelSerializer):
    suggested_venue_name = serializers.CharField(
        source='suggested_venue.name',
        read_only=True,
        default=None,
    )
    user1_ephemeral_handle = serializers.CharField(
        source='user1.ephemeral_handle',
        read_only=True,
    )
    user2_ephemeral_handle = serializers.CharField(
        source='user2.ephemeral_handle',
        read_only=True,
    )

    class Meta:
        model = Match
        fields = [
            'id',
            'user1',
            'user2',
            'user1_ephemeral_handle',
            'user2_ephemeral_handle',
            'status',
            'score',
            'match_type',
            'activity_bucket',
            'time_window',
            'suggested_venue',
            'suggested_venue_name',
            'user1_checked_in_at',
            'user2_checked_in_at',
            'created_at',
        ]


class MicroTableSeatSerializer(serializers.ModelSerializer):
    is_verified_human = serializers.BooleanField(
        source='user.is_verified_human',
        read_only=True,
    )

    class Meta:
        model = MicroTableSeat
        fields = [
            'id',
            'ephemeral_alias',
            'is_verified_human',
            'checked_in_via_qr',
            'joined_at',
        ]


class MicroTableSerializer(serializers.ModelSerializer):
    venue_name = serializers.CharField(source='venue.name', read_only=True)
    city = serializers.CharField(source='venue.city', read_only=True)
    seat_count = serializers.SerializerMethodField()
    seats_remaining = serializers.SerializerMethodField()
    seats = MicroTableSeatSerializer(many=True, read_only=True)

    class Meta:
        model = MicroTable
        fields = [
            'id',
            'venue',
            'venue_name',
            'city',
            'activity_bucket',
            'time_window',
            'scheduled_for',
            'min_seats',
            'max_seats',
            'seat_count',
            'seats_remaining',
            'safety_mode',
            'status',
            'perk_description',
            'chat_room_id',
            'seats',
        ]

    def get_seat_count(self, obj):
        return obj.seats.count()

    def get_seats_remaining(self, obj):
        return max(0, obj.max_seats - obj.seats.count())
