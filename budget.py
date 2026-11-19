"""Run-time budget so long backfills stop in time to save their progress."""
import time

import config

_START = time.time()


def minutes_used():
    return (time.time() - _START) / 60


def out_of_time(reserve_min=0):
    """True once the run has used its budget (minus a reserve for later steps)."""
    return bool(config.TIME_BUDGET_MIN) and minutes_used() > config.TIME_BUDGET_MIN - reserve_min
