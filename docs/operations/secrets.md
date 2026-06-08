# Secrets and Credential Operations

This repository must not contain production credentials. Runtime secrets are supplied by the deployment environment: shell environment, CI/CD secret store, container platform secret injection, or a dedicated secret manager chosen by the deployer.

## Required Production Secrets

Set `APP_ENV=production` and provide these values before starting the API or worker:

- `JWT_SECRET`
- `JWT_KEY_ID`
- `POSTGRES_PASSWORD`
- `MINIO_ACCESS_KEY`
- `MINIO_SECRET_KEY`
- `CORS_ORIGINS`

Production startup refuses to continue when required secrets are missing, weak, or known development defaults. Production also requires:

- `SEED_DEV_USERS=false`
- `ALLOW_QUERY_TOKEN_AUTH=false`
- `CORS_ORIGINS` must not include `*`

## JWT Secret Rotation

Access tokens include a `kid` header. The app signs new tokens with `JWT_SECRET` and labels that key with `JWT_KEY_ID`. During rotation, old access tokens can remain valid by listing previous key versions in `JWT_PREVIOUS_SECRETS`.

Format:

```text
JWT_PREVIOUS_SECRETS=old-key-id:old-secret,older-key-id:older-secret
```

Rotation procedure:

1. Generate a new 32+ character random JWT secret.
2. Move the current key into `JWT_PREVIOUS_SECRETS` using its current `JWT_KEY_ID`.
3. Set `JWT_SECRET` to the new secret.
4. Set `JWT_KEY_ID` to a new version label, such as `2026-06-07`.
5. Restart API and worker containers.
6. Wait longer than the access-token lifetime plus operational clock skew. The default access-token lifetime is 15 minutes.
7. Remove the old key from `JWT_PREVIOUS_SECRETS`.
8. Restart API and worker containers again.

Refresh tokens are stored by hash in the database and are not invalidated by JWT signing-key rotation. To force all users to re-authenticate, use the logout-all flow or revoke rows in `refresh_tokens`.

## Database Password Rotation

The exact SQL depends on how Postgres is managed, but the application sequence is:

1. Create or choose a maintenance window.
2. Pause new deploys and long-running batch uploads.
3. Rotate the database password in Postgres for the application user.
4. Update the external secret source for `POSTGRES_PASSWORD`.
5. Restart API and worker containers.
6. Confirm `/health/deps` reports `postgres: ok`.
7. Confirm parsing jobs can read and write documents.
8. Remove the old password from the secret source and any operator shell history.

For managed Postgres, prefer provider-native password rotation and update the injected environment variable through the platform secret mechanism.

## MinIO Credential Rotation

Preferred approach: create a new application access key before removing the old one.

1. Create a new MinIO access key with read/write access to the configured bucket.
2. Update the external secret source for `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY`.
3. Restart API and worker containers.
4. Upload a test PDF and verify it can be viewed through the document detail PDF viewer.
5. Verify a worker can read the object and finish a parse.
6. Revoke the old MinIO credentials.
7. Confirm `/health/deps` and the upload/parse smoke test still pass.

If rotating the MinIO root user, first create least-privileged application credentials and move the app to those. Root credentials should not be used by the API in production.

## Local Development

`.env.example` is documentation only. Real `.env` files are ignored by Git and must not be committed. A local `.env` may use development-only values, but production mode rejects known defaults.

`secrets/` is ignored as defense in depth for local operator files. Do not rely on that directory as a production secret store.
