# Vizuara — Railway deployment guide

Vizuara is a single Docker image that runs in one of three modes, controlled by
the entrypoint Railway uses for each service:

| Service       | Entry script                       | Public? | Purpose |
|---------------|------------------------------------|---------|---------|
| `dashboard`   | `sh scripts/start_dashboard.sh`    | yes     | operator UI: queue, replies, costs, confidence |
| `poller`      | `sh scripts/start_poller.sh`       | no      | watches LumenX inbox, drafts, logs |
| `visualizer`  | `sh scripts/start_visualizer.sh`   | optional| wiki knowledge graph + chat |

All three services share **one mounted volume at `/data`** (SQLite, MLP checkpoint,
cost log, corpus summary, wiki). Without the shared volume the dashboard would
not see the drafts the poller produces.

---

## 0. Before you start

You need:
- A Railway account (https://railway.app) with billing enabled (CPU build is small;
  expect single-digit USD/month at this traffic level).
- The Vizuara repo pushed to GitHub.
- Your Anthropic API key.
- Your LumenX admin token (the same one currently in `.env`).

---

## 1. Create the Railway project

```
# from your local repo
railway login
railway init                       # name it: vizuara
```

Or in the Railway dashboard: **New Project → Deploy from GitHub repo → pick this repo**.

---

## 2. Create the shared volume

In the Railway dashboard for your new project:

1. **Add Volume**
2. Mount path: **`/data`**
3. Size: **1 GB** is plenty (SQLite + MLP checkpoint + logs are all small)

Note the volume's id — you'll attach it to every service that needs the shared
state in the next step.

---

## 3. Create the `dashboard` service

In the Railway dashboard, **New Service → Empty Service**:

- **Name**: `vizuara-dashboard`
- **Source**: this GitHub repo (Railway auto-detects the `Dockerfile`)
- **Variables** (paste in):
  ```
  ANTHROPIC_API_KEY=sk-ant-...
  LUMENX_ADMIN_TOKEN=lmx_...
  LUMENX_BASE_URL=https://lumenx-demo.up.railway.app
  VIZUARA_DATA_DIR=/data
  VIZUARA_AUTO_SEND_ENABLED=false
  VIZUARA_AUTO_SEND_THRESHOLD=0.5
  START_CMD=sh scripts/start_dashboard.sh
  ```
- **Volume**: attach the shared `/data` volume.
- **Networking**: generate a public domain (e.g. `vizuara-dashboard.up.railway.app`).
- **Healthcheck path**: `/` (already in `railway.json`)

Deploy. First boot runs `bootstrap_if_needed.py` and will take ~3-5 minutes
(it calls Anthropic to build the corpus summary and ~150 confidence training
samples — one-time cost ~$1.60). Subsequent restarts skip the bootstrap.

When the build is green, open the public URL. You should see the **Overview** page.

---

## 4. Create the `poller` service

**New Service → from the same GitHub repo**:

- **Name**: `vizuara-poller`
- **Variables**: same as dashboard, but change:
  ```
  START_CMD=sh scripts/start_poller.sh
  ```
  Keep `VIZUARA_AUTO_SEND_ENABLED=false` until you've completed shadow week.
- **Volume**: attach the SAME `/data` volume (Railway lets multiple services
  mount the same volume).
- **Networking**: NO public domain — this is a background worker.
- **Healthcheck**: disable (no HTTP server).

Deploy. Watch the logs in Railway — within ~10 seconds you should see:

```
[start_poller] starting daemon (AUTO_SEND_ENABLED=false)
... | daemon up | poll_interval=5.0s | AUTO_SEND_ENABLED=False ...
... | poll #1 | entries=N drafted=M ...
```

The poller writes to the same `feedback.db` the dashboard reads from. New
drafts will appear in the dashboard's `/queue` page within seconds.

---

## 5. (Optional) Create the `visualizer` service

If you want the wiki knowledge graph + chat live on a URL:

**New Service** like the dashboard, with:
```
START_CMD=sh scripts/start_visualizer.sh
```
- Volume: attach the same `/data` volume (read-only is fine).
- Generate a public domain (e.g. `vizuara-wiki.up.railway.app`).

Skip this service if you only need the wiki explorer locally.

---

## 6. Shadow week

This is the gate before flipping auto-send live. The plan:

1. Leave `VIZUARA_AUTO_SEND_ENABLED=false` on both services.
2. Let the poller run for **at least 7 days** against real LumenX traffic.
3. Open the dashboard daily. For each draft in `/queue`:
   - Read the customer message and the draft.
   - If the draft is good, click **Send** (with `live-send` checked) to actually post it.
   - If the draft is wrong, edit and send.
4. After a week, look at:
   - `/replies` filtered by `sent_only` — what fraction of sent drafts were
     accepted as-is (edit_distance == 0)?
   - `/confidence` shadow-eval table at your chosen threshold.
   - The hallucination_leak column must be **0** at the threshold you plan to use.

If both metrics look acceptable (rule of thumb: **≥80% accepted-as-is** at your
chosen threshold AND **zero hallucination leaks** in shadow eval), proceed to step 7.

---

## 7. Going live

When you're ready to let the agent auto-send:

1. In the `vizuara-poller` service, set `VIZUARA_AUTO_SEND_ENABLED=true`.
2. Confirm `VIZUARA_AUTO_SEND_THRESHOLD` is the value you picked from the ROC.
3. Redeploy the poller (Railway does this automatically when env vars change).
4. Watch the dashboard. The next `/queue` poll should show some auto-sent rows
   with `mode=auto` and a confidence score recorded.

To roll back at any time: flip `VIZUARA_AUTO_SEND_ENABLED=false` and redeploy.
The poller picks up the change on its next iteration and reverts to draft-only mode.

---

## 8. Re-bootstrap (rare)

The `bootstrap_if_needed.py` script skips steps whose output already exists.
If you want to rebuild from scratch (e.g. after a wiki schema change), shell
into the service and delete the relevant file, then restart:

```
railway run --service vizuara-dashboard sh -c "rm /data/corpus_summary.txt && python -m scripts.bootstrap_if_needed"
```

---

## 9. Updating Anthropic model versions

To switch models (e.g. Sonnet 4.6 → 4.7), edit `src/vizuara/cost/pricing.py`,
push to GitHub, and Railway auto-deploys. No volume reset needed.

---

## 10. Cost estimate (live operation)

Per-message cost at steady state:
- Intent classification: ~$0.002 (Haiku 4.5)
- Draft generation: ~$0.005 with prompt cache warm (Sonnet 4.6)
- **~$0.007 per customer message**

At 1000 customer messages/month: ~**$7/month** in Anthropic costs.
Plus Railway compute: ~$5-10/month for two always-on small services + volume.

The MLP retraining is one-time (~$1.60) unless you re-bootstrap.

---

## Troubleshooting

**Bootstrap fails on first boot**: check the service's env vars — `ANTHROPIC_API_KEY`
and `LUMENX_ADMIN_TOKEN` must be set before the container starts.

**Dashboard says "no checkpoint found"**: the bootstrap script didn't finish.
Check `data/confidence.pt` exists in the volume: `railway run --service
vizuara-dashboard ls -la /data`.

**Poller logs "auto_send_enabled=False" but you wanted auto**: env var update
didn't take effect. Redeploy the service.

**Visualizer 500s on /api/query**: it needs `ANTHROPIC_API_KEY` set in the
visualizer service too — that endpoint calls Sonnet.
