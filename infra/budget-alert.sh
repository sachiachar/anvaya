#!/usr/bin/env bash
# A Google Cloud budget alert for the project, as a backstop to the service's daily ceiling
# (PR-15.1, agents/policies.yaml "service_ceiling"). The ceiling stops our key's calls each day;
# this budget emails the billing account's admins when the month's spend reaches 50%, 90% and
# 100% of the budget, or is forecast to pass it. It alerts only: it never stops anything.
#
# A person runs this once (it needs Billing Account Administrator or Costs Manager); running it
# again updates the amount.
# Usage: PROJECT_ID=my-project BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX [BUDGET_USD=300] ./infra/budget-alert.sh
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${BILLING_ACCOUNT:?Set BILLING_ACCOUNT (gcloud billing accounts list)}"
BUDGET_USD="${BUDGET_USD:-300}"   # the same figure as MONTHLY_BUDGET_USD for the admin portal
NAME="Anvaya demo (${PROJECT_ID})"

gcloud services enable billingbudgets.googleapis.com --project "$PROJECT_ID"

existing=$(gcloud billing budgets list --billing-account "$BILLING_ACCOUNT" \
  --filter "displayName=\"$NAME\"" --format 'value(name)')
if [ -n "$existing" ]; then
  echo "== Updating budget $existing to ${BUDGET_USD} USD"
  gcloud billing budgets update "$existing" --billing-account "$BILLING_ACCOUNT" --budget-amount "${BUDGET_USD}USD"
else
  echo "== Creating budget \"$NAME\" of ${BUDGET_USD} USD a month"
  gcloud billing budgets create --billing-account "$BILLING_ACCOUNT" --display-name "$NAME" \
    --budget-amount "${BUDGET_USD}USD" --calendar-period month \
    --filter-projects "projects/${PROJECT_ID}" \
    --threshold-rule percent=0.5 --threshold-rule percent=0.9 --threshold-rule percent=1.0 \
    --threshold-rule percent=1.0,basis=forecasted-spend
fi
echo "Done. Alerts go by email to the billing account's admins and users."
