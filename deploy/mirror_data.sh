#!/usr/bin/env bash
# Mirror the PyMEOP event files to one or more places with rsync, for cron.
#
#   mirror_data.sh SOURCE_DIR DEST [DEST ...]
#
# Each DEST is anything rsync takes: user@host:/path over ssh, or a local or
# mounted path. Destinations are tried independently, so one being down does
# not hold up the others. Example crontab line, every minute:
#
#   * * * * * /home/daq/PyMEOP/deploy/mirror_data.sh /home/daq/PyMEOP/data epics:/srv/pymeop/data group:/group/xxx/pymeop/data >> /home/daq/PyMEOP/log/mirror.log 2>&1
#
# Files are never deleted at a destination, so it stays an archive even if the
# DAQ's copy is cleared, with one exception: the DAQ writes a run to
# current_<start>.txt and renames it <start>__<stop>.txt when it closes, so a
# current_ file that has gone from the source is removed from the destinations
# too, or the closed run would show up twice in the browser.
#
# rsync writes each file under a temporary name and renames it into place, so
# the browser never reads a half copied file. Prints nothing unless something
# fails, so the log only grows on trouble.

set -u

if [ $# -lt 2 ]; then
    echo "usage: $(basename "$0") SOURCE_DIR DEST [DEST ...]" >&2
    exit 2
fi

src="${1%/}/"    # trailing slash: copy the folder's contents, not the folder
shift

if [ ! -d "$src" ]; then
    echo "$(date '+%F %T') no source folder $src" >&2
    exit 2
fi

# cron starts a new copy every minute; if the last one is still going (a slow
# or hung destination), let it finish rather than piling up
exec 9> "${TMPDIR:-/tmp}/pymeop_mirror.lock"
flock -n 9 || exit 0

# never stop to ask for a password or host key, and give up on a dead host quickly
ssh_cmd="ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=15"

status=0
for dest in "$@"; do
    rsync --recursive --times --timeout=120 -e "$ssh_cmd" \
          --delete --filter='R current_*' --filter='P *' \
          "$src" "${dest%/}/"
    rc=$?
    # 24: a file vanished while it was being sent, the DAQ renaming a run it
    # just closed; the next pass picks up the new name
    if [ $rc -ne 0 ] && [ $rc -ne 24 ]; then
        echo "$(date '+%F %T') rsync to $dest failed, exit $rc" >&2
        status=$rc
    fi
done
exit $status
