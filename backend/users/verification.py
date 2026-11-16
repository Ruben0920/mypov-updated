from dataclasses import dataclass
import hashlib
import hmac
from typing import Protocol

from django.conf import settings
from django.utils import timezone
from django.utils.module_loading import import_string


class ImageValidationError(ValueError):
    """Raised when uploaded selfie bytes fail in-memory magic-byte validation."""


@dataclass(frozen=True)
class LivenessVerificationResult:
    is_verified_human: bool
    is_verified_adult_18_plus: bool
    verification_proof_token: str
    image_format: str


def validate_image_magic_bytes(image_bytes: bytes) -> str:
    """
    Validates common image formats strictly from raw in-memory bytes without touching disk.
    """
    if not image_bytes or len(image_bytes) < 8:
        raise ImageValidationError("Image payload is empty or too small.")

    if image_bytes.startswith(b'\xff\xd8\xff'):
        return 'jpeg'
    if image_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if len(image_bytes) >= 12 and image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
        return 'webp'
    if image_bytes.startswith((b'GIF87a', b'GIF89a')):
        return 'gif'

    raise ImageValidationError("Unsupported image format: invalid magic bytes.")


class LivenessVerifier(Protocol):
    def verify(
        self,
        *,
        image_bytes: bytes,
        attestation_token: str,
        user_id: int,
        fingerprint_hmac: str,
    ) -> LivenessVerificationResult:
        ...


class DeterministicLocalLivenessVerifier:
    """
    Deterministic in-memory liveness/adult verifier for local development and tests.
    Validates image magic bytes and attestation token presence without disk writes.
    """

    def verify(
        self,
        *,
        image_bytes: bytes,
        attestation_token: str,
        user_id: int,
        fingerprint_hmac: str,
    ) -> LivenessVerificationResult:
        image_format = validate_image_magic_bytes(image_bytes)
        if not attestation_token or not attestation_token.strip():
            raise ImageValidationError("Signed attestation/device token is required.")

        frame_digest = hashlib.sha256(image_bytes).hexdigest()
        pepper = getattr(settings, 'DEVICE_HASH_PEPPER', 'dev-pepper')
        proof_material = (
            f"{user_id}:{fingerprint_hmac}:{frame_digest}:{timezone.now().isoformat()}"
        ).encode('utf-8')
        proof_token = hmac.new(
            pepper.encode('utf-8') if isinstance(pepper, str) else pepper,
            proof_material,
            hashlib.sha256,
        ).hexdigest()

        del frame_digest
        del proof_material

        return LivenessVerificationResult(
            is_verified_human=True,
            is_verified_adult_18_plus=True,
            verification_proof_token=proof_token,
            image_format=image_format,
        )


def get_liveness_verifier() -> LivenessVerifier:
    verifier_path = getattr(
        settings,
        'ZK_LIVENESS_VERIFIER',
        'users.verification.DeterministicLocalLivenessVerifier',
    )
    verifier_cls = import_string(verifier_path)
    return verifier_cls()
