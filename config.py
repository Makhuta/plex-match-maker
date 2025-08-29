
import os, pytz

tz_name = os.getenv("TZ", "UTC")

try:
    TZ = pytz.timezone(tz_name)
except pytz.UnknownTimeZoneError:
    TZ = pytz.UTC