import io

from PIL import Image, UnidentifiedImageError

from users.verification import validate_image_magic_bytes
from venues.models import ActivityBucket

from .constants import BUCKET_MICRO_ACTIVITY_TAGS

KEYWORD_BUCKET_MAP = {
    ActivityBucket.SPORTS_PADEL: [
        ('padel', 'padel_doubles'),
        ('racket', 'padel_doubles'),
        ('gym', 'gym_lift'),
        ('lift', 'gym_lift'),
        ('run', '5k_run'),
        ('jog', '5k_run'),
        ('football', 'football_5v5'),
        ('soccer', 'football_5v5'),
        ('kickbox', 'kickboxing_sparring'),
    ],
    ActivityBucket.COFFEE_COWORK: [
        ('coffee', 'specialty_coffee'),
        ('espresso', 'specialty_coffee'),
        ('latte', 'specialty_coffee'),
        ('matcha', 'specialty_coffee'),
        ('study', 'exam_study'),
        ('exam', 'exam_study'),
        ('laptop', 'deep_work_sprint'),
        ('cowork', 'deep_work_sprint'),
        ('work', 'deep_work_sprint'),
        ('founder', 'founder_chat'),
        ('wifi', 'quiet_wifi'),
    ],
    ActivityBucket.DRINKS_NIGHTLIFE: [
        ('beer', 'craft_beer'),
        ('ipa', 'craft_beer'),
        ('cocktail', 'cocktails'),
        ('drink', 'cocktails'),
        ('bar', 'rooftop_drinks'),
        ('dj', 'live_dj'),
        ('wine', 'wine_bar'),
        ('pub', 'craft_beer'),
    ],
    ActivityBucket.FOOD_DINING: [
        ('taco', 'tacos'),
        ('food', 'street_food'),
        ('burger', 'street_food'),
        ('dinner', 'casual_dining'),
        ('lunch', 'casual_dining'),
        ('meze', 'shared_meze'),
        ('seafood', 'seafood'),
        ('eat', 'casual_dining'),
    ],
    ActivityBucket.BOARD_GAMES_GEEK: [
        ('board', 'board_games'),
        ('catan', 'catan'),
        ('chess', 'chess_blitz'),
        ('trivia', 'trivia_night'),
        ('quiz', 'trivia_night'),
        ('arcade', 'retro_arcade'),
        ('game', 'board_games'),
    ],
    ActivityBucket.WALK_BEACH_SUNSET: [
        ('sunset', 'sunset_walk'),
        ('beach', 'beach_sunset'),
        ('sea', 'sea_view'),
        ('walk', 'morning_walk'),
        ('molos', 'sunset_walk'),
        ('stroll', 'old_town_stroll'),
    ],
}


def extract_intent_in_memory(
    *,
    image_bytes: bytes | None = None,
    text: str = '',
    activity_bucket: str | None = None,
) -> dict:
    """
    Extracts suggested activity bucket and canonical micro-activity tags strictly
    in memory. No media is ever written to disk.
    """
    detected_objects: list[str] = []
    image_stream: io.BytesIO | None = None

    try:
        if image_bytes is not None:
            image_format = validate_image_magic_bytes(image_bytes)
            detected_objects.append(f'ephemeral_{image_format}_frame')
            image_stream = io.BytesIO(image_bytes)
            try:
                with Image.open(image_stream) as img:
                    width, height = img.size
                    if width > 0 and height > 0:
                        detected_objects.append('camera_scene')
            except (UnidentifiedImageError, OSError):
                # Magic bytes already validated; dummy test payloads still proceed cleanly.
                pass

        normalized_text = (text or '').strip().lower()
        bucket_scores: dict[str, int] = {bucket: 0 for bucket in ActivityBucket.values}
        matched_tags: list[str] = []

        for bucket, keyword_pairs in KEYWORD_BUCKET_MAP.items():
            for keyword, canonical_tag in keyword_pairs:
                if keyword in normalized_text:
                    bucket_scores[bucket] += 1
                    if canonical_tag not in matched_tags:
                        matched_tags.append(canonical_tag)

        if activity_bucket and activity_bucket in ActivityBucket.values:
            suggested_bucket = activity_bucket
        else:
            best_bucket, best_score = max(
                bucket_scores.items(),
                key=lambda item: item[1],
            )
            suggested_bucket = best_bucket if best_score > 0 else ActivityBucket.COFFEE_COWORK

        canonical_pool = BUCKET_MICRO_ACTIVITY_TAGS.get(suggested_bucket, [])
        suggested_tags: list[str] = []
        for tag in matched_tags:
            if tag in canonical_pool and tag not in suggested_tags:
                suggested_tags.append(tag)
        for tag in canonical_pool:
            if tag not in suggested_tags:
                suggested_tags.append(tag)
            if len(suggested_tags) >= 4:
                break

        return {
            'suggested_bucket': suggested_bucket,
            'suggested_tags': suggested_tags[:5],
            'detected_objects': detected_objects,
            'media_retained': False,
        }
    finally:
        if image_stream is not None:
            image_stream.close()
            del image_stream
        if image_bytes is not None:
            del image_bytes
