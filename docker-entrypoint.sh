#!/bin/bash
set -e

# Start a virtual framebuffer so Firefox can run with headless=False inside the
# container. True headless mode triggers additional bot-detection signals on both
# Immowelt and IS24, so we keep headless=False and give it a virtual screen instead.
Xvfb :99 -screen 0 1280x900x24 -nolisten tcp &
XVFB_PID=$!

# Wait until the X socket is available before handing off to the app.
until [ -e /tmp/.X11-unix/X99 ]; do sleep 0.05; done

export DISPLAY=:99

# Clean up Xvfb when the app exits.
trap "kill $XVFB_PID 2>/dev/null" EXIT

exec "$@"
