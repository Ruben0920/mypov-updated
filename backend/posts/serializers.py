from rest_framework import serializers

from venues.models import ActivityBucket, City, Venue
from venues.serializers import VenueDiscoveryItemSerializer

from .constants import TimeWindow, VibePreference
from .models import Post


class PostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = [
            'id',
            'user',
            'activity_bucket',
            'time_window',
            'confirmed_tags',
            'vibe_preference',
            'city',
            'preferred_venue',
            'created_at',
            'expires_at',
            'detected_objects',
            'inferred_interests',
        ]
        read_only_fields = ['id', 'user', 'created_at', 'expires_at']


class ExtractIntentSerializer(serializers.Serializer):
    text = serializers.CharField(required=False, allow_blank=True, max_length=140, default='')
    activity_bucket = serializers.ChoiceField(
        choices=ActivityBucket.choices,
        required=False,
        allow_null=True,
        allow_blank=True,
    )


class ConfirmIntentSerializer(serializers.Serializer):
    activity_bucket = serializers.ChoiceField(choices=ActivityBucket.choices)
    time_window = serializers.ChoiceField(choices=TimeWindow.choices)
    confirmed_tags = serializers.ListField(
        child=serializers.CharField(max_length=64),
        min_length=1,
        max_length=5,
    )
    vibe_preference = serializers.ChoiceField(
        choices=VibePreference.choices,
        required=False,
        default=VibePreference.EITHER,
    )
    city = serializers.ChoiceField(
        choices=City.choices,
        required=False,
        default=City.LIMASSOL,
    )
    preferred_venue_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_confirmed_tags(self, tags):
        cleaned = []
        for tag in tags:
            normalized = tag.strip().lower().replace(' ', '_')
            if normalized and normalized not in cleaned:
                cleaned.append(normalized)
        if not cleaned:
            raise serializers.ValidationError("At least 1 valid tag is required.")
        if len(cleaned) > 5:
            raise serializers.ValidationError("Maximum 5 confirmed tags allowed.")
        return cleaned

    def validate_preferred_venue_id(self, value):
        if value is None:
            return None
        if not Venue.objects.filter(pk=value, is_active=True).exists():
            raise serializers.ValidationError("Preferred venue not found or inactive.")
        return value


class ConfirmIntentResponseSerializer(serializers.Serializer):
    post = PostSerializer()
    recommended_venues = VenueDiscoveryItemSerializer(many=True)
