'''PyMEOP data files, J.Maxwell

Event files and slow controls logs are both written the same way: a file named
current_<start>.txt while it is open, renamed to <start>__<end>.txt once closed,
with times in UTC.
'''

import datetime
import os
import time

STAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"


def open_current(directory):
    '''Open a new current_<start>.txt file in directory.

    Returns:
        (file, path, start) with start the formatted UTC start time
    '''

    os.makedirs(directory, exist_ok=True)
    start = datetime.datetime.now(tz=datetime.timezone.utc).strftime(STAMP_FORMAT)
    path = os.path.join(directory, f'current_{start}.txt')
    return open(path, "w"), path, start


def close_current(f, path, start, directory):
    '''Close a current file and rename it with its start and end times.

    Returns:
        the new file name
    '''

    f.close()
    end = datetime.datetime.now(tz=datetime.timezone.utc).strftime(STAMP_FORMAT)
    new = f'{start}__{end}.txt'
    for attempt in range(10):
        try:
            os.rename(path, os.path.join(directory, new))
            break
        except PermissionError:  # Windows refuses while the data browser is reading the file
            if attempt == 9:
                raise
            time.sleep(0.05)
    return new
