import { google } from 'googleapis';
import { ConnectionsService } from './connectionsService.js';

export class GoogleDocsService {
  static async createProposal(userId, payload) {
    const conn = await ConnectionsService.getDecryptedSecrets(userId, 'google_docs');
    if (!conn || !conn.secrets?.refreshToken) {
      throw new Error('Google Docs not connected. Please provide Client ID, Secret, and Refresh Token in Integrations.');
    }

    // AI agent sends snake_case; frontend sends camelCase. Handle both.
    const clientName = payload.clientName || payload.client_name || 'Client';
    const projectName = payload.projectName || payload.project_name || 'Project';
    const summary = payload.summary || '';
    const scope = payload.scope || [];
    const budget = payload.estimated_budget || payload.budget || 0;
    const days = payload.estimated_days || payload.days || 0;
    const startDate = payload.start_date || payload.startDate || new Date().toISOString().split('T')[0];

    // Client ID might be in secrets (encrypted) or metadata (plain text) depending on UI version
    const secrets = conn.secrets || {};
    const metadata = conn.metadata || {};
    const clientId = secrets.clientId || metadata.clientId;
    const clientSecret = secrets.clientSecret;
    const refreshToken = secrets.refreshToken;

    if (!clientId || !clientSecret) {
      throw new Error('Google Docs configuration is incomplete (missing Client ID or Secret).');
    }

    const auth = new google.auth.OAuth2(clientId, clientSecret);
    auth.setCredentials({ refresh_token: refreshToken });

    const docs = google.docs({ version: 'v1', auth });

    // 1. Create a blank document
    const title = `Proposal: ${projectName} - ${clientName}`;
    let documentId;
    try {
      const doc = await docs.documents.create({ requestBody: { title } });
      documentId = doc.data.documentId;
    } catch (e) {
      console.error('[google-docs] document creation failed:', e.response?.data || e.message);
      if (e.message?.includes('invalid_grant') || e.response?.status === 400) {
        throw new Error('Google OAuth refresh failed. Your Refresh Token might be invalid or expired. Please reconnect in Integrations.');
      }
      throw new Error(`Google API Error: ${e.message}`);
    }

    // 2. Build the batchUpdate requests for formatting
    const requests = [
      // Title
      { insertText: { location: { index: 1 }, text: `${title}\n\n` } },
      { updateParagraphStyle: { range: { startIndex: 1, endIndex: title.length + 1 }, paragraphStyle: { namedStyleType: 'TITLE' }, fields: 'namedStyleType' } },
      
      // Meta info
      { insertText: { location: { index: title.length + 3 }, text: `Prepared for: ${clientName}\nDate: ${new Date().toLocaleDateString()}\n\n` } },
      
      // Summary Section
      { insertText: { endOfSegmentLocation: {}, text: `Executive Summary\n` } },
      { insertText: { endOfSegmentLocation: {}, text: `${summary}\n\n` } },
      
      // Scope Section
      { insertText: { endOfSegmentLocation: {}, text: `Scope of Work\n` } },
    ];

    // Add scope items
    if (Array.isArray(scope)) {
      for (const item of scope) {
        if (item) requests.push({ insertText: { endOfSegmentLocation: {}, text: `• ${item}\n` } });
      }
    }
    requests.push({ insertText: { endOfSegmentLocation: {}, text: `\n` } });

    // Investment Section
    const budgetNum = Number(budget) || 0;
    const daysNum = Number(days) || 0;
    const investmentText = `Investment & Timeline\nTotal Investment: $${budgetNum.toLocaleString()}\nEstimated Duration: ${daysNum} Business Days\nProposed Start Date: ${startDate}\n`;
    requests.push({ insertText: { endOfSegmentLocation: {}, text: investmentText } });

    // Execute the updates
    try {
      await docs.documents.batchUpdate({
        documentId,
        requestBody: { requests }
      });
    } catch (e) {
      console.error('[google-docs] document formatting failed:', e.response?.data || e.message);
      throw new Error(`Google API Formatting Error: ${e.message}`);
    }

    return {
      documentId,
      url: `https://docs.google.com/document/d/${documentId}/edit`,
      title
    };
  }
}
