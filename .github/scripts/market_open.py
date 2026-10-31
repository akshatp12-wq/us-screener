"""Exit 0 if US equity markets are open now (with a 20-minute tail after the close), else 1."""
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

now = datetime.now(ZoneInfo("America/New_York"))
is_open = now.weekday() < 5 and time(9, 30) <= now.time() <= time(16, 20)
print(f"New York time {now:%a %H:%M} -> market {'open' if is_open else 'closed'}")
sys.exit(0 if is_open else 1)
