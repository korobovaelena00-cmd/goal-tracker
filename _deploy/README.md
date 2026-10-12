# Staged deploy (preprod -> prod)

Goal Tracker deploys in TWO steps so a broken build never reaches all users:

1. **stage** - write the new app to `preview/index.html` only (owner-only preprod).
2. **promote** - after the owner verifies on `/preview/?cohort=preview`, copy the
   verified preview *body* to `index.html` (prod) and `beta/index.html`.

## Why this exists
Previously every edit was committed to `index.html` + `beta/` + `preview/` in one
commit, so preprod was just a mirror and prod moved instantly. Now the app **body**
is one artifact and the `<head>` gate (the only difference between the three served
files) is re-injected per path from constants in `deploy_staged.py`. Staging and
promotion only ever move the body.

## Served layout (identical except the gate)
- `index.html`         = ROOT_GATE    + BODY  (fail-open cohort router)
- `beta/index.html`    = BETA_GATE    + BODY  (return guard, cohort=beta)
- `preview/index.html` = PREVIEW_GATE + BODY  (return guard, cohort=preview, owner-exempt)

## Usage (token is a required argv, never hardcoded)
    python3 _deploy/deploy_staged.py <gh_token> status
    python3 _deploy/deploy_staged.py <gh_token> stage   <local_app.html> [--dry-run]
    python3 _deploy/deploy_staged.py <gh_token> promote                  [--dry-run]

`status` prints HEAD, body lengths, whether each gate matches the embedded
constant, and whether preview is promotable. Refuses if the app marker is absent
or JS fails `node --check`.

Exit codes: 0 ok, 1 error, 2 refused.
