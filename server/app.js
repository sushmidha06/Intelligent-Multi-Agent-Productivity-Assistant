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
import { GoogleDocsService } from './services/googleDocsService.js';
import { TogglService } from './services/togglService.js';
import { LinearService } from './services/linearService.js';
import { ApprovalService } from './services/approvalService.js';
import { BotService } from './services/botService.js';
import { firestore } from './services/firebaseAdmin.js';
import webhookRoutes from './routes/webhooks.js';
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
// Capture the raw body during JSON parsing so webhook routes (Slack, Discord)
// can verify HMAC signatures over the original bytes. Without `verify`, the
// raw body is consumed by the parser and signature checks see an empty string
// — every signed webhook then 401s with "signature mismatch".
app.use(express.json({
  verify: (req, _res, buf) => { req.rawBody = buf; },
  limit: '1mb',
}));

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

app.use('/api/webhooks', webhookRoutes);

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

// /api/chat is defined further below with the correct service-token payload
// shape and a real proxy timeout. This block previously had a buggy duplicate
// that called signServiceToken(string, string) — shadowing the real handler
// and breaking auth on the Python side. Removed.

app.post('/api/chat/audio', requireAuth, express.raw({ type: 'audio/*', limit: '10mb' }), async (req, res) => {
  const aiUrl = process.env.PYTHON_AI_BASE_URL;
  if (!aiUrl) return res.status(503).json({ error: 'AI service not configured' });
  try {
    const token = signServiceToken({ userId: req.user.id, email: req.user.email });
    const r = await axios.post(`${aiUrl}/chat/audio`, req.body, {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': req.headers['content-type'] || 'audio/webm',
      },
      timeout: 58000,
    });
    res.json(r.data);
  } catch (e) {
    const status = e.response?.status || 502;
    res.status(status).json({ error: e.response?.data?.detail || e.message });
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
      timeout: 58000,
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

// --- Internal: Python AI service resolves a Slack/Discord platform user
// to our internal userId. Auth is the same shared-secret service-token; the
// `userId` claim is irrelevant here (the request is an unauthenticated lookup
// from the bot side, gated only by knowledge of JWT_SHARED_SECRET).
app.get('/api/internal/bot-mapping/:platform/:platformUserId', async (req, res) => {
  const auth = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  const claims = verifyServiceToken(auth);
  if (!claims) return res.status(401).json({ error: 'invalid service token' });
  try {
    const internalUserId = await BotService.getInternalUserId(req.params.platform, req.params.platformUserId);
    if (!internalUserId) return res.status(404).json({ error: 'not linked' });
    res.json({ internalUserId });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

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

app.get('/api/internal/integrations', async (req, res) => {
  const auth = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  const claims = verifyServiceToken(auth);
  if (!claims?.userId) return res.status(401).json({ error: 'invalid service token' });
  try {
    const list = await ConnectionsService.listConnections(claims.userId);
    res.json({ integrations: list.map(c => c.provider) });
  } catch (e) { res.status(500).json({ error: e.message }); }
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

// Internal: timesheet-driven invoice creation. The auto-billing tool calls this
// after gathering Toggl entries — same shape as POST /api/billing but auth'd via
// the service token so the Python AI service can act on the user's behalf.
app.post('/api/internal/billing', async (req, res) => {
  const auth = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  const claims = verifyServiceToken(auth);
  if (!claims?.userId) return res.status(401).json({ error: 'invalid service token' });
  const body = req.body || {};
  if (!body.client || !body.amount) return res.status(400).json({ error: 'client and amount required' });
  try {
    const today = new Date();
    const due = new Date(today.getTime() + 30 * 24 * 3600 * 1000);
    const invoice = {
      id: 'INV-' + Math.floor(1000 + Math.random() * 9000),
      client: String(body.client).slice(0, 200),
      issuedDate: today.toISOString().slice(0, 10),
      dueDate: body.dueDate || due.toISOString().slice(0, 10),
      amount: Number(body.amount),
      status: body.status || 'Pending',
      lineItems: Array.isArray(body.lineItems) ? body.lineItems.slice(0, 50) : [],
      source: 'agent-timesheet',
    };
    const saved = await DBService.addToCollection('invoices', claims.userId, invoice);
    await NotificationsService.push(claims.userId, {
      title: `AI created invoice ${saved.id}`,
      body: `${saved.client} • $${Number(saved.amount).toLocaleString()}`,
      kind: 'success',
      link: '/billing',
    });
    res.status(201).json(saved);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Internal: proactive agents push an in-app notification on behalf of the user.
// Auth: same service-token header as the other /internal endpoints.
app.post('/api/internal/notifications/push', async (req, res) => {
  const auth = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  const claims = verifyServiceToken(auth);
  if (!claims?.userId) return res.status(401).json({ error: 'invalid service token' });
  const { title, body, kind } = req.body || {};
  if (!title || !body) return res.status(400).json({ error: 'title and body required' });
  try {
    const id = await NotificationsService.push(claims.userId, {
      title: String(title).slice(0, 200),
      body: String(body).slice(0, 1000),
      kind: kind || 'info',
    });
    res.json({ id, ok: true });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

// Internal: list all user IDs so the cron can iterate. Cron-only — protected
// by the shared cron secret rather than the per-user service token.
app.get('/api/internal/users', async (req, res) => {
  const expected = process.env.CRON_SHARED_SECRET;
  const presented = req.headers['x-cron-secret'];
  if (!expected) return res.status(503).json({ error: 'CRON_SHARED_SECRET not configured' });
  if (presented !== expected) return res.status(401).json({ error: 'invalid cron secret' });
  try {
    const snap = await firestore.collection('users').get();
    const users = snap.docs.map(d => ({ id: d.id, email: d.data().email || null }));
    res.json({ users });
  } catch (e) { res.status(500).json({ error: e.message }); }
});
// Internal: trigger a sync of all project stats for all users.
// This is called by the cron job periodically.
app.post('/api/internal/projects/sync', async (req, res) => {
  const expected = process.env.CRON_SHARED_SECRET;
  const presented = req.headers['x-cron-secret'];
  if (!expected) return res.status(503).json({ error: 'CRON_SHARED_SECRET not configured' });
  if (presented !== expected) return res.status(401).json({ error: 'invalid cron secret' });

  try {
    const snap = await firestore.collection('users').get();
    const results = [];

    for (const userDoc of snap.docs) {
      const userId = userDoc.id;
      const projects = await DBService.getCollection('projects', userId);
      const ghConn = await ConnectionsService.getDecryptedSecrets(userId, 'github').catch(() => null);
      
      if (!ghConn?.secrets?.token) continue;

      const repos = projects.filter(p => p.repo).map(p => p.repo);
      if (repos.length === 0) continue;

      // For each repo, fetch the latest stats and save them.
      // We do this sequentially to avoid hitting GitHub rate limits too hard across users.
      for (const repo of [...new Set(repos)]) {
        try {
          const headers = { Authorization: `token ${ghConn.secrets.token}`, Accept: 'application/vnd.github+json' };
          const commits = await axios.get(`https://api.github.com/repos/${repo}/commits`, {
            headers, params: { per_page: 1 }, timeout: 10000
          });
          
          const pushedAt = commits.data?.[0]?.commit?.author?.date || null;
          const link = commits.headers.link;
          let count = 0;
          if (link && link.includes('rel="last"')) {
            const match = link.match(/&page=(\d+)>; rel="last"/);
            count = match ? parseInt(match[1]) : 1;
          } else {
            count = (commits.data || []).length;
          }

          // Update all projects using this repo
          for (const p of projects.filter(p => p.repo === repo)) {
            await DBService.updateInCollection('projects', userId, p.id, {
              commits: count,
              pushedAt,
              lastSyncedAt: new Date().toISOString()
            });
          }
        } catch (err) {
          console.error(`[internal-sync] failed for ${repo}:`, err.message);
        }
      }
      results.push({ userId, projectsCount: projects.length });
    }

    res.json({ ok: true, usersProcessed: results.length });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
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

// Internal: AI service creates a Google Doc proposal
app.post('/api/internal/documents/google-doc', async (req, res) => {
  const auth = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  const claims = verifyServiceToken(auth);
  if (!claims?.userId) return res.status(401).json({ error: 'invalid service token' });
  
  try {
    const result = await GoogleDocsService.createProposal(claims.userId, req.body);
    res.status(201).json(result);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Internal: AI service fetches Toggl time entries
app.get('/api/internal/timesheets/toggl', async (req, res) => {
  const auth = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  const claims = verifyServiceToken(auth);
  if (!claims?.userId) return res.status(401).json({ error: 'invalid service token' });
  
  const { start, end } = req.query;
  if (!start || !end) return res.status(400).json({ error: 'start and end dates required' });

  try {
    const entries = await TogglService.getTimeEntries(claims.userId, start, end);
    res.json({ entries });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Internal: AI service manages Linear issues
app.get('/api/internal/issues/linear/teams', async (req, res) => {
  const auth = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  const claims = verifyServiceToken(auth);
  if (!claims?.userId) return res.status(401).json({ error: 'invalid service token' });
  try {
    const teams = await LinearService.listTeams(claims.userId);
    res.json({ teams });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

app.post('/api/internal/issues/linear', async (req, res) => {
  const auth = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  const claims = verifyServiceToken(auth);
  if (!claims?.userId) return res.status(401).json({ error: 'invalid service token' });
  try {
    const issue = await LinearService.createIssue(claims.userId, req.body);
    res.status(201).json(issue);
  } catch (e) { res.status(500).json({ error: e.message }); }
});

// Internal: AI service requests an approval
app.post('/api/internal/approvals', async (req, res) => {
  const auth = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  const claims = verifyServiceToken(auth);
  if (!claims?.userId) return res.status(401).json({ error: 'invalid service token' });
  try {
    const approval = await ApprovalService.create(claims.userId, req.body);
    // Push notification to user
    await NotificationsService.push(claims.userId, {
      title: 'Action Required',
      body: `Sushmi wants to ${req.body.summary}. Please approve or reject.`,
      kind: 'warning'
    });
    res.status(201).json(approval);
  } catch (e) { res.status(500).json({ error: e.message }); }
});

// --- App data (all per-user) ---
app.get('/api/approvals', requireAuth, async (req, res) => {
  try {
    const list = await ApprovalService.list(req.user.id);
    res.json({ approvals: list });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

app.post('/api/approvals/:id/approve', requireAuth, async (req, res) => {
  try {
    const approval = await ApprovalService.updateStatus(req.user.id, req.params.id, 'approved');
    if (!approval) return res.status(404).json({ error: 'approval not found' });
    // Execute the tool via Python service. The token payload must be an
    // OBJECT — passing positional args silently produces a JWT without
    // userId, which the Python side then rejects (401 → 502 here).
    const token = signServiceToken({ userId: req.user.id, email: req.user.email });
    const r = await axios.post(`${process.env.PYTHON_AI_BASE_URL}/approvals/execute`, {
      tool: approval.tool,
      arguments: approval.arguments,
    }, {
      headers: { Authorization: `Bearer ${token}` },
      timeout: 58000,
    });
    res.json({ status: 'approved', result: r.data });
  } catch (e) {
    const status = e.response?.status || 500;
    res.status(status).json({ error: e.response?.data?.detail || e.message });
  }
});

app.post('/api/approvals/:id/reject', requireAuth, async (req, res) => {
  try {
    await ApprovalService.updateStatus(req.user.id, req.params.id, 'rejected');
    res.json({ status: 'rejected' });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

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
    let participationCount = null;
    let pushedAt = null;
    try {
      const part = await axios.get(`https://api.github.com/repos/${repo}/stats/participation`, { headers, timeout: 6000 });
      const all = (part.data && part.data.all) || [];
      if (all.length) {
        participationCount = all.reduce((s, n) => s + n, 0);
      }
    } catch { /* fall through */ }

    try {
      const commits = await axios.get(`https://api.github.com/repos/${repo}/commits`, {
        headers, params: { per_page: 100 }, timeout: 6000,
      });
      pushedAt = commits.data?.[0]?.commit?.author?.date || null;
      
      const link = commits.headers.link;
      let totalFromLink = null;
      if (link && link.includes('rel="last"')) {
        const match = link.match(/&page=(\d+)>; rel="last"/);
        if (match) totalFromLink = parseInt(match[1]) * 100;
      }

      // Final count strategy:
      // 1. If we have a Link header total (e.g. 500+ commits), use that.
      // 2. Otherwise, use the larger of (participation last 52w) or (the count of items on page 1).
      const page1Count = (commits.data || []).length;
      let finalCount = totalFromLink || Math.max(participationCount || 0, page1Count);

      return { repo, commits: finalCount, pushedAt };
    } catch { 
      return { repo, commits: participationCount, pushedAt };
    }
  }

  const stats = await Promise.all([...new Set(reposNeeded)].map(fetchCommitCount));
  const byRepo = new Map(stats.map(s => [s.repo, s]));
  const enriched = withSpent.map(p => {
    if (!p.repo) return p;
    const s = byRepo.get(p.repo);
    if (!s || s.commits == null) return p;
    
    const hasChanged = s.commits !== p.commits || s.pushedAt !== p.pushedAt;
    const now = new Date().toISOString();
    
    // Background save to Firestore so background agents see the updated stats
    if (hasChanged) {
      DBService.updateInCollection('projects', req.user.id, p.id, {
        commits: s.commits,
        pushedAt: s.pushedAt,
        lastSyncedAt: now,
      }).catch(err => console.error('[project-sync] background save failed:', err.message));
    }

    return { ...p, commits: s.commits, pushedAt: s.pushedAt || p.pushedAt, lastSyncedAt: hasChanged ? now : (p.lastSyncedAt || null) };
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

app.patch('/api/projects/:id', requireAuth, async (req, res) => {
  const updated = await DBService.updateInCollection('projects', req.user.id, req.params.id, req.body || {});
  if (!updated) return res.status(404).json({ error: 'Project not found' });
  res.json(updated);
});

app.delete('/api/projects/:id', requireAuth, async (req, res) => {
  const ok = await DBService.removeFromCollection('projects', req.user.id, req.params.id);
  res.json({ success: ok });
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

app.delete('/api/billing/:id', requireAuth, async (req, res) => {
  const ok = await DBService.removeFromCollection('invoices', req.user.id, req.params.id);
  res.json({ success: ok });
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
