from rest_framework import serializers

from .models import (
    CustomUser,
    MatchSafetyFilter,
    ReportReason,
    VerifiedGenderGroup,
)
from .services import extract_community_domain


class CustomUserSerializer(serializers.ModelSerializer):
    """
    Serializer for CustomUser exposing only username/ephemeral identity and
    non-sensitive verification flags (zero public bio/profile picture).
    """

    class Meta:
        model = CustomUser
        fields = [
            'id',
            'username',
            'ephemeral_handle',
            'is_verified_human',
            'is_verified_adult_18_plus',
            'match_safety_filter',
            'e2ee_public_key',
        ]


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, style={'input_type': 'password'})
    password_confirm = serializers.CharField(write_only=True, required=True, style={'input_type': 'password'})

    class Meta:
        model = CustomUser
        fields = ('username', 'email', 'password', 'password_confirm', 'ephemeral_handle')
        extra_kwargs = {
            'email': {'required': False, 'allow_blank': True},
            'ephemeral_handle': {'required': False, 'allow_blank': True},
        }

    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({'password': "Password fields didn't match."})
        return attrs

    def create(self, validated_data):
        validated_data.pop('password_confirm')
        return CustomUser.objects.create_user(**validated_data)


class SafetyPreferencesSerializer(serializers.ModelSerializer):
    community_email = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = CustomUser
        fields = [
            'ephemeral_handle',
            'match_safety_filter',
            'e2ee_public_key',
            'verified_gender_group',
            'verified_community_domain',
            'community_email',
        ]
        extra_kwargs = {
            'ephemeral_handle': {'required': False},
            'match_safety_filter': {'required': False},
            'e2ee_public_key': {'required': False, 'allow_blank': True},
            'verified_gender_group': {'required': False},
            'verified_community_domain': {'required': False, 'allow_blank': True},
        }

    def validate_ephemeral_handle(self, value):
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("Ephemeral handle cannot be blank.")
        qs = CustomUser.objects.filter(ephemeral_handle=cleaned)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("This ephemeral handle is already in use.")
        return cleaned

    def update(self, instance, validated_data):
        community_email = validated_data.pop('community_email', None)
        if community_email is not None:
            validated_data['verified_community_domain'] = extract_community_domain(community_email)
        elif 'verified_community_domain' in validated_data:
            validated_data['verified_community_domain'] = extract_community_domain(
                validated_data['verified_community_domain']
            )
        return super().update(instance, validated_data)


class SafetyReportInputSerializer(serializers.Serializer):
    reported_user_id = serializers.IntegerField(required=False)
    reported_user = serializers.IntegerField(required=False)
    match_id = serializers.IntegerField(required=False)
    reason = serializers.ChoiceField(choices=ReportReason.choices)

    def validate(self, attrs):
        if (
            attrs.get('reported_user_id') is None
            and attrs.get('reported_user') is None
            and attrs.get('match_id') is None
        ):
            raise serializers.ValidationError(
                "One of 'reported_user_id', 'reported_user', or 'match_id' is required."
            )
        return attrs
