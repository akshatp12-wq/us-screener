#!/usr/bin/env bash
# Open (or comment on) a GitHub issue when a scheduled pipeline run fails.
set -euo pipefail
TITLE="Pipeline failure: ${WORKFLOW_NAME}"
URL="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
gh label create pipeline-failure --color d03b3b --description "Automated: a scheduled pipeline run failed" 2>/dev/null || true
EXISTING=$(gh issue list --label pipeline-failure --state open --search "in:title \"${TITLE}\"" --json number --jq '.[0].number' || true)
BODY="The **${WORKFLOW_NAME}** run failed at $(date -u +%Y-%m-%dT%H:%MZ).

Run log: ${URL}

Stage errors are also listed on the dashboard's Data health page (docs/data/manifest.json). This issue stays open until someone closes it; later failures add comments."
if [ -n "${EXISTING}" ]; then
  gh issue comment "${EXISTING}" --body "Failed again: ${URL}"
else
  gh issue create --title "${TITLE}" --label pipeline-failure --body "${BODY}"
fi
