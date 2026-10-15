#!/usr/bin/env sh
# Starts JobPing on your own computer with fake data. Nothing is sent to anyone.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
python -m pip install --quiet -r requirements.txt
export DATABASE_PATH="$PWD/demo.db"
python -m flask --app wsgi seed-demo
echo "Open http://localhost:5000/login and use the email and password shown above."
python -m flask --app wsgi run
