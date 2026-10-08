#!/bin/bash
# Deploy Twelve to PythonAnywhere
# Usage: bash deploy.sh              → deploy all files
#        bash deploy.sh app.py db.py → deploy specific files

TOKEN="${PA_API_TOKEN:?Définis PA_API_TOKEN (clé API PythonAnywhere) avant de lancer}"
USER="FOOxS"
BASE="https://www.pythonanywhere.com/api/v0/user/$USER"
REMOTE="/home/$USER/twelve"

# All deployable files: "local_path|remote_path" pairs
ALL_FILES=(
  "app.py|$REMOTE/app.py"
  "db.py|$REMOTE/db.py"
  "notify_hourly.py|$REMOTE/notify_hourly.py"
  "templates/home.html|$REMOTE/templates/home.html"
  "templates/challenge.html|$REMOTE/templates/challenge.html"
  "templates/slot.html|$REMOTE/templates/slot.html"
  "templates/summary.html|$REMOTE/templates/summary.html"
  "templates/error.html|$REMOTE/templates/error.html"
)

# Build list of files to deploy
if [ $# -gt 0 ]; then
  DEPLOY=()
  for arg in "$@"; do
    found=0
    for pair in "${ALL_FILES[@]}"; do
      local="${pair%%|*}"
      if [ "$local" = "$arg" ]; then
        DEPLOY+=("$pair")
        found=1
        break
      fi
    done
    if [ $found -eq 0 ]; then
      echo "Unknown file: $arg"
      echo "Available files:"
      for pair in "${ALL_FILES[@]}"; do echo "  ${pair%%|*}"; done
      exit 1
    fi
  done
else
  DEPLOY=("${ALL_FILES[@]}")
fi

# Upload each file
ERRORS=0
for pair in "${DEPLOY[@]}"; do
  local_path="${pair%%|*}"
  remote_path="${pair##*|}"
  if [ ! -f "$local_path" ]; then
    echo "✗ $local_path — file not found"
    ERRORS=$((ERRORS + 1))
    continue
  fi
  status=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "$BASE/files/path$remote_path" \
    -H "Authorization: Token $TOKEN" \
    -F "content=@$local_path")
  if [ "$status" = "200" ] || [ "$status" = "201" ]; then
    echo "✓ $local_path [$status]"
  else
    echo "✗ $local_path [$status]"
    ERRORS=$((ERRORS + 1))
  fi
  sleep 2
done

if [ $ERRORS -gt 0 ]; then
  echo ""
  echo "⚠️  $ERRORS upload(s) failed — skipping reload"
  exit 1
fi

# Reload webapp
echo ""
echo "Reloading webapp..."
status=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "$BASE/webapps/fooxs.pythonanywhere.com/reload/" \
  -H "Authorization: Token $TOKEN")
if [ "$status" = "200" ]; then
  echo "✓ Reloaded — https://fooxs.pythonanywhere.com"
else
  echo "✗ Reload failed [$status]"
  exit 1
fi
