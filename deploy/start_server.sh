#!/usr/bin/env bash
# Start the PyMEOP data browser in a detached screen session, for a server.
#
#   start_server.sh [DATA_DIR [PORT]]
#
# DATA_DIR is the folder mirror_data.sh copies into. Either can be left off the
# command line to use DEFAULT_DATA_DIR and DEFAULT_PORT below, so set those once
# for the server and start it with no arguments. The
# server listens on every interface, so keep the firewall limited to the lab
# subnet (see webapp/README.md). If it stops on an error it is started again
# after 5 s. Look in on it with `screen -r pymeop-web` (detach again with
# Ctrl-a d); its output is also kept in log/webapp.screen.log. Stop it with
# stop_server.sh.

set -u

# used when not given on the command line; leave DEFAULT_DATA_DIR empty to
# always require it
DEFAULT_DATA_DIR=""
DEFAULT_PORT=8008

SESSION=pymeop-web

data_dir=${1:-$DEFAULT_DATA_DIR}
port=${2:-$DEFAULT_PORT}
if [ -z "$data_dir" ]; then
    echo "usage: $(basename "$0") [DATA_DIR [PORT]]  (or set DEFAULT_DATA_DIR in the script)" >&2
    exit 2
fi
data_dir=$(realpath "$data_dir")

if [ ! -d "$data_dir" ]; then
    echo "No such folder: $data_dir" >&2
    exit 2
fi

screen -wipe > /dev/null    # clear any session left dead by a crash or reboot
if screen -list | grep -q "[0-9]\.$SESSION[[:space:]]"; then
    echo "Already running in screen session $SESSION; stop it first with stop_server.sh" >&2
    exit 1
fi

root=$(cd "$(dirname "$0")/.." && pwd)
python="$root/.venv/bin/python"
[ -x "$python" ] || python=python3
mkdir -p "$root/log"

# restart if it stops on an error or is killed; Ctrl-C stops it cleanly with exit 0
cmd="cd '$root' && while true; do
    '$python' webapp/server.py --host 0.0.0.0 --port $port --no-browser --data-dir '$data_dir'
    rc=\$?
    [ \$rc -eq 0 ] && break
    echo \"\$(date '+%F %T') server exited with \$rc, restarting in 5 s\"
    sleep 5
done"

screen -dmS "$SESSION" -L -Logfile "$root/log/webapp.screen.log" bash -c "$cmd"
sleep 1
if screen -list | grep -q "[0-9]\.$SESSION[[:space:]]"; then
    echo "Started in screen session $SESSION on port $port, serving $data_dir"
else
    echo "Failed to start; see $root/log/webapp.screen.log" >&2
    exit 1
fi
