# GCP Cronjobs — Operations Guide

All scheduled jobs run as Cloud Run jobs in `europe-north1`, triggered by Cloud Scheduler in `europe-west1`.

- **Service account**: `1031374119937-compute@developer.gserviceaccount.com`
- **Artifact Registry**: `europe-north1-docker.pkg.dev/stock-agent-447619/`
- **Scheduler timezone**: `Europe/Helsinki`

---

## weekly-market-recap

Generates weekly market recaps for US (Tavily) and VN (Brave) markets and persists them to the `market_recap` table.

| Attribute | Value |
|-----------|-------|
| Cloud Run job | `weekly-market-recap` (europe-north1) |
| Image | `europe-north1-docker.pkg.dev/stock-agent-447619/market-recap-repo/market-recap:latest` |
| Dockerfile | `Dockerfile.market-recap` |
| Scheduler | `weekly-market-recap-scheduler` (europe-west1) |
| Schedule | `0 8 * * 6` — Saturday 08:00 Helsinki ≈ 01:00 New York EDT |
| CPU / Memory | 1 CPU / 2 Gi |
| Timeout | 1800s |
| Max retries | 1 |
| Entry point | `python scripts/run_market_recap.py --markets US,VN,FI --cadence weekly` |

### Required env vars

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Neon PostgreSQL connection string |
| `OPENROUTER_API_KEY` | LLM generation (OpenRouter) |
| `BRAVE_API_KEY` | Search retrieval for all markets (US + VN) |
| `PYTHONUNBUFFERED` | `1` (Cloud Logging line-by-line) |

### Build and deploy

```bash
cd backend

# Build for linux/amd64 (Cloud Run) and push
docker buildx build --platform linux/amd64 \
  -t europe-north1-docker.pkg.dev/stock-agent-447619/market-recap-repo/market-recap:latest \
  -f Dockerfile.market-recap --push .

# Update Cloud Run job to use new image
gcloud run jobs update weekly-market-recap \
  --region=europe-north1 \
  --image=europe-north1-docker.pkg.dev/stock-agent-447619/market-recap-repo/market-recap:latest
```

### Create job (first-time)

```bash
gcloud run jobs create weekly-market-recap \
  --region=europe-north1 \
  --image=europe-north1-docker.pkg.dev/stock-agent-447619/market-recap-repo/market-recap:latest \
  --tasks=1 \
  --max-retries=1 \
  --task-timeout=1800s \
  --cpu=1 \
  --memory=2Gi \
  --execution-environment=gen2 \
  --service-account=1031374119937-compute@developer.gserviceaccount.com \
  --set-env-vars="PYTHONUNBUFFERED=1,DATABASE_URL=<url>,OPENROUTER_API_KEY=<key>,BRAVE_API_KEY=<key>"
```

### Create scheduler (first-time)

```bash
gcloud scheduler jobs create http weekly-market-recap-scheduler \
  --location=europe-west1 \
  --schedule="0 8 * * 6" \
  --time-zone="Europe/Helsinki" \
  --uri="https://europe-north1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/stock-agent-447619/jobs/weekly-market-recap:run" \
  --http-method=POST \
  --oauth-service-account-email=1031374119937-compute@developer.gserviceaccount.com \
  --oauth-token-scope=https://www.googleapis.com/auth/cloud-platform \
  --attempt-deadline=180s \
  --description="Weekly market recap (US+VN) every Saturday at 8am Helsinki"
```

### Manual trigger

```bash
gcloud run jobs execute weekly-market-recap --region=europe-north1 --wait
```

### Pause / resume scheduler

```bash
gcloud scheduler jobs pause  weekly-market-recap-scheduler --location=europe-west1
gcloud scheduler jobs resume weekly-market-recap-scheduler --location=europe-west1
```

### Observability

See `docs/market_recap_operations.md` for structured log event schema, status values, and rollback procedure.

---

## daily-market-recap

Generates daily market recaps for US, VN, and FI markets and persists them to the `market_recap` table.

| Attribute | Value |
|-----------|-------|
| Cloud Run job | `daily-market-recap` (europe-north1) |
| Image | `europe-north1-docker.pkg.dev/stock-agent-447619/market-recap-repo/market-recap-daily:latest` |
| Dockerfile | `Dockerfile.market-recap.daily` |
| Scheduler | `daily-market-recap-scheduler` (europe-west1) |
| Schedule | `0 7 * * 2-6` — Tue-Sat 07:00 Helsinki (after US close) |
| CPU / Memory | 1 CPU / 2 Gi |
| Timeout | 1800s |
| Max retries | 1 |
| Entry point | `python scripts/run_market_recap.py --cadence daily --markets US,VN,FI` |

### Build and deploy

```bash
cd backend

docker buildx build --platform linux/amd64 \
  -t europe-north1-docker.pkg.dev/stock-agent-447619/market-recap-repo/market-recap-daily:latest \
  -f Dockerfile.market-recap.daily --push .

gcloud run jobs update daily-market-recap \
  --region=europe-north1 \
  --image=europe-north1-docker.pkg.dev/stock-agent-447619/market-recap-repo/market-recap-daily:latest
```

### Create job (first-time)

```bash
gcloud run jobs create daily-market-recap \
  --region=europe-north1 \
  --image=europe-north1-docker.pkg.dev/stock-agent-447619/market-recap-repo/market-recap-daily:latest \
  --tasks=1 \
  --max-retries=1 \
  --task-timeout=1800s \
  --cpu=1 \
  --memory=2Gi \
  --execution-environment=gen2 \
  --service-account=1031374119937-compute@developer.gserviceaccount.com \
  --set-env-vars="PYTHONUNBUFFERED=1,DATABASE_URL=<url>,OPENROUTER_API_KEY=<key>,BRAVE_API_KEY=<key>"
```

### Create scheduler (first-time)

```bash
gcloud scheduler jobs create http daily-market-recap-scheduler \
  --location=europe-west1 \
  --schedule="0 7 * * 2-6" \
  --time-zone="Europe/Helsinki" \
  --uri="https://europe-north1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/stock-agent-447619/jobs/daily-market-recap:run" \
  --http-method=POST \
  --oauth-service-account-email=1031374119937-compute@developer.gserviceaccount.com \
  --oauth-token-scope=https://www.googleapis.com/auth/cloud-platform \
  --attempt-deadline=180s \
  --description="Daily market recap (US+VN+FI) Tue-Sat at 7am Helsinki"
```

### Manual trigger

```bash
gcloud run jobs execute daily-market-recap --region=europe-north1 --wait
```

### Pause / resume scheduler

```bash
gcloud scheduler jobs pause  daily-market-recap-scheduler --location=europe-west1
gcloud scheduler jobs resume daily-market-recap-scheduler --location=europe-west1
```

### Rollback drill

```bash
# Pause schedule
gcloud scheduler jobs pause daily-market-recap-scheduler --location=europe-west1

# Roll back image
gcloud run jobs update daily-market-recap \
  --region=europe-north1 \
  --image=europe-north1-docker.pkg.dev/stock-agent-447619/market-recap-repo/market-recap-daily:<previous-tag>
```

### Alerting

- Alert policy: `Daily Market Recap Job Failed`
- Metric filter: `run.googleapis.com/job/completed_execution_count` with `metric.labels.result="failed"` for `resource.labels.job_name="daily-market-recap"`.
- Notification channel: same channel as weekly recap job alert policy.

---

## quarterly-financial-export

Exports quarterly financial reports via Playwright/Chromium browser automation.

| Attribute | Value |
|-----------|-------|
| Cloud Run job | `quarterly-financial-export` (europe-north1) |
| Image | `europe-north1-docker.pkg.dev/stock-agent-447619/quarterly-export-repo/quarterly-export:latest` |
| Dockerfile | `Dockerfile.export-quarterly` |
| Scheduler | `quarterly-financial-export-scheduler` (europe-west1) |
| Schedule | `0 1 1 * *` — 1st of each month at 01:00 Helsinki |
| CPU / Memory | 8 CPU / 32 Gi (Playwright) |
| Timeout | 3600s |
| Max retries | 1 |

### Build and deploy

```bash
cd backend

docker buildx build --platform linux/amd64 \
  -t europe-north1-docker.pkg.dev/stock-agent-447619/quarterly-export-repo/quarterly-export:latest \
  -f Dockerfile.export-quarterly --push .

gcloud run jobs update quarterly-financial-export \
  --region=europe-north1 \
  --image=europe-north1-docker.pkg.dev/stock-agent-447619/quarterly-export-repo/quarterly-export:latest
```

### Manual trigger

```bash
gcloud run jobs execute quarterly-financial-export --region=europe-north1 --wait
```
