from django.contrib.auth import authenticate, login as django_login
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from matches.models import Match

from .controllers import send_email_otp
from .models import CustomUser, VerifiedGenderGroup
from .serializers import SafetyPreferencesSerializer, SafetyReportInputSerializer
from .services import (
    compute_device_fingerprint_hmac,
    execute_one_tap_wipe,
    extract_community_domain,
    is_device_blocked,
    submit_safety_report,
)
from .verification import ImageValidationError, get_liveness_verifier


def _extract_raw_device_token(data) -> str:
    return (
        data.get('device_fingerprint')
        or data.get('device_token')
        or data.get('phone_fingerprint')
        or data.get('attestation_token')
        or ''
    )


class SignupView(APIView):
    def post(self, request, *args, **kwargs):
        username = (request.data.get('username') or '').strip()
        password = request.data.get('password')
        email = (request.data.get('email') or '').strip()
        ephemeral_handle = (request.data.get('ephemeral_handle') or '').strip()

        if not username or not password:
            return Response(
                {'error': 'Username and password are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_device = _extract_raw_device_token(request.data)
        fp_hmac = compute_device_fingerprint_hmac(raw_device) if raw_device else ''
        if fp_hmac:
            blocked, _ = is_device_blocked(fp_hmac)
            if blocked:
                return Response(
                    {'error': 'Device is blacklisted due to safety violations.'},
                    status=status.HTTP_403_FORBIDDEN,
                )

        if CustomUser.objects.filter(username=username).exists():
            return Response({'error': 'Username already exists'}, status=status.HTTP_400_BAD_REQUEST)

        if email and CustomUser.objects.filter(email=email).exists():
            return Response({'error': 'Email already exists'}, status=status.HTTP_400_BAD_REQUEST)

        if ephemeral_handle and CustomUser.objects.filter(ephemeral_handle=ephemeral_handle).exists():
            return Response({'error': 'Ephemeral handle already exists'}, status=status.HTTP_400_BAD_REQUEST)

        user_kwargs = {
            'username': username,
            'password': password,
            'email': email,
            'expires_at': timezone.now() + timezone.timedelta(hours=24),
            'device_fingerprint_hmac': fp_hmac,
        }
        if ephemeral_handle:
            user_kwargs['ephemeral_handle'] = ephemeral_handle

        community_email = request.data.get('community_email')
        if community_email:
            user_kwargs['verified_community_domain'] = extract_community_domain(community_email)

        user = CustomUser.objects.create_user(**user_kwargs)

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                'id': user.id,
                'username': user.username,
                'ephemeral_handle': user.ephemeral_handle,
                'is_verified_human': user.is_verified_human,
                'is_verified_adult_18_plus': user.is_verified_adult_18_plus,
                'match_safety_filter': user.match_safety_filter,
                'expires_at': user.expires_at.isoformat(),
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            },
            status=status.HTTP_200_OK,
        )


class LoginView(APIView):
    def post(self, request, *args, **kwargs):
        username = request.data.get('username')
        password = request.data.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            if user.expires_at and user.expires_at < timezone.now():
                return Response(
                    {'error': 'Temporary account has expired. Please create a new one.'},
                    status=status.HTTP_403_FORBIDDEN,
                )

            raw_device = _extract_raw_device_token(request.data)
            fp_hmac = (
                compute_device_fingerprint_hmac(raw_device)
                if raw_device
                else user.device_fingerprint_hmac
            )
            if fp_hmac:
                blocked, _ = is_device_blocked(fp_hmac)
                if blocked:
                    return Response(
                        {'error': 'Device is blacklisted due to safety violations.'},
                        status=status.HTTP_403_FORBIDDEN,
                    )

            django_login(request, user)
            refresh = RefreshToken.for_user(user)
            return Response(
                {
                    'id': user.id,
                    'username': user.username,
                    'ephemeral_handle': user.ephemeral_handle,
                    'is_verified_human': user.is_verified_human,
                    'is_verified_adult_18_plus': user.is_verified_adult_18_plus,
                    'expires_at': user.expires_at.isoformat() if user.expires_at else None,
                    'refresh': str(refresh),
                    'access': str(refresh.access_token),
                },
                status=status.HTTP_200_OK,
            )
        return Response({'error': 'Invalid credentials'}, status=status.HTTP_400_BAD_REQUEST)


class ForgotPasswordView(APIView):
    def post(self, request, *args, **kwargs):
        email = request.data.get('email', None)

        if email is None:
            return Response({'error': 'No email or password provided'}, status=status.HTTP_400_BAD_REQUEST)

        user = CustomUser.objects.filter(email=email).first()
        if not user:
            return Response({'error': 'User with this email does not exist.'}, status=status.HTTP_404_NOT_FOUND)
        if user.expires_at and user.expires_at < timezone.now():
            return Response({'error': 'Temporary account has expired.'}, status=status.HTTP_403_FORBIDDEN)

        send_email_otp(email)
        return Response({'success': 'Email sent'}, status=status.HTTP_200_OK)


class ZKLivenessVerifyView(APIView):
    """
    Zero-knowledge liveness & 18+ verification.
    Selfie frames are processed strictly in RAM and discarded immediately; zero disk writes.
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def post(self, request, *args, **kwargs):
        user = request.user
        raw_device = _extract_raw_device_token(request.data)
        attestation_token = (
            request.data.get('attestation_token')
            or request.data.get('device_token')
            or request.data.get('device_fingerprint')
            or ''
        )

        fp_hmac = (
            compute_device_fingerprint_hmac(raw_device)
            if raw_device
            else user.device_fingerprint_hmac
        )
        if not fp_hmac:
            return Response(
                {'error': 'A device or attestation token is required for verification.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        blocked, _ = is_device_blocked(fp_hmac)
        if blocked:
            return Response(
                {'error': 'Device is blacklisted due to safety violations.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        uploaded_file = (
            request.FILES.get('image')
            or request.FILES.get('selfie')
            or request.FILES.get('file')
        )
        if uploaded_file is None:
            return Response(
                {'error': 'An in-memory selfie image file is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        image_bytes = None
        try:
            image_bytes = uploaded_file.read()
            verifier = get_liveness_verifier()
            result = verifier.verify(
                image_bytes=image_bytes,
                attestation_token=attestation_token or raw_device,
                user_id=user.pk,
                fingerprint_hmac=fp_hmac,
            )
        except ImageValidationError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        finally:
            try:
                uploaded_file.close()
            except Exception:
                pass
            if image_bytes is not None:
                del image_bytes

        update_fields = [
            'is_verified_human',
            'is_verified_adult_18_plus',
            'device_fingerprint_hmac',
            'verification_proof_token',
        ]
        user.is_verified_human = result.is_verified_human
        user.is_verified_adult_18_plus = result.is_verified_adult_18_plus
        user.device_fingerprint_hmac = fp_hmac
        user.verification_proof_token = result.verification_proof_token

        verified_gender_group = request.data.get('verified_gender_group')
        if verified_gender_group:
            if verified_gender_group not in VerifiedGenderGroup.values:
                return Response(
                    {'error': 'Invalid verified_gender_group choice.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            user.verified_gender_group = verified_gender_group
            update_fields.append('verified_gender_group')

        community_email = request.data.get('community_email')
        if community_email:
            user.verified_community_domain = extract_community_domain(community_email)
            update_fields.append('verified_community_domain')

        user.save(update_fields=update_fields)

        return Response(
            {
                'is_verified_human': user.is_verified_human,
                'is_verified_adult_18_plus': user.is_verified_adult_18_plus,
                'verified_gender_group': user.verified_gender_group,
                'verified_community_domain': user.verified_community_domain,
                'verification_proof_token': user.verification_proof_token,
                'ephemeral_handle': user.ephemeral_handle,
            },
            status=status.HTTP_200_OK,
        )


class SafetyPreferencesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        serializer = SafetyPreferencesSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, *args, **kwargs):
        serializer = SafetyPreferencesSerializer(
            request.user,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)


class SafetyReportView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = SafetyReportInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        target_user = None
        match_id = data.get('match_id')
        reported_user_id = data.get('reported_user_id') or data.get('reported_user')

        if match_id is not None:
            match = Match.objects.filter(pk=match_id).select_related('user1', 'user2').first()
            if not match:
                return Response({'error': 'Match not found.'}, status=status.HTTP_404_NOT_FOUND)
            if request.user.pk == match.user1_id:
                target_user = match.user2
            elif request.user.pk == match.user2_id:
                target_user = match.user1
            else:
                return Response(
                    {'error': 'You are not a participant in this match.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            target_user = CustomUser.objects.filter(pk=reported_user_id).first()
            if not target_user:
                return Response({'error': 'Reported user not found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            report, ledger = submit_safety_report(
                reporter=request.user,
                reported_user=target_user,
                reason=data['reason'],
            )
        except DjangoValidationError as exc:
            return Response({'error': exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                'report_id': report.id,
                'reason': report.reason,
                'strike_count': ledger.strike_count if ledger else None,
                'is_blacklisted': ledger.is_blacklisted if ledger else False,
                'pair_blocked': True,
            },
            status=status.HTTP_201_CREATED,
        )


class OneTapWipeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        ledger = execute_one_tap_wipe(user=request.user)
        return Response(
            {
                'status': 'wiped',
                'wipe_count_30d': ledger.wipe_count_30d if ledger else 0,
            },
            status=status.HTTP_200_OK,
        )
