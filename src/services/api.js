import axios from 'axios';

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:3001/api',
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('sushmi_token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

export const authService = {
  signUp:          (payload) => api.post('/auth/signup', payload).then(r => r.data),
  signIn:          (payload) => api.post('/auth/signin', payload).then(r => r.data),
  me:              ()        => api.get('/auth/me').then(r => r.data),
  logout:          ()        => api.post('/auth/logout').then(r => r.data),
  updateProfile:   (payload) => api.patch('/auth/profile', payload).then(r => r.data),
  changePassword:  (payload) => api.post('/auth/change-password', payload).then(r => r.data),
  updatePreferences: (payload) => api.patch('/auth/preferences', payload).then(r => r.data),
  deleteAccount:   ()        => api.delete('/auth/account').then(r => r.data),
};

export const integrationsService = {
  list:       ()                        => api.get('/integrations').then(r => r.data),
  connect:    (provider, secrets, meta) => api.put(`/integrations/${provider}`, { secrets, metadata: meta || {} }).then(r => r.data),
  disconnect: (provider)                => api.delete(`/integrations/${provider}`).then(r => r.data),
  githubRepos: ()                       => api.get('/integrations/github/repos').then(r => r.data),
};

export const chatService = {
  async send(message, history = []) {
    const r = await api.post('/chat', { message, history });
    return r.data;
  },
  async sendAudio(audioBlob) {
    const r = await api.post('/chat/audio', audioBlob, {
      headers: { 'Content-Type': audioBlob.type || 'audio/webm' }
    });
    return r.data;
  },
  async warmup() {
    return api.get('/chat/warmup').then(r => r.data).catch(() => null);
  }
};

export const notificationsService = {
  list:        ()     => api.get('/notifications').then(r => r.data),
  markRead:    (id)   => api.post(`/notifications/${id}/read`).then(r => r.data),
  markAllRead: ()     => api.post('/notifications/read-all').then(r => r.data),
  remove:      (id)   => api.delete(`/notifications/${id}`).then(r => r.data),
  clear:       ()     => api.delete('/notifications').then(r => r.data),
};

export const dashboardService = {
  getDashboard: () => api.get('/dashboard').then(r => r.data),
};

export const inboxService = {
  getEmails:        ()             => api.get('/inbox').then(r => r.data),
  getGmail:         ()             => api.get('/inbox/gmail').then(r => r.data),
  dismiss:          (id)           => api.delete(`/inbox/${id}`).then(r => r.data),
  moveEmail:        (id, folder)   => api.patch(`/inbox/email/${id}/folder`, { folder }).then(r => r.data),
  deleteEmail:      (id)           => api.delete(`/inbox/email/${id}`).then(r => r.data),
  restoreEmail:     (id)           => api.post(`/inbox/email/${id}/restore`).then(r => r.data),
  extractExpense:   (id)           => api.post(`/inbox/email/${id}/extract-expense`).then(r => r.data),
  draftReply:       (id)           => api.post(`/inbox/email/${id}/draft-reply`).then(r => r.data),
  syncRag:          (opts = {})    => api.post('/inbox/sync-rag', opts).then(r => r.data),
  syncRagStatus:    ()             => api.get('/inbox/sync-rag/status').then(r => r.data),
};

export const projectService = {
  getProjects: ()        => api.get('/projects').then(r => r.data),
  createProject: (data)  => api.post('/projects', data).then(r => r.data),
};

export const billingService = {
  getInvoices:   ()              => api.get('/billing').then(r => r.data),
  createInvoice: (data)          => api.post('/billing', data).then(r => r.data),
  updateInvoice: (id, patch)     => api.patch(`/billing/${id}`, patch).then(r => r.data),
};

export const expensesService = {
  list:    (projectId)         => api.get('/expenses', { params: projectId ? { projectId } : {} }).then(r => r.data),
  create:  (data)              => api.post('/expenses', data).then(r => r.data),
  update:  (id, patch)         => api.patch(`/expenses/${id}`, patch).then(r => r.data),
  remove:  (id)                => api.delete(`/expenses/${id}`).then(r => r.data),
};

export const alertService = {
  dismiss: (id) => api.delete(`/alerts/${id}`).then(r => r.data),
};

export default api;
