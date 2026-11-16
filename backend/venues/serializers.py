from rest_framework import serializers

from .models import (
    ActivityBucket,
    CrowdLevel,
    NoiseLevel,
    Venue,
    VenueFlashPerk,
    VenueVibeReport,
    VibePresetBadge,
)


class VenueFlashPerkSerializer(serializers.ModelSerializer):
    remaining_redemptions = serializers.SerializerMethodField()

    class Meta:
        model = VenueFlashPerk
        fields = [
            'id',
            'title',
            'target_bucket',
            'valid_from',
            'valid_until',
            'max_redemptions',
            'redeemed_count',
            'remaining_redemptions',
            'requires_companion_or_table',
        ]

    def get_remaining_redemptions(self, obj):
        return max(0, obj.max_redemptions - obj.redeemed_count)


class VenueDiscoveryItemSerializer(serializers.ModelSerializer):
    score = serializers.FloatField(read_only=True)
    tag_overlap_count = serializers.IntegerField(read_only=True)
    distance_km = serializers.FloatField(read_only=True, allow_null=True)
    live_vibe = serializers.DictField(read_only=True)
    active_flash_perks = VenueFlashPerkSerializer(many=True, read_only=True)

    class Meta:
        model = Venue
        fields = [
            'id',
            'name',
            'slug',
            'city',
            'district_corridor',
            'latitude',
            'longitude',
            'address',
            'supported_buckets',
            'interest_tags',
            'tier',
            'is_weekly_map_anchor',
            'default_discount_text',
            'avg_spend_estimate_eur',
            'score',
            'tag_overlap_count',
            'distance_km',
            'live_vibe',
            'active_flash_perks',
        ]


class VibeSubmissionSerializer(serializers.Serializer):
    crowd_level = serializers.ChoiceField(choices=CrowdLevel.choices)
    noise_level = serializers.ChoiceField(choices=NoiseLevel.choices)
    current_best_for = serializers.ChoiceField(choices=ActivityBucket.choices)
    vibe_preset_badge = serializers.ChoiceField(choices=VibePresetBadge.choices)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown_keys = set(data.keys()) - set(self.fields.keys())
            if unknown_keys:
                raise serializers.ValidationError(
                    {
                        'non_field_errors': [
                            f"Free-text or unsupported fields are not allowed: {sorted(unknown_keys)}"
                        ]
                    }
                )
        return super().to_internal_value(data)


class VenueVibeReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = VenueVibeReport
        fields = [
            'id',
            'venue',
            'crowd_level',
            'noise_level',
            'current_best_for',
            'vibe_preset_badge',
            'created_at',
            'expires_at',
        ]


class QRCheckinInputSerializer(serializers.Serializer):
    qr_secret_token = serializers.CharField(max_length=128)
    match_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    micro_table_id = serializers.UUIDField(required=False, allow_null=True)
    party_size = serializers.IntegerField(required=False, default=1, min_value=1, max_value=20)
    flash_perk_id = serializers.IntegerField(required=False, allow_null=True)
    vibe = VibeSubmissionSerializer(required=False)
