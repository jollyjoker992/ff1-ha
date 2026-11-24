from datetime import timedelta

DOMAIN = "ff1"
CONF_HOST = "host"
CONF_PORT = "port"
DEFAULT_PORT = 1111
UPDATE_EVERY = timedelta(seconds=60)      # the socket brings changes at once; the poll is the safety net
SLOW_EVERY = timedelta(minutes=10)        # the panel over DDC, the cache, the works' health, /metrics
RECONNECT_MIN, RECONNECT_MAX = 5, 120     # seconds between tries of the notification socket
