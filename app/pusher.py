import os
import pusher


def get_pusher_client():
    app_id = os.getenv('PUSHER_APP_ID')
    key = os.getenv('PUSHER_KEY')
    secret = os.getenv('PUSHER_SECRET')
    cluster = os.getenv('PUSHER_CLUSTER', 'ap1')

    if not app_id or not key or not secret:
        return None

    return pusher.Pusher(
        app_id=app_id,
        key=key,
        secret=secret,
        cluster=cluster,
        ssl=True
    )


def pusher_trigger(channel, event, data):
    client = get_pusher_client()
    if client:
        client.trigger(channel, event, data)
