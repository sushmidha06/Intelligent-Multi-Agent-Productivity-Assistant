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
    const snap = await firestore.collection('users').doc(userId).collection('approvals')
      .where('status', '==', 'pending')
      .orderBy('createdAt', 'desc')
      .get();
    return snap.docs.map(d => ({ id: d.id, ...d.data() }));
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
