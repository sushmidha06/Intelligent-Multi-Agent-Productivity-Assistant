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
    const internalUserId = await this.getInternalUserId(platform, platformUserId);
    if (!internalUserId) {
      return "Your account is not linked to Sushmi. Please go to the web app and use the /link command to connect your Slack/Discord account.";
    }

    // Call the Python AI service
    const token = signServiceToken(internalUserId);
    try {
      const r = await axios.post(`${process.env.PYTHON_AI_BASE_URL}/chat`, {
        message: messageText,
        history: [] // We could potentially store/retrieve bot history here
      }, {
        headers: { Authorization: `Bearer ${token}` }
      });

      return r.data.response;
    } catch (e) {
      console.error('Bot AI error:', e.message);
      return "Sorry, I encountered an error while processing your request.";
    }
  }
}
