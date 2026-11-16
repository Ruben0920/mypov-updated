from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import models
from django.utils import timezone

from venues.models import ActivityBucket


class TimeWindow(models.TextChoices):
    RIGHT_NOW = 'RIGHT_NOW', 'Right Now (3 hours)'
    TONIGHT = 'TONIGHT', 'Tonight (until 02:00)'
    THIS_WEEKEND = 'THIS_WEEKEND', 'This Weekend (until Sunday 23:59)'


class VibePreference(models.TextChoices):
    CHILL_1ON1 = 'CHILL_1ON1', 'Chill 1-on-1'
    SMALL_GROUP_3_6 = 'SMALL_GROUP_3_6', 'Small Group (3–6)'
    EITHER = 'EITHER', 'Either 1-on-1 or Small Group'


BUCKET_MICRO_ACTIVITY_TAGS = {
    ActivityBucket.COFFEE_COWORK: [
        'deep_work_sprint',
        'exam_study',
        'specialty_coffee',
        'founder_chat',
        'quiet_wifi',
    ],
    ActivityBucket.DRINKS_NIGHTLIFE: [
        'craft_beer',
        'cocktails',
        'live_dj',
        'wine_bar',
        'rooftop_drinks',
    ],
    ActivityBucket.SPORTS_PADEL: [
        'padel_doubles',
        'gym_lift',
        '5k_run',
        'football_5v5',
        'kickboxing_sparring',
    ],
    ActivityBucket.FOOD_DINING: [
        'street_food',
        'casual_dining',
        'tacos',
        'seafood',
        'shared_meze',
    ],
    ActivityBucket.BOARD_GAMES_GEEK: [
        'board_games',
        'catan',
        'trivia_night',
        'retro_arcade',
        'chess_blitz',
    ],
    ActivityBucket.WALK_BEACH_SUNSET: [
        'sunset_walk',
        'beach_sunset',
        'sea_view',
        'morning_walk',
        'old_town_stroll',
    ],
}

DEFAULT_LOCAL_TIMEZONE = ZoneInfo('Asia/Nicosia')


def compute_expires_at(time_window: str, now: datetime | None = None) -> datetime:
    """
    Computes deterministic expiration timestamp from one of the 3 canonical time windows:
    - RIGHT_NOW: now + 3 hours
    - TONIGHT: next 02:00 local time
    - THIS_WEEKEND: upcoming Sunday 23:59:59 local time
    """
    now = now or timezone.now()
    if time_window == TimeWindow.RIGHT_NOW:
        return now + timedelta(hours=3)

    local_now = now.astimezone(DEFAULT_LOCAL_TIMEZONE)

    if time_window == TimeWindow.TONIGHT:
        target_date = local_now.date() if local_now.hour < 2 else local_now.date() + timedelta(days=1)
        local_target = datetime.combine(target_date, time(2, 0, 0), tzinfo=DEFAULT_LOCAL_TIMEZONE)
        return local_target.astimezone(timezone.get_default_timezone())

    if time_window == TimeWindow.THIS_WEEKEND:
        # weekday(): Monday=0 .. Sunday=6
        days_until_sunday = (6 - local_now.weekday()) % 7
        sunday_date = local_now.date() + timedelta(days=days_until_sunday)
        local_target = datetime.combine(sunday_date, time(23, 59, 59), tzinfo=DEFAULT_LOCAL_TIMEZONE)
        return local_target.astimezone(timezone.get_default_timezone())

    return now + timedelta(hours=3)
