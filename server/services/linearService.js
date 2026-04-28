import axios from 'axios';
import { ConnectionsService } from './connectionsService.js';

export class LinearService {
  static async _request(userId, query, variables = {}) {
    const conn = await ConnectionsService.getDecryptedSecrets(userId, 'linear');
    if (!conn || !conn.secrets?.apiKey) {
      throw new Error('Linear not connected. Please provide your API Key in Integrations.');
    }

    const r = await axios.post('https://api.linear.app/graphql', { query, variables }, {
      headers: { 
        Authorization: conn.secrets.apiKey,
        'Content-Type': 'application/json'
      }
    });

    if (r.data.errors) {
      throw new Error(r.data.errors[0].message);
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
