# GCP Cronjobs — Operations Guide

All scheduled jobs run as Cloud Run jobs in `europe-north1`, triggered by Cloud Scheduler in `europe-west1`.

- **Service account**: `1031374119937-compute@developer.gserviceaccount.com`
- **Artifact Registry**: `europe-north1-docker.pkg.dev/stock-agent-447619/`
- **Scheduler timezone**: `Europe/Helsinki`

> **Secrets — TODO(security):** All jobs currently carry `DATABASE_URL`,
> `OPENROUTER_API_KEY`, `BRAVE_API_KEY`, and (on the two daily recap jobs)
> `OPENAI_API_KEY` as **plaintext env vars** (visible in
> `gcloud run jobs describe`). Move these to Secret Manager (`--set-secrets`) and
> rotate the current values, which have been exposed in plaintext.

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
| Schedule | `0 5 * * 2-6` — Tue-Sat 05:00 Helsinki (after US close) |
| CPU / Memory | 1 CPU / 2 Gi |
| Timeout | 1800s |
| Max retries | 1 |
| Entry point | Image `CMD`: `run_market_recap.py --cadence daily --markets US,VN,FI` **&&** `run_recap_audio.py --cadence daily --kind market --limit 10 --since-days 3` |

### Required env vars

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Neon PostgreSQL connection string |
| `OPENROUTER_API_KEY` | LLM generation (OpenRouter) |
| `BRAVE_API_KEY` | Search retrieval |
| `OPENAI_API_KEY` | **Audio stage** — speakable rewrite (`gpt-4o-mini`) + TTS (`gpt-4o-mini-tts`) |
| `PYTHONUNBUFFERED` | `1` (Cloud Logging line-by-line) |

See [Audio stage](#audio-stage-recap-narration) for how the second command behaves.

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
  --schedule="0 5 * * 2-6" \
  --time-zone="Europe/Helsinki" \
  --uri="https://europe-north1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/stock-agent-447619/jobs/daily-market-recap:run" \
  --http-method=POST \
  --oauth-service-account-email=1031374119937-compute@developer.gserviceaccount.com \
  --oauth-token-scope=https://www.googleapis.com/auth/cloud-platform \
  --attempt-deadline=180s \
  --description="Daily market recap (US+VN+FI) Tue-Sat at 5am Helsinki"
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

## daily-ticker-recap

Generates daily per-ticker news recaps for a fixed set of popular US tickers and persists them to the `ticker_recap` table. Exit code 0 = at least one ticker succeeded; exit code 1 = every ticker failed/skipped (triggers alert).

| Attribute | Value |
|-----------|-------|
| Cloud Run job | `daily-ticker-recap` (europe-north1) |
| Image | `europe-north1-docker.pkg.dev/stock-agent-447619/market-recap-repo/ticker-recap:latest` |
| Dockerfile | `Dockerfile.ticker-recap` |
| Scheduler | `daily-ticker-recap-scheduler` (europe-west1) |
| Schedule | `0 5 * * 2-6` — Tue-Sat 05:00 Helsinki (after US close) |
| CPU / Memory | 1 CPU / 2 Gi |
| Timeout | 1800s |
| Max retries | 1 |
| Entry point | `run_ticker_recap.py --cadence daily` **&&** `run_recap_audio.py --cadence daily --kind ticker --limit 20 --since-days 3` |

The ticker set = built-in `POPULAR_TICKERS` (in `scripts/run_ticker_recap.py`) **merged with** the optional `RECAP_TICKERS` env var. Adding/overriding tickers via the env var needs **no image rebuild** — just a job update (see below).

Also requires `OPENAI_API_KEY` for the audio stage — see [Audio stage](#audio-stage-recap-narration). **`--limit 20` must stay above the ticker count**; raise it when tickers are added or the newest recaps will crowd out the rest.

### Add / change tickers (no redeploy)

The live ticker set = built-in `POPULAR_TICKERS` **merged with / overridden by** the `RECAP_TICKERS` env var. Since the env-reader shipped (image rebuilt 2026-07-11), changing tickers is a **pure env-var update — no image rebuild, no scheduler change**. `RECAP_TICKERS` currently holds the full authoritative list of 6 (NVDA, AAPL, TSLA, GOOG, NKE, DELL).

**Process:**

1. Compose the value: semicolon-separated `TICKER:Company Name:MARKET` records (market optional → US). Merge semantics — env entries add to / override same-key built-ins; you can't *remove* a built-in via env (that's a code change). Keep the existing entries and append the new ones so the var stays the full list.
2. Update the job (note the `^##^` prefix — see gotcha):
   ```bash
   gcloud run jobs update daily-ticker-recap \
     --region=europe-north1 \
     --update-env-vars="^##^RECAP_TICKERS=NVDA:NVIDIA Corporation;AAPL:Apple Inc.;TSLA:Tesla, Inc.;GOOG:Alphabet Inc.;NKE:NIKE, Inc.;DELL:Dell Technologies Inc.;<NEW>:<Name>"
   ```
3. Verify:
   ```bash
   gcloud run jobs describe daily-ticker-recap --region=europe-north1 \
     --format="json(spec.template.spec.template.spec.containers[0].env)"
   ```
   The next scheduled run (Tue–Sat 05:00 Helsinki) picks it up. To smoke-test now: `gcloud run jobs execute daily-ticker-recap --region=europe-north1` (real run — API cost + DB writes).

**Gotcha:** company names contain commas (`NIKE, Inc.`), but `--update-env-vars` splits on `,` by default — that mangles the value. The `^##^` prefix switches gcloud's delimiter to `##`, preserving commas. Use `--update-env-vars` (not `--set-env-vars`, which wipes the secret env vars). To clear entirely: `--remove-env-vars=RECAP_TICKERS`.

### Build and deploy

```bash
cd backend

docker buildx build --platform linux/amd64 \
  -t europe-north1-docker.pkg.dev/stock-agent-447619/market-recap-repo/ticker-recap:latest \
  -f Dockerfile.ticker-recap --push .

gcloud run jobs update daily-ticker-recap \
  --region=europe-north1 \
  --image=europe-north1-docker.pkg.dev/stock-agent-447619/market-recap-repo/ticker-recap:latest
```

### Create job (first-time)

```bash
gcloud run jobs create daily-ticker-recap \
  --region=europe-north1 \
  --image=europe-north1-docker.pkg.dev/stock-agent-447619/market-recap-repo/ticker-recap:latest \
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
gcloud scheduler jobs create http daily-ticker-recap-scheduler \
  --location=europe-west1 \
  --schedule="0 5 * * 2-6" \
  --time-zone="Europe/Helsinki" \
  --uri="https://europe-north1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/stock-agent-447619/jobs/daily-ticker-recap:run" \
  --http-method=POST \
  --oauth-service-account-email=1031374119937-compute@developer.gserviceaccount.com \
  --oauth-token-scope=https://www.googleapis.com/auth/cloud-platform \
  --attempt-deadline=180s \
  --description="Daily per-ticker news recap (popular US tickers) Tue-Sat at 5am Helsinki"
```

### Manual trigger

```bash
gcloud run jobs execute daily-ticker-recap --region=europe-north1 --wait
```

### Pause / resume scheduler

```bash
gcloud scheduler jobs pause  daily-ticker-recap-scheduler --location=europe-west1
gcloud scheduler jobs resume daily-ticker-recap-scheduler --location=europe-west1
```

### Rollback drill

```bash
# Pause schedule
gcloud scheduler jobs pause daily-ticker-recap-scheduler --location=europe-west1

# Roll back image
gcloud run jobs update daily-ticker-recap \
  --region=europe-north1 \
  --image=europe-north1-docker.pkg.dev/stock-agent-447619/market-recap-repo/ticker-recap:<previous-tag>
```

### Alerting

- Alert policy: `Daily Ticker Recap Job Failed`
- Metric filter: `run.googleapis.com/job/completed_execution_count` with `metric.labels.result="failed"` for `resource.labels.job_name="daily-ticker-recap"`.
- Notification channel: same channel as the daily market recap job alert policy.

---

## Audio stage (recap narration)

Both daily recap jobs run `scripts/run_recap_audio.py` as a **second command chained
with `&&`** after recap generation. It narrates each new recap to an MP3 and uploads
it to GCS; the frontend plays it from the Morning Brief modal.

Pipeline per recap: fetch → LLM "speakable rewrite" (`gpt-4o-mini`) → TTS
(`gpt-4o-mini-tts`, voice `nova`) → upload → persist `audio_key` + `audio_duration_s`
on the recap row.

| Attribute | Value |
|-----------|-------|
| Script | `scripts/run_recap_audio.py` |
| Service | `services/recap_audio.py` |
| Bucket | `gs://stonkie-recap-audio` (europe-north1, uniform access, **private**) |
| Object key | `market/{MARKET}/{cadence}/{period_start}-{id}.mp3`, `ticker/{TICKER}/...` |
| Cost | ~0.3¢ per clip; ~3¢/day for 3 markets + 6 tickers |
| Runtime | ~15–30s per clip, sequential |

### Operational properties

- **Idempotent.** Only selects rows where `audio_key IS NULL`, so re-running costs
  nothing and a missed day self-heals on the next pass.
- **`--since-days 3` bounds the lookback.** Without it the query walks backwards
  through the whole archive once fresh rows are done — an unintended, billable
  backfill. Do not remove this flag. `--since-days -1` disables the bound
  (deliberate full backfill only).
- **`--dry-run`** lists what would be generated without calling any API. Use it
  before changing `--limit` or `--since-days`.
- Historical recaps (before 2026-07-17) were deliberately **not** backfilled and
  return `audio: null` from the API.

### Gotcha: job-level `command`/`args` override the image CMD

`daily-market-recap` was created with explicit `--command`/`--args`, which
**silently override the Dockerfile's `CMD`**. When the audio stage was added to
the Dockerfile, that job kept running the old single command — exiting 0, logging
nothing, generating no audio. `daily-ticker-recap` had no override and picked the
new CMD up immediately.

Cleared with:

```bash
gcloud run jobs update daily-market-recap --region=europe-north1 --command="" --args=""
```

Now all recap jobs take their entry point from the image, so **the Dockerfile is
the single source of truth**. Before assuming a `CMD` change took effect, check:

```bash
gcloud run jobs describe <job> --region=europe-north1 \
  --format="json(spec.template.spec.template.spec.containers[0].command,spec.template.spec.template.spec.containers[0].args)"
# expect: null
```

A job that exits 0 with no application logs is the signature of this problem.

### `&&` coupling — alerting caveat

Because the commands are chained with `&&`:
- A **recap** failure skips the audio stage entirely (no wasted TTS spend). Good.
- An **audio** failure makes the job exit non-zero, firing the recap alert **even
  though recap generation succeeded**. Treat an alert whose logs show
  `recap_audio.job.failed` as an audio-only incident: the recaps are fine, and the
  audio self-heals next run. If this proves noisy, decouple into a separate job.

### GCS auth

Nothing to configure. Both jobs run as
`1031374119937-compute@developer.gserviceaccount.com`, which holds `roles/editor`,
so uploads authenticate via ADC. `AudioStorageConnector` falls back to ADC when
`GOOGLE_APPLICATION_CREDENTIALS_JSON` is unset.

**The API is different.** It runs on **Railway**, outside GCP, and mints *signed*
URLs — which requires a service-account private key that ADC cannot provide. Railway
must have `GOOGLE_APPLICATION_CREDENTIALS_JSON` set (base64 service-account JSON,
same convention as `scripts/export_financial_report.py`). If it is missing, signing
fails silently and the API returns `audio: null` for every recap — the symptom is
"play buttons never appear", not an error.

Signed URLs expire after **6 hours** and are minted per request.

### Known issue — figures corrupted in rewrite

The `gpt-4o-mini` rewrite step **alters financial figures**: observed `50` → `70`,
`40,89` rounded to `40,9`, dropped tickers, and dropped bullets. Accepted for v1 to
ship playback. `validate_script_figures()` logs a `recap_audio.figure_mismatch`
warning but **cannot catch the spelled-out case** (a script with no digits has
nothing to compare) — treat warning counts as weak signal, not a quality gate.
Hardening (stronger rewrite model, or number-token extraction) is deferred.

### Log events

| Event | Meaning |
|-------|---------|
| `recap_audio.job.start` | pending count for this run |
| `recap_audio.job.ok` | per-recap success, with key + duration + warning count |
| `recap_audio.job.failed` | per-recap failure (job will exit non-zero) |
| `recap_audio.job.done` | run summary: attempted + failed counts |
| `recap_audio.job.nothing_pending` | all recaps in window already have audio |
| `recap_audio.job.dry_run` | `--dry-run` listing; nothing was generated |
| `recap_audio.uploaded` | object written to GCS, with key + byte size |
| `recap_audio.figure_mismatch` | rewrite dropped/changed digits (advisory) |
| `recap_audio.signed_url_failed` | API-side signing failure (missing credentials) |
| `recap_audio.credentials_decode_failed` | `GOOGLE_APPLICATION_CREDENTIALS_JSON` present but unparseable |

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
