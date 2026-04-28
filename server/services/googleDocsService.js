import { google } from 'googleapis';
import { ConnectionsService } from './connectionsService.js';

export class GoogleDocsService {
  static async createProposal(userId, { clientName, projectName, summary, scope, budget, days, startDate }) {
    const conn = await ConnectionsService.getDecryptedSecrets(userId, 'google_docs');
    if (!conn || !conn.secrets?.refreshToken) {
      throw new Error('Google Docs not connected. Please provide Client ID, Secret, and Refresh Token in Integrations.');
    }

    const auth = new google.auth.OAuth2(
      conn.secrets.clientId,
      conn.secrets.clientSecret
    );
    auth.setCredentials({ refresh_token: conn.secrets.refreshToken });

    const docs = google.docs({ version: 'v1', auth });
    const drive = google.drive({ version: 'v3', auth });

    // 1. Create a blank document
    const title = `Proposal: ${projectName} - ${clientName}`;
    const doc = await docs.documents.create({ requestBody: { title } });
    const documentId = doc.data.documentId;

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
    for (const item of scope) {
      requests.push({ insertText: { endOfSegmentLocation: {}, text: `• ${item}\n` } });
    }
    requests.push({ insertText: { endOfSegmentLocation: {}, text: `\n` } });

    // Investment Section
    const investmentText = `Investment & Timeline\nTotal Investment: $${budget.toLocaleString()}\nEstimated Duration: ${days} Business Days\nProposed Start Date: ${startDate}\n`;
    requests.push({ insertText: { endOfSegmentLocation: {}, text: investmentText } });

    // Execute the updates
    await docs.documents.batchUpdate({
      documentId,
      requestBody: { requests }
    });

    return {
      documentId,
      url: `https://docs.google.com/document/d/${documentId}/edit`,
      title
    };
  }
}
