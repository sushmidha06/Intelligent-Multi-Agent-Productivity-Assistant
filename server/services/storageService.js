import { bucket } from './firebaseAdmin.js';

export class StorageService {
  /**
   * Uploads a buffer (PDF) to the user's dedicated folder in Firebase Storage.
   * Returns a signed URL that expires in 7 days.
   */
  static async uploadDocument(userId, fileName, buffer, contentType = 'application/pdf') {
    if (!bucket) throw new Error('Firebase Storage bucket not configured');
    
    // Scoped to the user's folder
    const path = `users/${userId}/documents/${Date.now()}_${fileName}`;
    const file = bucket.file(path);

    await file.save(buffer, {
      metadata: { contentType },
      resumable: false,
    });

    // Generate a signed URL for the user to view/download the proposal.
    // Expiring in 7 days is safe for a proposal.
    const [url] = await file.getSignedUrl({
      action: 'read',
      expires: Date.now() + 7 * 24 * 60 * 60 * 1000,
    });

    return { url, path };
  }
}
