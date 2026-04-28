import axios from 'axios';
import { ConnectionsService } from './connectionsService.js';

export class LinearService {
  static async _request(userId, query, variables = {}) {
    const conn = await ConnectionsService.getDecryptedSecrets(userId, 'linear');
    if (!conn || !conn.secrets?.apiKey) {
      throw new Error('Linear not connected. Please provide your API Key in Integrations.');
    }

    // Linear personal API keys use the raw key as the Authorization value
    // (no "Bearer " prefix). OAuth access tokens DO need "Bearer ". Detect
    // and normalise so users can paste either form.
    const raw = String(conn.secrets.apiKey).trim();
    const authHeader = raw.startsWith('lin_oauth_') ? `Bearer ${raw}` : raw;

    let r;
    try {
      r = await axios.post('https://api.linear.app/graphql', { query, variables }, {
        headers: {
          Authorization: authHeader,
          'Content-Type': 'application/json',
        },
        timeout: 15000,
      });
    } catch (e) {
      // axios throws on non-2xx; surface Linear's actual error body.
      const status = e.response?.status;
      const linearMsg = e.response?.data?.errors?.[0]?.message
        || JSON.stringify(e.response?.data || {}).slice(0, 300)
        || e.message;
      throw new Error(`Linear API ${status || 'request'} failed: ${linearMsg}`);
    }

    if (r.data?.errors?.length) {
      const first = r.data.errors[0];
      const extra = first.extensions?.userPresentableMessage || first.extensions?.code || '';
      throw new Error(`Linear: ${first.message}${extra ? ` (${extra})` : ''}`);
    }
    return r.data.data;
  }

  static async listTeams(userId) {
    const query = `{ teams { nodes { id name key } } }`;
    const data = await this._request(userId, query);
    return data.teams.nodes;
  }

  static async createIssue(userId, { title, description, teamId, priority = 0 }) {
    // If teamId is missing, default to the first team
    let finalTeamId = teamId;
    if (!finalTeamId) {
      const teams = await this.listTeams(userId);
      if (!teams.length) throw new Error('No teams found in your Linear account');
      finalTeamId = teams[0].id;
    }

    const query = `
      mutation CreateIssue($title: String!, $description: String, $teamId: String!, $priority: Float) {
        issueCreate(input: { title: $title, description: $description, teamId: $teamId, priority: $priority }) {
          success
          issue { id identifier url title }
        }
      }
    `;
    const data = await this._request(userId, query, { title, description, teamId: finalTeamId, priority });
    return data.issueCreate.issue;
  }

  static async searchIssues(userId, searchTerm) {
    const query = `
      query Search($term: String!) {
        searchIssues(term: $term) {
          nodes { id identifier title state { name } }
        }
      }
    `;
    const data = await this._request(userId, query, { term: searchTerm });
    return data.searchIssues.nodes;
  }
}
