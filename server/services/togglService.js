import axios from 'axios';
import { ConnectionsService } from './connectionsService.js';

export class TogglService {
  /**
   * Fetches time entries for a user within a specific date range.
   */
  static async getTimeEntries(userId, startDate, endDate) {
    const conn = await ConnectionsService.getDecryptedSecrets(userId, 'toggl');
    if (!conn || !conn.secrets?.apiToken) {
      throw new Error('Toggl Track not connected. Please provide your API Token in Integrations.');
    }

    const auth = Buffer.from(`${conn.secrets.apiToken}:api_token`).toString('base64');
    
    // 1. Get Me to find the default workspace_id if not known
    const me = await axios.get('https://api.track.toggl.com/api/v9/me', {
      headers: { Authorization: `Basic ${auth}` }
    });
    const workspaceId = me.data.default_workspace_id;

    // 2. Fetch time entries
    // Toggl API expects ISO-8601 strings
    const r = await axios.get('https://api.track.toggl.com/api/v9/me/time_entries', {
      headers: { Authorization: `Basic ${auth}` },
      params: {
        start_date: new Date(startDate).toISOString(),
        end_date: new Date(endDate).toISOString(),
      }
    });

    // 3. Map to a clean format for the AI
    return r.data.map(entry => ({
      id: entry.id,
      description: entry.description || '(no description)',
      duration: entry.duration, // in seconds
      start: entry.start,
      stop: entry.stop,
      tags: entry.tags || [],
      project_id: entry.project_id
    }));
  }
}
