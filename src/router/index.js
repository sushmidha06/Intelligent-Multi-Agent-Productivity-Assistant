import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import AuthView      from '../views/AuthView.vue'
import DashboardView from '../views/DashboardView.vue'
import InboxView     from '../views/InboxView.vue'
import ProjectsView  from '../views/ProjectsView.vue'
import BillingView   from '../views/BillingView.vue'
import SettingsView     from '../views/SettingsView.vue'
import IntegrationsView from '../views/IntegrationsView.vue'
import ExpensesView     from '../views/ExpensesView.vue'
import ApprovalsView    from '../views/ApprovalsView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/auth',     name: 'auth',      component: AuthView,      meta: { public: true } },
    { path: '/',         name: 'dashboard', component: DashboardView, meta: { requiresAuth: true } },
    { path: '/inbox',    name: 'inbox',     component: InboxView,     meta: { requiresAuth: true } },
    { path: '/projects', name: 'projects',  component: ProjectsView,  meta: { requiresAuth: true } },
    { path: '/billing',  name: 'billing',   component: BillingView,   meta: { requiresAuth: true } },
    { path: '/expenses', name: 'expenses',  component: ExpensesView,  meta: { requiresAuth: true } },
    { path: '/settings',     name: 'settings',     component: SettingsView,     meta: { requiresAuth: true } },
    { path: '/integrations', name: 'integrations', component: IntegrationsView, meta: { requiresAuth: true } },
    { path: '/approvals',    name: 'approvals',    component: ApprovalsView,    meta: { requiresAuth: true } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (auth.token && !auth.user) {
    await auth.hydrate()
  }
  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return { name: 'auth', query: { mode: 'signup' } }
  }
  if (to.name === 'auth' && auth.isAuthenticated) {
    return { name: 'dashboard' }
  }
})

export default router
