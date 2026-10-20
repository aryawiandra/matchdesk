#!/usr/bin/env bash
# Deploy MatchDesk to Azure Container Apps (cloud build, no local Docker needed).
# Prerequisite, once: az login; az provider register for Microsoft.App, ContainerRegistry, OperationalInsights.
set -euo pipefail
RG="${RG:-rg-matchdesk}"; LOC="${LOC:-southeastasia}"; ENVN="${ENVN:-matchdesk-env}"; APP="${APP:-matchdesk}"
az group create -n "$RG" -l "$LOC" -o none
az containerapp up -n "$APP" -g "$RG" -l "$LOC" --environment "$ENVN" --source . \
  --ingress external --target-port 8000
az containerapp update -n "$APP" -g "$RG" --min-replicas 0 --max-replicas 1 -o none
az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv
