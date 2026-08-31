#!/bin/sh
set -eu
alembic upgrade head
python -m visionpass.seed
exec "$@"
