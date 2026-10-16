# secretmgmt — env-file vault for Jenkins

A small Flask app that stores whole `.env` , so Jenkinsfiles don't need hardcoded
secrets or env files committed to git.


## Run

```bash
cd secretmgmt
cp .env.example .env      # fill in DATABASE_URL and VAULT_MASTER_KEY
docker compose up -d --build
```

Open `http://localhost:8097/team3` and click **Create team**. The team token is shown **once**; copy it.
(If `ADMIN_TOKEN` is set, it has to be entered to create a team.)

To try it without a real database, start the bundled throwaway Postgres:

```bash
# in .env: DATABASE_URL=postgresql+psycopg2://vault:vault@db:5432/secret_mgmt_leap
docker compose --profile localdb up -d --build
```

## Jenkins setup

In **Manage Jenkins → Credentials**, add two **Secret text** credentials:

| ID            | Value                                   |
|---------------|-----------------------------------------|
| `vault-url`   | `http://<vault-host>:8097/team3`        |
| `vault-token` | the team token from the UI              |

If Jenkins runs in a container, `localhost` is the Jenkins container itself. In that case use the
host's IP or `host.docker.internal`.

Both Jenkinsfiles pull every file for the team in their `Checkout SCM` stage and unpack it into the
workspace at the stored paths:

```groovy
withCredentials([
  string(credentialsId: 'vault-url', variable: 'VAULT_URL'),
  string(credentialsId: 'vault-token', variable: 'VAULT_TOKEN')
]) {
  sh '''#!/usr/bin/env bash
    set -euo pipefail
    curl -fsS -H "Authorization: Bearer $VAULT_TOKEN" "${VAULT_URL%/}/api/bundle.tar.gz" | tar xzv > .vault-files
  '''
}
```

The `post` blocks delete every file listed in `.vault-files`.

## API

All endpoints take `Authorization: Bearer <team token>`.

| Method | Path                                   | Description                         |
|--------|----------------------------------------|-------------------------------------|
| GET    | `/<team>/api/files`                    | list paths + `updated_at`           |
| GET    | `/<team>/api/file?path=<p>`            | raw file content                    |
| PUT    | `/<team>/api/file?path=<p>`            | create/replace (body = raw content) |
| DELETE | `/<team>/api/file?path=<p>`            | delete                              |
| GET    | `/<team>/api/bundle.tar.gz`            | all files as a tarball              |
| GET    | `/healthz`                             | liveness + DB check                 |

```bash
curl -H "Authorization: Bearer $TOKEN" -X PUT --data-binary @secrets.env \
  "http://localhost:8097/team3/api/file?path=k8s/base/secrets.env"
```

Use **Rotate token** in the UI if a token leaks, then update `vault-token` in Jenkins.
