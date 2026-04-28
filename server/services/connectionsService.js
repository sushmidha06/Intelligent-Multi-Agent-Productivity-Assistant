import { firestore } from './firebaseAdmin.js';
import { encryptObject, decryptObject } from './cryptoService.js';

// Per-tenant integration credentials live at:
//   users/{uid}/connections/{provider}
//
// All `secrets` are AES-256-GCM encrypted at rest. `metadata` is plaintext
// (display info only, e.g. "connectedAt", "githubUsername").

function col(uid) {
  return firestore.collection('users').doc(uid).collection('connections');
}

const SUPPORTED = new Set(['github', 'gmail', 'razorpay', 'calendar', 'google_docs', 'toggl', 'linear', 'slack', 'discord']);

function ensureSupported(provider) {
  if (!SUPPORTED.has(provider)) {
    const err = new Error(`Unsupported provider: ${provider}`);
    err.status = 400;
    throw err;
  }
}

export const ConnectionsService = {
  async listStatus(uid) {
    const snap = await col(uid).get();
    const map = new Map(snap.docs.map(d => [d.id, d.data()]));
    return [...SUPPORTED].map(p => {
      const c = map.get(p);
      return {
        provider: p,
        connected: !!c,
        connectedAt: c?.connectedAt || null,
        metadata: c?.metadata || null,
      };
    });
  },

  async connect(uid, provider, { secrets, metadata }) {
    ensureSupported(provider);
    if (!secrets || typeof secrets !== 'object') {
      const err = new Error('secrets payload required'); err.status = 400; throw err;
    }
    await col(uid).doc(provider).set({
      provider,
      secrets: encryptObject(secrets),
      metadata: metadata || {},
      connectedAt: new Date().toISOString(),
    });
    return { provider, connected: true, connectedAt: new Date().toISOString(), metadata: metadata || {} };
  },

  async disconnect(uid, provider) {
    ensureSupported(provider);
    await col(uid).doc(provider).delete().catch(() => {});
    return { provider, connected: false };
  },

  // Internal: only the Python AI service should call this (via JWT-protected endpoint).
  async getDecryptedSecrets(uid, provider) {
    ensureSupported(provider);
    const doc = await col(uid).doc(provider).get();
    if (!doc.exists) return null;
    const data = doc.data();
    return {
      provider,
      secrets: decryptObject(data.secrets),
      metadata: data.metadata || {},
    };
  },
};
