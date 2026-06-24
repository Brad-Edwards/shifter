# Post-deploy range smoke (#218)

Operator/CI harness that provisions a real dev range through existing CMS/engine
services, verifies guest connectivity, and tears down via request-id ownership paths.

## Required environment

- `SMOKE_TEST_USER_EMAIL` — dedicated smoke actor with no active range at start
- `SMOKE_WINDOWS_AGENT_ID` / `SMOKE_LINUX_AGENT_ID` — required for the `windows`
  variant (`ad_attack_lab` scenario)
- AWS credentials with SSM access to the portal EC2 instance
- `ENV` (default `dev`) and optional `PORTAL_INSTANCE_TAG` (default `${ENV}-portal`)

## Local usage

```bash
export ENV=dev
export AWS_PROFILE=<dev profile>
export SMOKE_TEST_USER_EMAIL=smoke@example.com
bash scripts/smoke-test.sh --variant linux
bash scripts/smoke-test-windows.sh
```

Implementation lives in `cms/post_deploy_smoke/` and
`python manage.py run_post_deploy_smoke`.
