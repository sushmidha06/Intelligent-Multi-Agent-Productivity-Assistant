import { GoogleDocsService } from '../server/services/googleDocsService.js';
import { ConnectionsService } from '../server/services/connectionsService.js';
import { google } from 'googleapis';

// Mock ConnectionsService
ConnectionsService.getDecryptedSecrets = async (uid, provider) => {
  return {
    secrets: { clientSecret: 'fake_secret', refreshToken: 'fake_refresh' },
    metadata: { clientId: 'fake_client_id' }
  };
};

// Mock googleapis
google.docs = () => ({
  documents: {
    create: async ({ requestBody }) => {
      console.log('Document creation title:', requestBody.title);
      return { data: { documentId: 'mock_doc_id' } };
    },
    batchUpdate: async ({ documentId, requestBody }) => {
      console.log('Batch update requests count:', requestBody.requests.length);
      return { data: {} };
    }
  }
});

async function test() {
  const payload = {
    client_name: 'Acme Corp',
    project_name: 'Project X',
    summary: 'Test summary',
    scope: ['Item 1', 'Item 2'],
    estimated_budget: 1000,
    estimated_days: 5,
    start_date: '2025-01-01'
  };

  console.log('Testing GoogleDocsService.createProposal with snake_case payload...');
  const result = await GoogleDocsService.createProposal('user_123', payload);
  console.log('Result:', result);
}

test().catch(console.error);
