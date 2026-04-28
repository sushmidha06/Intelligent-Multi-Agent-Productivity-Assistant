import axios from 'axios';
import { firestore } from './firebaseAdmin.js';
import { ConnectionsService } from './connectionsService.js';
import { signServiceToken } from './jwtService.js';

export class BotService {
  /**
   * Links a platform userId (Slack/Discord) to our internal userId.
   */
  static async linkAccount(platform, platformUserId, internalUserId) {
    await firestore.collection('botMappings').doc(`${platform}_${platformUserId}`).set({
      internalUserId,
      platform,
      platformUserId,
      linkedAt: new Date().toISOString()
    });
  }

  /**
   * Resolves a platform userId to our internal userId.
   */
  static async getInternalUserId(platform, platformUserId) {
    const docId = `${platform}_${platformUserId}`;
    const ref = firestore.collection('botMappings').doc(docId);
    const doc = await ref.get();
    // Diagnostic — remove once /link flow is built. Confirms which project +
    // doc id firebase-admin actually queries against.
    console.log('[bot-mapping] lookup', {
      docId,
      path: ref.path,
      projectId: ref.firestore?.app?.options?.projectId,
      exists: doc.exists,
      hasInternalUserId: !!doc.data()?.internalUserId,
    });
    return doc.exists ? doc.data().internalUserId : null;
  }

  /**
   * Forwards a message from a bot to the AI Orchestrator.
   */
  static async processMessage(platform, platformUserId, messageText) {
    let internalUserId = await this.getInternalUserId(platform, platformUserId);

    // Handle manual linking: "link user@example.com"
    const text = (messageText || '').trim();
    if (text.toLowerCase().startsWith('link ')) {
      const email = text.slice(5).trim().toLowerCase();
      const snap = await firestore.collection('users').where('email', '==', email).limit(1).get();
      if (!snap.empty) {
        const userId = snap.docs[0].id;
        await this.linkAccount(platform, platformUserId, userId);
        return `Successfully linked your ${platform} account to ${email}. You can now ask me to list projects, draft emails, etc.`;
      } else {
        return `Could not find a Sushmi account with email "${email}". Make sure you've signed up in the web app first.`;
      }
    }

    if (!internalUserId) {
      return "Your account is not linked to Sushmi. Please type `link your-email@example.com` to connect your Slack/Discord account to your Sushmi workspace.";
    }

    // Call the Python AI service. signServiceToken expects an OBJECT payload
    // — passing a string here silently produces a JWT without `userId`, which
    // the Python side then rejects (and the bot user sees a generic error).
    const token = signServiceToken({ userId: internalUserId });
    try {
      const r = await axios.post(`${process.env.PYTHON_AI_BASE_URL}/chat`, {
        message: messageText,
        history: [],
      }, {
        headers: { Authorization: `Bearer ${token}` },
        timeout: 58000,
      });
      return r.data.response;
    } catch (e) {
      console.error('Bot AI error:', e.response?.data || e.message);
      return "Sorry, I encountered an error while processing your request.";
    }
  }
}
