# Post-deploy regression runs from CI/CD

Ready-to-paste recipes for triggering a TraceIQ suite after a deploy and
failing the pipeline when the release gate fails. All of them do the same
three calls:

1. **Start** — `POST /api/runs?suite_id=N` with `X-API-Key` and a JSON body
   carrying the change context. The response is one run object, or a list of
   them (one per browser/device, or per case in a SEPARATE-mode suite).

   ```json
   {"git_commit": "<sha>", "git_branch": "main", "triggered_by": "ci",
    "agent_id": "argocd", "region": "eu"}
   ```

   `region` is optional — omit it unless you run region-pinned workers
   (docs/OPERATIONS.md → Regions). `triggered_by: "ci"` matters: CI runs are
   the ones allowed to page on-call when a project has an `on_run_failure`
   paging channel; a human or agent run never pages.
2. **Wait** — poll `GET /api/runs/{id}` until `status` is `passed`, `failed`
   or `error` (`pending`/`running` mean keep waiting).
3. **Gate** — `GET /api/projects/{project_id}/quality-gate?git_commit=<sha>`
   evaluates every finished run for that commit against the project's gate
   policy and returns `{"passed": bool, "checks": [...], "evaluated_run_ids":
   [...]}`. Fail the job when `passed` is false.

## Credentials

- Mint a key at `POST /api/workspaces/{id}/api-keys` (or the API Keys page).
  Scope it to the project (`project_id`) — a project-scoped key can only run
  and read that project.
- Restrict where it works with `allowed_cidrs` (your CI runners' egress
  ranges, e.g. `["203.0.113.0/24"]`). A leaked key then fails with 403 from
  anywhere else, and the attempt is audited. Behind a reverse proxy the backend
  needs `TRUSTED_PROXY_CIDRS` to see the runner's real address — see
  docs/OPERATIONS.md → IP allowlists.
- Store the key in the CI system's secret store, never in the pipeline file.

## The shared script

Every recipe below runs this (needs `curl` and `jq`). Save it as
`traceiq-gate.sh` in the repo or inline it.

```bash
#!/usr/bin/env bash
# TRACEIQ_URL, TRACEIQ_API_KEY, SUITE_ID, PROJECT_ID, GIT_SHA required;
# GIT_BRANCH, REGION, AGENT_ID, TIMEOUT_SECONDS (default 1800) optional.
set -euo pipefail
API="${TRACEIQ_URL%/}/api"
H=(-H "X-API-Key: ${TRACEIQ_API_KEY}" -H "Content-Type: application/json")

body=$(jq -n --arg c "$GIT_SHA" --arg b "${GIT_BRANCH:-}" --arg r "${REGION:-}" \
             --arg a "${AGENT_ID:-ci}" \
  '{git_commit:$c, triggered_by:"ci", agent_id:$a}
   + (if $b != "" then {git_branch:$b} else {} end)
   + (if $r != "" then {region:$r} else {} end)')
runs=$(curl -fsS -X POST "${API}/runs?suite_id=${SUITE_ID}" "${H[@]}" -d "$body" \
  | jq -r 'if type == "array" then .[].id else .id end')
echo "Started run(s): $runs"

deadline=$(( $(date +%s) + ${TIMEOUT_SECONDS:-1800} ))
for id in $runs; do
  while :; do
    status=$(curl -fsS "${API}/runs/${id}" "${H[@]}" | jq -r .status)
    case "$status" in passed|failed|error) echo "run $id: $status"; break ;; esac
    [ "$(date +%s)" -lt "$deadline" ] || { echo "timed out waiting for run $id"; exit 1; }
    sleep 10
  done
done

gate=$(curl -fsS "${API}/projects/${PROJECT_ID}/quality-gate?git_commit=${GIT_SHA}" "${H[@]}")
echo "$gate" | jq -r '.checks[] | "\(if .passed then "PASS" else "FAIL" end)  \(.name): \(.detail // "")"'
echo "$gate" | jq -e '.passed' >/dev/null || { echo "TraceIQ quality gate FAILED"; exit 1; }
echo "TraceIQ quality gate passed"
```

## Argo CD — PostSync hook

Runs once Argo CD has synced the application; a failing gate marks the sync
failed. Put the Job next to your manifests.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: traceiq-ci
type: Opaque
stringData:
  TRACEIQ_API_KEY: tiq_...           # better: an ExternalSecret / SealedSecret
---
apiVersion: batch/v1
kind: Job
metadata:
  generateName: traceiq-postsync-
  annotations:
    argocd.argoproj.io/hook: PostSync
    argocd.argoproj.io/hook-delete-policy: BeforeHookCreation
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 2400
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: traceiq
          image: alpine:3.20
          envFrom:
            - secretRef: {name: traceiq-ci}
          env:
            - {name: TRACEIQ_URL, value: "https://traceiq.example.com"}
            - {name: SUITE_ID, value: "12"}
            - {name: PROJECT_ID, value: "3"}
            - {name: AGENT_ID, value: "argocd"}
            # The deployed revision — template it from your app (Helm value,
            # Kustomize image tag) so the gate evaluates exactly this commit.
            - {name: GIT_SHA, value: "REPLACE_WITH_DEPLOYED_SHA"}
          command: ["/bin/sh", "-c"]
          args:
            - apk add --no-cache bash curl jq >/dev/null && bash /scripts/traceiq-gate.sh
          volumeMounts:
            - {name: scripts, mountPath: /scripts}
      volumes:
        - name: scripts
          configMap: {name: traceiq-gate-script}   # holds traceiq-gate.sh
```

## Jenkins — declarative pipeline

```groovy
pipeline {
  agent any
  environment {
    TRACEIQ_URL     = 'https://traceiq.example.com'
    TRACEIQ_API_KEY = credentials('traceiq-api-key')   // Secret text credential
    SUITE_ID        = '12'
    PROJECT_ID      = '3'
    AGENT_ID        = 'jenkins'
  }
  stages {
    stage('Deploy') { steps { sh './deploy.sh' } }
    stage('TraceIQ regression') {
      options { timeout(time: 40, unit: 'MINUTES') }
      steps {
        sh '''
          export GIT_SHA="$GIT_COMMIT" GIT_BRANCH="${BRANCH_NAME:-$GIT_BRANCH}"
          bash ci/traceiq-gate.sh
        '''
      }
    }
  }
}
```

## GitLab CI

```yaml
traceiq-regression:
  stage: verify            # after your deploy stage
  image: alpine:3.20
  needs: [deploy]
  variables:
    TRACEIQ_URL: https://traceiq.example.com
    SUITE_ID: "12"
    PROJECT_ID: "3"
    AGENT_ID: gitlab-ci
  before_script:
    - apk add --no-cache bash curl jq
  script:
    # TRACEIQ_API_KEY: a masked, protected CI/CD variable
    - GIT_SHA="$CI_COMMIT_SHA" GIT_BRANCH="$CI_COMMIT_REF_NAME" bash ci/traceiq-gate.sh
  timeout: 40m
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
```

## GitHub Actions

The repo ships an action (`integrations/github-action/`) that starts the run,
waits, posts a PR comment and fails the step on failures — use it for PRs:

```yaml
- uses: ./integrations/github-action      # or <owner>/<repo>/integrations/github-action@<ref>
  with:
    base-url: https://traceiq.example.com
    api-key: ${{ secrets.TRACEIQ_API_KEY }}
    suite-id: 12
    project-id: 3
    triggered-by: ci
```

After a deploy, where the release gate (not just run status) should decide,
the raw variant:

```yaml
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: ./deploy.sh
      - name: TraceIQ post-deploy gate
        timeout-minutes: 40
        env:
          TRACEIQ_URL: https://traceiq.example.com
          TRACEIQ_API_KEY: ${{ secrets.TRACEIQ_API_KEY }}
          SUITE_ID: "12"
          PROJECT_ID: "3"
          AGENT_ID: github-actions
          GIT_SHA: ${{ github.sha }}
          GIT_BRANCH: ${{ github.ref_name }}
        run: bash ci/traceiq-gate.sh
```

GitHub-hosted runners have no fixed egress range, so a CIDR-restricted key
needs self-hosted runners (or a fixed-IP egress) to work.

## Notes

- The gate evaluates **every finished run for the commit**, so a commit that
  ran the suite twice (retry) is judged on both; configure what counts in the
  project's gate policy (`GET/PUT /api/projects/{id}/quality-gate/policy`).
  `GET /api/projects/{id}/release-readiness?git_commit=<sha>` gives the same
  verdict with the reasons and evidence, for a richer job summary.
- Quarantined (flaky) cases are skipped at dispatch, so they neither fail the
  run nor the gate.
- To test a branch build before deploy, from the CI runner itself, use a local
  worker (`local_worker_id`, see CLAUDE.md) instead of the server workers.
