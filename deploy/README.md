# Deploying weather-lsa-control

Two supported ops patterns:

1) Kubernetes Deployment (preferred) running the built-in scheduler
2) Kubernetes CronJobs (alternative) to run weather checks and queue drains on a schedule
3) Systemd service (non-container host) running the scheduler

Do not run the Deployment and CronJobs at the same time.

## Prereqs
- Build/push your image to a registry your cluster can pull from.
- Provide secrets as files `client_secret.json` and `token.json` (Google OAuth creds) to be mounted at `/app/secrets/`.
- Provide a PVC for `/app/data` (SQLite DB and logs) via `pvc.yaml` (uses default StorageClass).

## Kubernetes: Scheduler Deployment

Apply PVC, Secret, Service, and Deployment:

```bash
kubectl apply -f deploy/k8s/pvc.yaml
# create the Secret from example and your files
kubectl apply -f deploy/k8s/secret-example.yaml
kubectl apply -f deploy/k8s/service.yaml
kubectl apply -f deploy/k8s/deployment.yaml
```

Update `deploy/k8s/deployment.yaml` image to your registry, or set an `imagePullSecret` if needed. Optionally create a ConfigMap for non-secret env and reference it.

Health: `/healthz` and `/readyz` on port 8080. Metrics: Prometheus at 9108.

Region mappings toggle: set `USE_REGION_MAPPINGS=false` in the Deployment (or ConfigMap) to ignore DB region-to-campaign mappings and use default `GOOGLE_ADS_*` IDs. You can also run with CLI flags `--use-region-mappings` or `--no-region-mappings` for ad-hoc runs.

## Kubernetes: CronJobs (alternative)

If you prefer discrete runs, apply the CronJobs instead of the Deployment:

```bash
kubectl apply -f deploy/k8s/pvc.yaml
kubectl apply -f deploy/k8s/secret-example.yaml
kubectl apply -f deploy/k8s/cronjob-weather.yaml
kubectl apply -f deploy/k8s/cronjob-worker.yaml
```

- `cronjob-weather.yaml` runs the weather check every 5 minutes.
- `cronjob-worker.yaml` drains the mutation queue every minute.

## Secrets

Use `deploy/k8s/secret-example.yaml` as a template. You can either inline base64-encoded file contents or use `kubectl create secret generic` from files:

```bash
kubectl create secret generic weather-lsa-secrets \
  --from-file=client_secret.json=secrets/client_secret.json \
  --from-file=token.json=secrets/token.json
```

## Systemd (non-container)

1) Copy the repo to `/opt/weather-lsa-control` (or adjust the unit WorkingDirectory). Ensure a Python 3.13 venv with project requirements is installed.
2) Update `/etc/weather-lsa.env` (sample in `deploy/systemd/weather-lsa.env`).
3) Install and start the service:

```bash
sudo cp deploy/systemd/weather-lsa.service /etc/systemd/system/
sudo cp deploy/systemd/weather-lsa.env /etc/
sudo systemctl daemon-reload
sudo systemctl enable --now weather-lsa
sudo systemctl status weather-lsa
```

The service runs `python -m src --scheduler`, exposing health on `$HEALTH_PORT`.

## Notes
- PVC assumes a default StorageClass; adjust if needed.
- Image name defaults to `weather-lsa-control:latest`; change to your registry image.
- Don’t run Deployment and CronJobs concurrently.
- Secrets mount path `/app/secrets` matches app expectations.
