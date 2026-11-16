import pusher
from django.conf import settings

def get_pusher_client():
    """Initializes and returns a Pusher client instance when configured."""
    if not getattr(settings, 'PUSHER_APP_ID', '') or not getattr(settings, 'PUSHER_KEY', ''):
        return None
    return pusher.Pusher(
        app_id=settings.PUSHER_APP_ID,
        key=settings.PUSHER_KEY,
        secret=settings.PUSHER_SECRET,
        cluster=settings.PUSHER_CLUSTER,
        ssl=settings.PUSHER_SSL
    )

def trigger_new_message(chat_id, message_data):
    """
    Triggers a 'new_message' event on a chat-specific channel.
    """
    pusher_client = get_pusher_client()
    if pusher_client is None:
        return
    channel_name = f'chat-{chat_id}' # Convention for chat channels
    try:
        pusher_client.trigger(channel_name, 'new_message', message_data)
    except Exception as e:
        print(f"Error triggering Pusher event for channel {channel_name}: {e}")

def trigger_chat_updated(user_id, chat_data):
    """
    Triggers a 'chat_updated' event on a user-specific channel
    when a chat they are part of is updated (e.g., new message, new chat created).
    """
    pusher_client = get_pusher_client()
    if pusher_client is None:
        return
    channel_name = f'user-{user_id}' # Convention for user-specific notification channels
    try:
        pusher_client.trigger(channel_name, 'chat_updated', chat_data)
    except Exception as e:
        print(f"Error triggering Pusher event for user channel {channel_name}: {e}")