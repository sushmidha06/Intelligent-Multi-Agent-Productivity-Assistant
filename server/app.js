import express from 'express';
import cors from 'cors';
import helmet from 'helmet';
import morgan from 'morgan';
import dotenv from 'dotenv';
import { DBService } from './services/dbService.js';
import { AuthService } from './services/authService.js';
import { NotificationsService } from './services/notificationsService.js';
import { ConnectionsService } from './services/connectionsService.js';
import { signServiceToken, verifyServiceToken } from './services/jwtService.js';
import { fetchRecentGmail, fetchOneGmail, fetchGmailWithBodies } from './services/gmailFetcher.js';
import { extractExpenseFromEmail } from './services/expenseExtractor.js';
import { draftReplyForEmail } from './services/draftService.js';
import { EmailBodyStore } from './services/emailBodyStore.js';
import { classifyEmail, FOLDER_ORDER } from './services/emailClassifier.js';
import { EmailMetaService } from './services/emailMetaService.js';
import { ExpensesService, EXPENSE_CATEGORIES } from './services/expensesService.js';
import axios from 'axios';

dotenv.config();

const app = express();

const allowedOrigins = (process.env.ALLOWED_ORIGINS || '')
  .split(',').map(s => s.trim()).filter(Boolean);

app.use(cors({
  origin: (origin, cb) => {
    if (!origin) return cb(null, true); // curl / server-to-server
    if (allowedOrigins.length === 0) return cb(null, true); // dev: allow all
    if (allowedOrigins.includes(origin)) return cb(null, true);
    return cb(new Error('CORS blocked: ' + origin));
  },
  credentials: true,
}));
app.use(helmet());
app.use(morgan('dev'));
app.use(express.json());

function getToken(req) {
  const h = req.headers.authorization || '';
  return h.startsWith('Bearer ') ? h.slice(7) : null;
}

async function requireAuth(req, res, next) {
  try {
    const user = await AuthService.getUserByToken(getToken(req));
    if (!user) return res.status(401).json({ error: 'Not authenticated' });
    req.user = user;
    next();
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
}

app.get('/api/health', (_req, res) => {
  res.json({ status: 'ok', service: 'Sushmi-MCP Gateway' });
});

// --- Auth ---
app.post('/api/auth/signup', async (req, res) => {
  try {
    const result = await AuthService.signUp(req.body || {});
    await NotificationsService.push(result.user.id, {
      title: 'Welcome to Sushmi MCP',
      body: 'Your workspace is ready. Create a project or invoice to get started.',
      kind: 'success',
    });
    res.status(201).json(result);
  } catch (e) { res.status(e.status || 500).json({ error: e.message }); }
});

app.post('/api/auth/signin', async (req, res) => {
  try {
    const result = await AuthService.signIn(req.body || {});
    res.json(result);
  } catch (e) { res.status(e.status || 500).json({ error: e.message, code: e.code }); }
});

app.post('/api/auth/google', async (req, res) => {
  try {
    const result = await AuthService.googleUpsert(req.body || {});
    if (result.isNew) {
      await NotificationsService.push(result.user.id, {
        title: 'Welcome to Sushmi MCP',
        body: 'Signed in with Google. Your workspace is ready.',
        kind: 'success',
      });
    }
    res.status(result.isNew ? 201 : 200).json(result);
  } catch (e) { res.status(e.status || 500).json({ error: e.message }); }
});

app.post('/api/auth/logout', async (req, res) => {
  await AuthService.logout(getToken(req));
  res.json({ ok: true });
});

app.get('/api/auth/me', async (req, res) => {
  try {
    const user = await AuthService.getUserByToken(getToken(req));
    if (!user) return res.status(401).json({ error: 'Not authenticated' });
    res.json({ user });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

// --- Settings (account) ---
app.patch('/api/auth/profile', requireAuth, async (req, res) => {
  try {
    const user = await AuthService.updateProfile(req.user.id, req.body || {});
    await NotificationsService.push(user.id, {
      title: 'Profile updated',
      body: 'Your account details have been saved.',
      kind: 'info',
    });
    res.json({ user });
  } catch (e) { res.status(e.status || 500).json({ error: e.message }); }
});

app.post('/api/auth/change-password', requireAuth, async (req, res) => {
  try {
    const result = await AuthService.changePassword(req.user.id, req.body || {});
    await NotificationsService.push(result.user.id, {
      title: 'Password changed',
      body: 'Your password was updated. Other sessions have been signed out.',
      kind: 'success',
    });
    res.json(result);
  } catch (e) { res.status(e.status || 500).json({ error: e.message }); }
});

app.patch('/api/auth/preferences', requireAuth, async (req, res) => {
  try {
    const preferences = await AuthService.updatePreferences(req.user.id, req.body || {});
    res.json({ preferences });
  } catch (e) { res.status(e.status || 500).json({ error: e.message }); }
});

app.delete('/api/auth/account', requireAuth, async (req, res) => {
  const ok = await AuthService.deleteAccount(req.user.id);
  res.json({ ok });
});

// --- Notifications ---
app.get('/api/notifications', requireAuth, async (req, res) => {
  const [items, unread] = await Promise.all([
    NotificationsService.list(req.user.id),
    NotificationsService.unreadCount(req.user.id),
  ]);
  res.json({ items, unread });
});

app.post('/api/notifications/:id/read', requireAuth, async (req, res) => {
  const n = await NotificationsService.markRead(req.user.id, req.params.id);
  res.json({ ok: !!n, notification: n });
});

app.post('/api/notifications/read-all', requireAuth, async (req, res) => {
  await NotificationsService.markAllRead(req.user.id);
  res.json({ ok: true });
});

app.delete('/api/notifications/:id', requireAuth, async (req, res) => {
  const ok = await NotificationsService.remove(req.user.id, req.params.id);
  res.json({ ok });
});

app.delete('/api/notifications', requireAuth, async (req, res) => {
  await NotificationsService.clear(req.user.id);
  res.json({ ok: true });
});

// --- Integrations (per-user external credentials) ---
app.get('/api/integrations', requireAuth, async (req, res) => {
  try {
    const status = await ConnectionsService.listStatus(req.user.id);
    res.json({ integrations: status });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

app.put('/api/integrations/:provider', requireAuth, async (req, res) => {
  try {
    const { secrets, metadata } = req.body || {};
    const result = await ConnectionsService.connect(req.user.id, req.params.provider, { secrets, metadata });
    await NotificationsService.push(req.user.id, {
      title: `${req.params.provider} connected`,
      body: 'Integration is now active for your AI agents.',
      kind: 'success',
      link: '/integrations',
    });
    res.json(result);
  } catch (e) { res.status(e.status || 500).json({ error: e.message }); }
});

app.delete('/api/integrations/:provider', requireAuth, async (req, res) => {
  try {
    const result = await ConnectionsService.disconnect(req.user.id, req.params.provider);
    res.json(result);
  } catch (e) { res.status(e.status || 500).json({ error: e.message }); }
});

// --- GitHub repo list (used by the New Project modal) ---
// Mirrors what the GitHub MCP server does, but called directly from the
// Node backend so the modal doesn't have to round-trip through Python.
app.get('/api/integrations/github/repos', requireAuth, async (req, res) => {
  try {
    const conn = await ConnectionsService.getDecryptedSecrets(req.user.id, 'github');
    if (!conn) return res.status(404).json({ error: 'GitHub is not connected. Connect it in Integrations first.' });
    const token = (conn.secrets || {}).token;
    if (!token) return res.status(400).json({ error: 'GitHub connection is missing a PAT.' });

    const upstream = await axios.get('https://api.github.com/user/repos', {
      headers: {
        Authorization: `token ${token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
      },
      params: { per_page: 50, sort: 'updated' },
      timeout: 12000,
    });

    const repos = (upstream.data || []).map(r => ({
      full_name: r.full_name,
      description: r.description,
      private: r.private,
      language: r.language,
      stars: r.stargazers_count,
      pushed_at: r.pushed_at,
    }));
    res.json({ repos });
  } catch (e) {
    const status = e.response?.status || 500;
    res.status(status).json({ error: e.response?.data?.message || e.message });
  }
});

// --- Gmail inbox fetch (used by the Inbox Triage page) ---
// Reads only the last 7 days of envelopes (no bodies) to keep the call cheap.
// Each email is classified into a folder; user-overrides in Firestore (email_meta)
// take precedence over the auto-classifier so "Move to" is sticky across refetches.
app.get('/api/inbox/gmail', requireAuth, async (req, res) => {
  try {
    const conn = await ConnectionsService.getDecryptedSecrets(req.user.id, 'gmail');
    if (!conn) return res.status(404).json({ error: 'Gmail is not connected.' });
    const secrets = conn.secrets || {};
    const metadata = conn.metadata || {};
    const email = metadata.email || secrets.email;
    const appPassword = secrets.appPassword || secrets.password;
    if (!email || !appPassword) return res.status(400).json({ error: 'Gmail connection is missing credentials.' });

    const days = Math.max(1, Math.min(parseInt(req.query.days, 10) || 7, 30));
    const limit = Math.max(1, Math.min(parseInt(req.query.limit, 10) || 15, 30));

    // Pull projects in parallel with Gmail so the classifier can match against client names.
    const [emailsRaw, projects, metaMap] = await Promise.all([
      fetchRecentGmail({ email, appPassword, days, limit }),
      DBService.getCollection('projects', req.user.id),
      EmailMetaService.listAll(req.user.id),
    ]);
    const projectClients = [...new Set((projects || []).map(p => p.client).filter(Boolean))];

    const enriched = emailsRaw.map(e => {
      const userMeta = metaMap.get(e.id);
      // User override wins; otherwise auto-classify.
      const folder = userMeta?.folder || classifyEmail({
        from: e.fromAddress,
        fromName: e.from,
        subject: e.subject,
        projectClients,
      });
      return { ...e, folder, deleted: !!userMeta?.deleted };
    });

    // Build the folder tree (skip deleted emails from group counts but keep them
    // in the response so the UI can show a "Trash" view if it wants).
    const visible = enriched.filter(e => !e.deleted);
    const groups = new Map();    // folder path -> emails[]
    for (const e of visible) {
      if (!groups.has(e.folder)) groups.set(e.folder, []);
      groups.get(e.folder).push(e);
    }

    // Render in the canonical order. Subfolders within "clients/" are sorted alphabetically.
    const folders = [];
    for (const top of FOLDER_ORDER) {
      if (top === 'clients') {
        const subs = [...groups.keys()].filter(k => k.startsWith('clients/')).sort();
        for (const sub of subs) {
          folders.push({ path: sub, top: 'clients', label: sub.slice('clients/'.length), count: groups.get(sub).length });
        }
      } else if (groups.has(top)) {
        folders.push({ path: top, top, label: top, count: groups.get(top).length });
      }
    }

    res.json({
      emails: enriched,
      folders,
      counts: { total: visible.length, deleted: enriched.length - visible.length },
      days,
      limit,
      source: 'gmail',
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Move an email to a different folder (creates user override in email_meta).
app.patch('/api/inbox/email/:id/folder', requireAuth, async (req, res) => {
  const folder = (req.body || {}).folder;
  if (!folder || typeof folder !== 'string') return res.status(400).json({ error: 'folder is required' });
  try {
    await EmailMetaService.setFolder(req.user.id, req.params.id, folder.trim());
    res.json({ ok: true, id: req.params.id, folder });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Soft-delete an email (we never modify Gmail itself).
app.delete('/api/inbox/email/:id', requireAuth, async (req, res) => {
  try {
    await EmailMetaService.setDeleted(req.user.id, req.params.id, true);
    res.json({ ok: true, id: req.params.id, deleted: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Restore a soft-deleted email.
app.post('/api/inbox/email/:id/restore', requireAuth, async (req, res) => {
  try {
    await EmailMetaService.setDeleted(req.user.id, req.params.id, false);
    res.json({ ok: true, id: req.params.id, deleted: false });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Fetch a single email's full body (used by the "convert to expense" flow).
app.get('/api/inbox/email/:id/body', requireAuth, async (req, res) => {
  try {
    const conn = await ConnectionsService.getDecryptedSecrets(req.user.id, 'gmail');
    if (!conn) return res.status(404).json({ error: 'Gmail is not connected.' });
    const secrets = conn.secrets || {};
    const metadata = conn.metadata || {};
    const email = metadata.email || secrets.email;
    const appPassword = secrets.appPassword || secrets.password;
    if (!email || !appPassword) return res.status(400).json({ error: 'Gmail credentials missing.' });

    // Email IDs are "g_<uid>" — strip the prefix to get the IMAP UID.
    const id = String(req.params.id || '');
    const uid = id.startsWith('g_') ? id.slice(2) : id;
    const data = await fetchOneGmail({ email, appPassword, uid });
    res.json(data);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Sync recent Gmail bodies into the per-user knowledge base. Heavy IMAP call,
// fronted by a Sync button in the UI so the user controls when it runs.
app.post('/api/inbox/sync-rag', requireAuth, async (req, res) => {
  try {
    const conn = await ConnectionsService.getDecryptedSecrets(req.user.id, 'gmail');
    if (!conn) return res.status(404).json({ error: 'Gmail is not connected.' });
    const secrets = conn.secrets || {};
    const metadata = conn.metadata || {};
    const email = metadata.email || secrets.email;
    const appPassword = secrets.appPassword || secrets.password;
    if (!email || !appPassword) return res.status(400).json({ error: 'Gmail credentials missing.' });

    const days  = Math.max(1, Math.min(parseInt(req.body?.days,  10) || 30,  90));
    const limit = Math.max(1, Math.min(parseInt(req.body?.limit, 10) || 100, 200));

    const emails = await fetchGmailWithBodies({ email, appPassword, days, limit });
    const indexed = await EmailBodyStore.upsertMany(req.user.id, emails);

    await NotificationsService.push(req.user.id, {
      title: 'Inbox synced to knowledge base',
      body: `Indexed ${indexed} emails (last ${days} days). Ask Sushmi about your inbox now.`,
      kind: 'success',
      link: '/inbox',
    });
    res.json({ ok: true, indexed, days, limit });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Stats for the Inbox page sync banner.
app.get('/api/inbox/sync-rag/status', requireAuth, async (req, res) => {
  try {
    const [count, lastSyncedAt] = await Promise.all([
      EmailBodyStore.count(req.user.id),
      EmailBodyStore.lastSyncedAt(req.user.id),
    ]);
    res.json({ count, lastSyncedAt });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Generate a Gemini reply and save it as a Gmail draft (IMAP APPEND to [Gmail]/Drafts).
app.post('/api/inbox/email/:id/draft-reply', requireAuth, async (req, res) => {
  try {
    const conn = await ConnectionsService.getDecryptedSecrets(req.user.id, 'gmail');
    if (!conn) return res.status(404).json({ error: 'Gmail is not connected.' });
    const secrets = conn.secrets || {};
    const metadata = conn.metadata || {};
    const email = metadata.email || secrets.email;
    const appPassword = secrets.appPassword || secrets.password;
    if (!email || !appPassword) return res.status(400).json({ error: 'Gmail credentials missing.' });

    const id = String(req.params.id || '');
    const uid = id.startsWith('g_') ? id.slice(2) : id;

    const result = await draftReplyForEmail({ user: { email, appPassword }, emailUid: uid });

    await NotificationsService.push(req.user.id, {
      title: 'AI reply drafted',
      body: `Saved to ${result.draftsMailbox} — open Gmail to review and send.`,
      kind: 'success',
      link: '/inbox',
    });
    res.json(result);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Run Gemini on an email body and return a draft expense object (no save).
// The user reviews + edits in a modal before they hit Save.
app.post('/api/inbox/email/:id/extract-expense', requireAuth, async (req, res) => {
  try {
    const conn = await ConnectionsService.getDecryptedSecrets(req.user.id, 'gmail');
    if (!conn) return res.status(404).json({ error: 'Gmail is not connected.' });
    const secrets = conn.secrets || {};
    const metadata = conn.metadata || {};
    const email = metadata.email || secrets.email;
    const appPassword = secrets.appPassword || secrets.password;

    const id = String(req.params.id || '');
    const uid = id.startsWith('g_') ? id.slice(2) : id;
    const meta = await fetchOneGmail({ email, appPassword, uid });
    const draft = await extractExpenseFromEmail(meta);
    res.json({
      emailId: id,
      sourceEmail: { uid: meta.uid, from: meta.from, subject: meta.subject, date: meta.date },
      draft,
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// --- Expenses ---
app.get('/api/expenses', requireAuth, async (req, res) => {
  try {
    const items = await ExpensesService.list(req.user.id, { projectId: req.query.projectId });
    res.json({ items, categories: EXPENSE_CATEGORIES });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/expenses', requireAuth, async (req, res) => {
  try {
    const created = await ExpensesService.create(req.user.id, req.body || {});
    await NotificationsService.push(req.user.id, {
      title: 'Expense logged',
      body: `${created.vendor} — ${created.category || 'uncategorised'}`,
      kind: 'info',
      link: '/expenses',
    });
    res.status(201).json(created);
  } catch (e) {
    res.status(e.status || 500).json({ error: e.message });
  }
});

app.patch('/api/expenses/:id', requireAuth, async (req, res) => {
  try {
    const updated = await ExpensesService.update(req.user.id, req.params.id, req.body || {});
    if (!updated) return res.status(404).json({ error: 'expense not found' });
    res.json(updated);
  } catch (e) {
    res.status(e.status || 500).json({ error: e.message });
  }
});

app.delete('/api/expenses/:id', requireAuth, async (req, res) => {
  try {
    const ok = await ExpensesService.remove(req.user.id, req.params.id);
    res.json({ ok });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// --- Warm up Render free-tier instance to avoid cold-start on first chat ---
app.get('/api/chat/warmup', async (req, res) => {
  const aiUrl = process.env.PYTHON_AI_BASE_URL;
  if (!aiUrl) return res.json({ warmed: false, reason: 'not configured' });
  try {
    await axios.get(`${aiUrl}/health`, { timeout: 60000 });
    res.json({ warmed: true });
  } catch (e) {
    res.json({ warmed: false, reason: e.message });
  }
});

// --- Chat proxy → Python AI service ---
app.post('/api/chat', requireAuth, async (req, res) => {
  const aiUrl = process.env.PYTHON_AI_BASE_URL;
  if (!aiUrl) return res.status(503).json({ error: 'AI service not configured' });
  try {
    const token = signServiceToken({ userId: req.user.id, email: req.user.email });
    const upstream = await axios.post(`${aiUrl}/chat`, req.body || {}, {
      headers: { Authorization: `Bearer ${token}` },
      timeout: 55000,
    });
    res.json(upstream.data);
  } catch (e) {
    const status = e.response?.status || 502;
    res.status(status).json({ error: e.response?.data?.detail || e.message });
  }
});

// --- Internal: Python agent creates an expense on behalf of the user ---
app.post('/api/internal/expenses', async (req, res) => {
  const auth = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  const claims = verifyServiceToken(auth);
  if (!claims?.userId) return res.status(401).json({ error: 'invalid service token' });
  try {
    const created = await ExpensesService.create(claims.userId, { ...(req.body || {}), source: req.body?.source || 'agent' });
    await NotificationsService.push(claims.userId, {
      title: 'AI logged an expense',
      body: `${created.vendor} — ${formatCurrencyAmount(created.amount)}`,
      kind: 'info',
      link: '/expenses',
    });
    res.status(201).json(created);
  } catch (e) { res.status(e.status || 500).json({ error: e.message }); }
});

function formatCurrencyAmount(n) {
  const v = Number(n || 0);
  return Number.isFinite(v) ? v.toLocaleString() : String(v);
}

// --- Internal: Python AI service fetches per-user secrets via JWT ---
app.get('/api/internal/connections/:provider', async (req, res) => {
  const auth = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  const claims = verifyServiceToken(auth);
  if (!claims?.userId) return res.status(401).json({ error: 'invalid service token' });
  try {
    const data = await ConnectionsService.getDecryptedSecrets(claims.userId, req.params.provider);
    if (!data) return res.status(404).json({ error: 'not connected' });
    res.json(data);
  } catch (e) { res.status(e.status || 500).json({ error: e.message }); }
});

// --- Internal: Python AI service fetches user's Firestore data ---
app.get('/api/internal/data/:collection', async (req, res) => {
  const auth = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  const claims = verifyServiceToken(auth);
  if (!claims?.userId) return res.status(401).json({ error: 'invalid service token' });
  const allowed = new Set(['projects', 'invoices', 'emails', 'alerts']);
  if (!allowed.has(req.params.collection)) return res.status(400).json({ error: 'unknown collection' });
  try {
    const items = await DBService.getCollection(req.params.collection, claims.userId);
    res.json({ items });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

// Internal: Python AI fetches indexed email bodies for RAG construction.
app.get('/api/internal/email-bodies', async (req, res) => {
  const auth = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  const claims = verifyServiceToken(auth);
  if (!claims?.userId) return res.status(401).json({ error: 'invalid service token' });
  try {
    const items = await EmailBodyStore.list(claims.userId, { limit: 200 });
    res.json({ items });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

// --- App data (all per-user) ---
app.get('/api/dashboard', requireAuth, async (req, res) => {
  const data = await DBService.getDashboardStats(req.user.id);
  res.json(data);
});

app.get('/api/inbox', requireAuth, async (req, res) => {
  const emails = await DBService.getCollection('emails', req.user.id);
  res.json(emails);
});

app.get('/api/projects', requireAuth, async (req, res) => {
  // Run Firestore reads + the GitHub token lookup in parallel — they don't depend on each other.
  const [projects, expenseSpent, ghConn] = await Promise.all([
    DBService.getCollection('projects', req.user.id),
    ExpensesService.sumByProject(req.user.id).catch(() => ({})),
    ConnectionsService.getDecryptedSecrets(req.user.id, 'github').catch(() => null),
  ]);

  // Apply expense rollup first — works whether or not GitHub is connected.
  const withSpent = projects.map(p => {
    const fromExpenses = expenseSpent[p.id] || 0;
    return fromExpenses > 0 ? { ...p, spent: fromExpenses } : p;
  });

  const token = ghConn?.secrets?.token;
  const reposNeeded = withSpent.filter(p => p.repo).map(p => p.repo);
  if (reposNeeded.length === 0 || !token) return res.json(withSpent);

  const headers = {
    Authorization: `token ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
  };

  // Fetch participation stats (52-week commit histogram) per repo, in parallel.
  // GitHub returns 202 the first time it computes; fall back to one commits page.
  async function fetchCommitCount(repo) {
    try {
      const part = await axios.get(`https://api.github.com/repos/${repo}/stats/participation`, { headers, timeout: 6000 });
      const all = (part.data && part.data.all) || [];
      if (all.length) return { repo, commits: all.reduce((s, n) => s + n, 0), pushedAt: null };
    } catch { /* fall through */ }
    try {
      const commits = await axios.get(`https://api.github.com/repos/${repo}/commits`, {
        headers, params: { per_page: 100 }, timeout: 6000,
      });
      return { repo, commits: (commits.data || []).length, pushedAt: commits.data?.[0]?.commit?.author?.date || null };
    } catch {
      return { repo, commits: null, pushedAt: null };
    }
  }

  const stats = await Promise.all([...new Set(reposNeeded)].map(fetchCommitCount));
  const byRepo = new Map(stats.map(s => [s.repo, s]));
  const enriched = withSpent.map(p => {
    if (!p.repo) return p;
    const s = byRepo.get(p.repo);
    if (!s || s.commits == null) return p;
    return { ...p, commits: s.commits, pushedAt: s.pushedAt || p.pushedAt };
  });
  res.json(enriched);
});

app.post('/api/projects', requireAuth, async (req, res) => {
  const body = req.body || {};
  if (!body.name || !body.client) return res.status(400).json({ error: 'Name and client are required' });
  const project = {
    name: body.name,
    client: body.client,
    status: body.status || 'On Track',
    health: typeof body.health === 'number' ? body.health : 90,
    commits: 0,
    daysLeft: typeof body.daysLeft === 'number' ? body.daysLeft : 30,
    spent: 0,
    budget: typeof body.budget === 'number' ? body.budget : 5000,
    repo: body.repo || null,
  };
  const saved = await DBService.addToCollection('projects', req.user.id, project);
  await NotificationsService.push(req.user.id, {
    title: 'New project created',
    body: `${saved.name} — ${saved.client}`,
    kind: 'info',
    link: '/projects',
  });
  res.status(201).json(saved);
});

app.get('/api/billing', requireAuth, async (req, res) => {
  const invoices = await DBService.getCollection('invoices', req.user.id);
  res.json(invoices);
});

app.post('/api/billing', requireAuth, async (req, res) => {
  const body = req.body || {};
  if (!body.client || !body.amount) return res.status(400).json({ error: 'Client and amount are required' });
  const today = new Date();
  const due = new Date(today.getTime() + 30 * 24 * 3600 * 1000);
  const invoice = {
    id: 'INV-' + Math.floor(1000 + Math.random() * 9000),
    client: body.client,
    issuedDate: today.toISOString().slice(0, 10),
    dueDate: (body.dueDate || due.toISOString().slice(0, 10)),
    amount: Number(body.amount),
    status: body.status || 'Pending',
  };
  const saved = await DBService.addToCollection('invoices', req.user.id, invoice);
  await NotificationsService.push(req.user.id, {
    title: `Invoice ${saved.id} created`,
    body: `${saved.client} • $${saved.amount.toLocaleString()}`,
    kind: 'success',
    link: '/billing',
  });
  res.status(201).json(saved);
});

app.patch('/api/billing/:id', requireAuth, async (req, res) => {
  const updated = await DBService.updateInCollection('invoices', req.user.id, req.params.id, req.body || {});
  if (!updated) return res.status(404).json({ error: 'Invoice not found' });
  if (req.body && req.body.status === 'Paid') {
    await NotificationsService.push(req.user.id, {
      title: `Invoice ${updated.id} marked paid`,
      body: `$${Number(updated.amount).toLocaleString()} from ${updated.client}`,
      kind: 'success',
      link: '/billing',
    });
  }
  res.json(updated);
});

app.delete('/api/alerts/:id', requireAuth, async (req, res) => {
  const ok = await DBService.removeFromCollection('alerts', req.user.id, req.params.id);
  res.json({ ok });
});

app.delete('/api/inbox/:id', requireAuth, async (req, res) => {
  const ok = await DBService.removeFromCollection('emails', req.user.id, req.params.id);
  res.json({ ok });
});

export default app;
