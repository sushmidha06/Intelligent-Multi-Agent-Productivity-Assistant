import { firestore } from './firebaseAdmin.js';

export class ApprovalService {
  static async create(userId, { tool, arguments: args, summary }) {
    const ref = firestore.collection('users').doc(userId).collection('approvals').doc();
    const approval = {
      id: ref.id,
      tool,
      arguments: args,
      summary,
      status: 'pending', // 'pending' | 'approved' | 'rejected'
      createdAt: new Date().toISOString()
    };
    await ref.set(approval);
    return approval;
  }

  static async list(userId) {
    // Single-field where + sort done in memory — avoids needing a composite
    // (status + createdAt) Firestore index. The approvals collection is
    // small (per-user, only pending items matter), so the cost is negligible.
    const snap = await firestore.collection('users').doc(userId).collection('approvals')
      .where('status', '==', 'pending')
      .get();
    const items = snap.docs.map(d => ({ id: d.id, ...d.data() }));
    items.sort((a, b) => String(b.createdAt || '').localeCompare(String(a.createdAt || '')));
    return items;
  }

  static async updateStatus(userId, approvalId, status) {
    const ref = firestore.collection('users').doc(userId).collection('approvals').doc(approvalId);
    await ref.update({ status, resolvedAt: new Date().toISOString() });
    
    if (status === 'approved') {
      const doc = await ref.get();
      return doc.data();
    }
    return null;
  }
}
