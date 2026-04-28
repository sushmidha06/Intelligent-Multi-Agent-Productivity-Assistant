# Deploy Runbook

Three services, three providers, one per concern:

| Service       | Host              | Contents                             |
|---------------|-------------------|--------------------------------------|
| Frontend      | Firebase Hosting  | Vue SPA (`dist/`)                    |
| Node backend  | Vercel serverless | `api/index.js` → `server/app.js`     |
| Python AI     | Render Docker     | `python_ai/` (FastAPI + MCP)         |

---

## A. Backend (Node) — Vercel

**Already deployed** at https://sushmi-mcp.vercel.app.

Vercel env vars (set via `vercel env add <NAME> production`):

| Var                        | Purpose                              |
|----------------------------|--------------------------------------|
| `FIREBASE_SERVICE_ACCOUNT` | Admin SDK credentials (JSON)         |
| `FIREBASE_PROJECT_ID`      | `freelance-mcp-c3b42`                |
| `ALLOWED_ORIGINS`          | Firebase Hosting origins (CORS)      |
| `TOKEN_ENCRYPTION_KEY`     | AES-256-GCM key for connections vault|
| `JWT_SHARED_SECRET`        | HS256, must match the Python service |
| `PYTHON_AI_BASE_URL`       | e.g. `https://sushmi-mcp-ai.onrender.com` |

To redeploy after code changes: `vercel --prod --yes`.

---

## B. Frontend (Vue) — Firebase Hosting

**Already deployed** at https://freelance-mcp-c3b42.web.app.

Build-time env (in `.env.production`):

| Var                      | Purpose                              |
|--------------------------|--------------------------------------|
| `VITE_API_BASE_URL`      | `https://sushmi-mcp.vercel.app/api`  |
| `VITE_FIREBASE_*`        | Firebase web config (public)         |

To redeploy after code changes:
```bash
npm run build
firebase deploy --only hosting,firestore:rules
```

---

## C. Python AI service — Render

This is the only service not yet deployed. Blueprint is committed as
`render.yaml` at the repo root.

### One-time setup (3 minutes)

1. Go to https://dashboard.render.com/ and log in.
2. Click **New → Blueprint**.
3. Pick the `sushmidha06/freelance-mcp` repo and the `main` branch.
4. Render reads `render.yaml` and pre-fills a `sushmi-mcp-ai` Docker web service.
5. When prompted, paste these **secret env vars** (the non-secret ones are
   already in the YAML):
   - `GEMINI_API_KEY` — your Gemini API key (from https://aistudio.google.com/apikey)
   - `JWT_SHARED_SECRET` — copy the exact value from Vercel env vars
6. Click **Apply**.
7. Wait ~3 minutes for the first Docker build + deploy.
8. Copy the service URL (e.g. `https://sushmi-mcp-ai.onrender.com`).

### Close the loop

Back on Vercel, add one more env var and redeploy:

```bash
echo -n "https://sushmi-mcp-ai.onrender.com" | vercel env add PYTHON_AI_BASE_URL production
vercel --prod --yes
```

Verify:

```bash
curl https://sushmi-mcp-ai.onrender.com/health
# → {"status":"ok","service":"sushmi-mcp-ai","model":"gemini-2.0-flash","configured":true}
```

Now **Ask Sushmi** in the UI should return a real answer with tool-call traces.

### Render free-tier caveat

The free plan sleeps after 15 min idle. First request after idle takes
~30 s to wake up. For a live demo, hit `/health` 60s before you start.
To eliminate sleep, upgrade to Render's Starter plan (~$7/mo).

---

## D. Proactive Agents (GitHub Actions Cron)

The system runs background agents every 30 minutes to triage inboxes and monitor project health. This is orchestrated via a GitHub Actions workflow (`.github/workflows/cron.yml`).

### Required Secrets

To enable this, you **must** add the following secrets in **GitHub → Settings → Secrets and variables → Actions**:

| Secret Name | Value |
|-------------|-------|
| `CRON_SHARED_SECRET` | A random string (must match the one on Vercel) |
| `NODE_API_BASE_URL` | `https://sushmi-mcp.vercel.app/api` |
| `PYTHON_AI_BASE_URL` | `https://sushmi-mcp-ai.onrender.com` |

**Important:** You must also set the `CRON_SHARED_SECRET` on your Vercel project:
`vercel env add CRON_SHARED_SECRET production`
Then redeploy: `vercel --prod --yes`.

---

## Env var reference

| Var                        | Vercel | Render | Frontend build |
|----------------------------|:------:|:------:|:--------------:|
| `FIREBASE_SERVICE_ACCOUNT` | ✓      |        |                |
| `FIREBASE_PROJECT_ID`      | ✓      |        |                |
| `ALLOWED_ORIGINS`          | ✓      |        |                |
| `TOKEN_ENCRYPTION_KEY`     | ✓      |        |                |
| `JWT_SHARED_SECRET`        | ✓      | ✓      |                |
| `PYTHON_AI_BASE_URL`       | ✓      |        |                |
| `CRON_SHARED_SECRET`       | ✓      |        |                |
| `GEMINI_API_KEY`           |        | ✓      |                |
| `GEMINI_MODEL`             |        | ✓ (default `gemini-2.0-flash`) |  |
| `GEMINI_EMBED_MODEL`       |        | ✓ (default `models/gemini-embedding-001`) | |
| `NODE_API_BASE_URL`        |        | ✓ (default `https://sushmi-mcp.vercel.app/api`) | |
| `VITE_API_BASE_URL`        |        |        | ✓              |
| `VITE_FIREBASE_*`          |        |        | ✓              |
| `CRON_SHARED_SECRET`       | ✓      |        | (GitHub Secret) |
| `NODE_API_BASE_URL`        |        | ✓      | (GitHub Secret) |

---

## Local dev

Three terminals:

```bash
# terminal 1 — Node backend
cd server && npm run dev
# needs .env with FIREBASE_SERVICE_ACCOUNT, TOKEN_ENCRYPTION_KEY, JWT_SHARED_SECRET

# terminal 2 — Python AI
cd python_ai && .venv/bin/uvicorn app.main:app --port 8001 --reload
# needs GEMINI_API_KEY, JWT_SHARED_SECRET, NODE_API_BASE_URL in env

# terminal 3 — frontend
npm run dev
# VITE_API_BASE_URL in .env pointing at localhost:3001/api
```
