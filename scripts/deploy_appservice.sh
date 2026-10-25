#!/usr/bin/env bash
# Deploy MatchDesk to Azure App Service (free F1 tier, Linux, source zip, built by Azure). No Docker needed.
# Used because ACR Tasks (cloud image builds) are blocked on Azure for Students.
set -euo pipefail
cd "$(dirname "$0")/.."
RG="${RG:-rg-matchdesk-ea}"; LOC="${LOC:-eastasia}"; PLAN="${PLAN:-matchdesk-plan}"
APP="${APP:-matchdesk-$(az account show --query id -o tsv | cut -c1-6)}"
git archive --format=zip -o /tmp/matchdesk.zip HEAD requirements.txt src web data/recorded
az group create -n "$RG" -l "$LOC" -o none
az appservice plan create -n "$PLAN" -g "$RG" -l "$LOC" --sku F1 --is-linux -o none
az webapp create -n "$APP" -g "$RG" -p "$PLAN" --runtime "PYTHON:3.12" -o none
az webapp config appsettings set -n "$APP" -g "$RG" -o none --settings \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true WEBSITES_PORT=8000 PYTHONPATH=/home/site/wwwroot/src
az webapp config set -n "$APP" -g "$RG" -o none --startup-file \
  "python -m uvicorn --factory matchdesk.feed:create_app_from_env --host 0.0.0.0 --port 8000"
az webapp deploy -n "$APP" -g "$RG" --src-path /tmp/matchdesk.zip --type zip
echo "https://$(az webapp show -n "$APP" -g "$RG" --query defaultHostName -o tsv)"
