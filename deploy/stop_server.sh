#!/usr/bin/env bash
# Stop the PyMEOP data browser started by start_server.sh.
#
# Sends Ctrl-C so the server shuts down cleanly, and closes the screen session
# outright if it is still there after 5 s.

set -u

SESSION=pymeop-web

running() { screen -wipe > /dev/null; screen -list | grep -q "[0-9]\.$SESSION[[:space:]]"; }

if ! running; then
    echo "No screen session $SESSION running"
    exit 0
fi

screen -S "$SESSION" -X stuff $'\003'
for _ in 1 2 3 4 5; do
    sleep 1
    running || { echo "Stopped"; exit 0; }
done

screen -S "$SESSION" -X quit
sleep 1
if running; then
    echo "Could not stop screen session $SESSION" >&2
    exit 1
fi
echo "Stopped (session closed)"
