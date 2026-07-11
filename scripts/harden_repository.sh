#!/usr/bin/env bash
set -euo pipefail

gh auth status --hostname github.com >/dev/null
repo="${1:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
visibility="$(gh repo view "$repo" --json visibility --jq .visibility)"
[[ "$visibility" == PUBLIC ]] || { echo "Refusing to harden a non-public builder repository" >&2; exit 1; }
api='2026-03-10'
base="repos/$repo"

jq -n '{enabled:true,allowed_actions:"selected",sha_pinning_required:true}' | gh api --method PUT -H "X-GitHub-Api-Version: $api" "$base/actions/permissions" --input -
jq -n '{github_owned_allowed:true,verified_allowed:false,patterns_allowed:[]}' | gh api --method PUT -H "X-GitHub-Api-Version: $api" "$base/actions/permissions/selected-actions" --input -
jq -n '{default_workflow_permissions:"read",can_approve_pull_request_reviews:false}' | gh api --method PUT -H "X-GitHub-Api-Version: $api" "$base/actions/permissions/workflow" --input -
jq -n '{days:7}' | gh api --method PUT -H "X-GitHub-Api-Version: $api" "$base/actions/permissions/artifact-and-log-retention" --input -
jq -n '{access_level:"none"}' | gh api --method PUT -H "X-GitHub-Api-Version: $api" "$base/actions/permissions/access" --input -

gh api -H "X-GitHub-Api-Version: $api" "$base/actions/permissions"
gh api -H "X-GitHub-Api-Version: $api" "$base/actions/permissions/selected-actions"
gh api -H "X-GitHub-Api-Version: $api" "$base/actions/permissions/workflow"
gh api -H "X-GitHub-Api-Version: $api" "$base/actions/permissions/artifact-and-log-retention"
gh api -H "X-GitHub-Api-Version: $api" "$base/actions/permissions/access"
