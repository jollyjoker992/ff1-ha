"""Client of the Feral File FF1's LAN interface: what the device lets anyone on its own network read and do."""

from .client import (CONTROL, READ, Busy, Ff1Client, Ff1Error, LanClosed, Locked, NotAllowed, NotLocal, Rejected,
                     Unreachable)
from .models import (Cache, Device, MediaHealth, Metrics, Panel, Player, SleepSchedule, Status, Work, parse_prometheus)

__version__ = "0.3.0"

__all__ = ["CONTROL", "READ", "Busy", "Cache", "Device", "Ff1Client", "Ff1Error", "LanClosed", "Locked", "MediaHealth",
           "Metrics", "NotAllowed", "NotLocal", "Panel", "Player", "Rejected", "SleepSchedule", "Status", "Unreachable",
           "Work", "parse_prometheus", "__version__"]
