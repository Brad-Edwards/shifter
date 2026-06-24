# Secrets rotation runbook

Operator procedures for the mechanisms defined in
[`../architecture/secrets-rotation-strategy.md`](../architecture/secrets-rotation-strategy.md).
This file grows as each mechanism ships; it currently covers RDS IAM database
authentication and Django `SECRET_KEY` rotation (issue #159, PR2).

Apply the architecture guardrails in
[`../architecture/secrets-rotation-strategy-preflight-159.md`](../architecture/secrets-rotation-strategy-preflight-159.md)
before changing production rotation behavior. Never put a secret value in
argv, an SSM command body, a workflow log, a Terraform output, or an issue
comment.

## RDS database credentials: IAM authentication

The running portal (web and workers) authenticates to PostgreSQL with a
short-lived RDS IAM token instead of a stored password, so there is no
database password to rotate for the runtime. The credential is the instance
IAM role plus a dedicated `rds_iam` database user.

Components:

- `mission_control` migration `0041_create_portal_runtime_user` creates the
  `portal_runtime` PostgreSQL role, grants it `rds_iam` and schema-wide DML
  (plus default privileges for future tables). It runs as the master user
  during the normal `migrate` step.
- `platform/terraform/modules/portal/ec2` attaches an `rds-db:connect` IAM
  policy scoped to `portal_runtime` on this instance's RDS resource id.
- `config.db_backends.rds_iam` mints the token per connection
  (`generate_db_auth_token`, a local signing call) and enforces SSL.
- `entrypoint.sh` runs migrations as the master user, then switches the
  long-running process to IAM auth (`DB_IAM_AUTH=true`, `DB_USER=portal_runtime`,
  `DB_PASSWORD` dropped) before exec. The master password user remains the
  schema owner and migrator.

### Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `DB_IAM_AUTH` | `false` | When `true`, the connection uses the IAM backend. The entrypoint sets it for the AWS runtime; do not set it by hand for `migrate`. |
| `DB_IAM_AUTH_RUNTIME` | `true` | Break-glass switch. Set `false` to keep the runtime on password auth (master user) for a one-off privileged session. |
| `DB_IAM_USER` | `portal_runtime` | The `rds_iam` runtime role. |
| `DB_IAM_REGION` | `AWS_REGION` | Region used to sign the token. |
| `DB_SSLMODE` | `require` | SSL mode for the IAM connection. RDS rejects IAM auth without SSL. |
| `DB_SSL_ROOT_CERT` | unset | Path to the RDS CA bundle. Set together with `DB_SSLMODE=verify-full` to also verify the server certificate. |

### Cutover (per environment)

The cutover is one deploy. Order matters because the IAM policy and the
`portal_runtime` grant must exist before the runtime switches to IAM auth.

1. Confirm the RDS instance has `iam_database_authentication_enabled = true`
   (already set for the portal instance).
2. Deploy the change. In a single deploy: `terraform apply` attaches the
   `rds-db:connect` policy, then the new container runs migrations as the
   master user (creating `portal_runtime` and its grants), then switches the
   runtime connection to IAM auth.
3. Verify: the portal answers `GET /health/`, and the database connection log
   shows the runtime connecting as `portal_runtime`. Confirm no password is
   present in the running process environment
   (`DB_PASSWORD` is unset for the app process).

### Rollback

If the IAM runtime connection fails (for example a missing grant or policy):

1. Set `DB_IAM_AUTH_RUNTIME=false` in the deployment configuration and
   redeploy. The runtime reverts to the master password user, which is
   unchanged.
2. Investigate the grant (`\du portal_runtime` should show `rds_iam`) and the
   `rds-db:connect` policy on the instance role, then re-enable.

No database password rotation is involved in either direction; the master
password credential is untouched by this mechanism.

## Django SECRET_KEY rotation

`SECRET_KEY` signs sessions and cookies. Rotating it with
`SECRET_KEY_FALLBACKS` keeps signatures from the previous key valid through the
rollout, so no user is logged out.

Do **not** rotate `FIELD_ENCRYPTION_KEY` as part of this procedure. It lives in
the same app secret bundle but rotating it makes encrypted model fields
unreadable without a separate re-encryption migration.

The app secret bundle carries an optional `django_secret_key_fallbacks` field
(a JSON array of previous keys). `config.settings` parses it into
`SECRET_KEY_FALLBACKS` (bounded to five keys); the entrypoint hydrates it.

### Procedure

1. Generate a new key:
   `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`.
2. Update the app secret bundle in Secrets Manager: set `django_secret_key` to
   the new key and add the **current** key to the `django_secret_key_fallbacks`
   array.
3. Redeploy so processes hydrate the new values. New signatures use the new
   key; existing sessions signed by the previous key still verify through the
   fallback.
4. After the longest session lifetime has elapsed (so no live session depends
   on the old key), remove the old key from `django_secret_key_fallbacks` and
   redeploy. The fallback list returns to empty in steady state.

Notify operators and support before the rotation: although sessions survive,
the rotation is a security-relevant event worth recording. Audit the rotation
event only, never the key value.
