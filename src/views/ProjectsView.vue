<script setup>
import { ref, reactive, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { FolderOpen, GitBranch, Calendar, TrendingUp, CheckCircle2, X, Loader2, AlertCircle, RefreshCw, FileText, Edit3, Trash2 } from 'lucide-vue-next'
import { projectService, integrationsService } from '../services/api'
import { formatMoney, currencySymbol } from '../services/format'
import { useAppStore } from '../stores/app'

const appStore = useAppStore()

// One-click handler that drops a pre-filled prompt into the chat drawer.
// The drawer auto-sends, the agent gathers context (RAG + calendar) and
// then routes through documents__generate_proposal which is approval-gated.
function draftProposalFor(project) {
  const client = project?.client || project?.name || 'this client'
  const name = project?.name || 'the engagement'
  const prompt = (
    `Draft a proposal for ${client} for the project "${name}". ` +
    `Use knowledge_base__search_knowledge to find similar past projects ` +
    `for budget/timeline estimates, then call documents__generate_proposal.`
  )
  appStore.openChatWith(prompt)
}

const projects = ref([])
const loading = ref(true)
const showModal = ref(false)
const submitting = ref(false)
const error = ref('')
const editingId = ref(null)

const githubLoading = ref(false)
const githubError = ref('')
const githubRepos = ref([])
const githubConnected = ref(true)

const form = reactive({
  name: '',
  client: '',
  status: 'On Track',
  health: 90,
  budget: 5000,
  daysLeft: 30,
  repo: null,
})

async function load() {
  loading.value = true
  try {
    projects.value = await projectService.getProjects()
  } catch (err) {
    console.error('Failed to fetch projects', err)
  } finally {
    loading.value = false
  }
}

onMounted(load)

function openModal() {
  editingId.value = null
  Object.assign(form, { name: '', client: '', status: 'On Track', health: 90, budget: 5000, daysLeft: 30, repo: null })
  error.value = ''
  showModal.value = true
  loadGithubRepos()
}

function editProject(p) {
  editingId.value = p.id
  Object.assign(form, { 
    name: p.name, 
    client: p.client, 
    status: p.status, 
    health: p.health, 
    budget: p.budget, 
    daysLeft: p.daysLeft, 
    repo: p.repo 
  })
  error.value = ''
  showModal.value = true
  loadGithubRepos()
}

async function removeProject(id) {
  if (!confirm('Are you sure you want to delete this project?')) return
  try {
    await projectService.removeProject(id)
    projects.value = projects.value.filter(p => p.id !== id)
  } catch (err) {
    alert('Failed to delete project')
  }
}

async function loadGithubRepos() {
  githubLoading.value = true
  githubError.value = ''
  try {
    const data = await integrationsService.githubRepos()
    githubRepos.value = data.repos || []
    githubConnected.value = true
  } catch (e) {
    if (e?.response?.status === 404) {
      githubConnected.value = false
      githubError.value = ''
    } else {
      githubError.value = e?.response?.data?.error || e.message || 'Could not load GitHub repos'
    }
    githubRepos.value = []
  } finally {
    githubLoading.value = false
  }
}

function pickRepo(fullName) {
  form.repo = fullName
  if (!form.name.trim()) form.name = fullName.split('/').pop()
}

async function saveProject() {
  error.value = ''
  if (!form.name.trim() || !form.client.trim()) {
    error.value = 'Name and client are required.'
    return
  }
  submitting.value = true
  try {
    if (editingId.value) {
      const updated = await projectService.updateProject(editingId.value, { ...form })
      const idx = projects.value.findIndex(p => p.id === editingId.value)
      if (idx !== -1) projects.value[idx] = updated
    } else {
      const created = await projectService.createProject({ ...form })
      projects.value = [created, ...projects.value]
    }
    showModal.value = false
  } catch (e) {
    error.value = e?.response?.data?.error || e.message || 'Failed to save project.'
  } finally {
    submitting.value = false
  }
}

function healthColor(h) {
  if (h >= 80) return { bar: 'bg-emerald-500', text: 'text-emerald-400' }
  if (h >= 60) return { bar: 'bg-amber-500',   text: 'text-amber-400'   }
  return         { bar: 'bg-rose-500',         text: 'text-rose-400'    }
}

function statusConfig(s) {
  if (s === 'On Track') return 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
  if (s === 'At Risk')  return 'bg-amber-500/15 text-amber-400 border-amber-500/30'
  return 'bg-rose-500/15 text-rose-400 border-rose-500/30'
}
</script>

<template>
  <div class="space-y-6 animate-fadeIn">
    <div class="flex items-center justify-between">
      <div>
        <h2 class="text-2xl font-bold text-white">Projects</h2>
        <p class="text-sm text-slate-400 mt-1">Real-time health across all active engagements</p>
      </div>
      <div class="flex items-center gap-2">
        <button
          @click="load"
          :disabled="loading"
          class="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 transition-all text-sm font-semibold text-slate-300 border border-slate-700 disabled:opacity-50"
        >
          <RefreshCw :size="14" :class="loading ? 'animate-spin' : ''" /> Sync
        </button>
        <button
          @click="openModal"
          class="flex items-center gap-2 px-4 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 transition-all text-sm font-semibold text-white shadow-lg shadow-cyan-500/20"
        >
          <FolderOpen :size="14" /> New Project
        </button>
      </div>
    </div>

    <div v-if="loading" class="text-sm text-slate-500">Loading projects…</div>
    <div v-else-if="projects.length === 0" class="rounded-2xl border border-slate-800 bg-slate-900 p-10 text-center text-slate-500 text-sm">
      No projects yet — create one to get started.
    </div>

    <div class="grid gap-5">
      <div v-for="p in projects" :key="p.id" class="p-6 rounded-2xl bg-slate-900 border border-slate-800 hover:border-slate-600 transition-all">
        <div class="flex items-start justify-between mb-4">
          <div>
            <h3 class="font-bold text-white text-lg">{{ p.name }}</h3>
            <p class="text-sm text-slate-400">{{ p.client }}</p>
          </div>
          <div class="flex items-center gap-2">
            <button
              @click="draftProposalFor(p)"
              title="Draft a proposal in Google Docs (uses RAG over past projects)"
              class="flex items-center gap-1 text-xs px-2.5 py-1 rounded-full border border-violet-500/30 text-violet-300 hover:bg-violet-500/10 transition-all"
            >
              <FileText :size="12" /> Draft Proposal
            </button>
            <button @click="editProject(p)" class="p-2 rounded-lg text-slate-500 hover:text-white hover:bg-white/5 transition-all" title="Edit">
              <Edit3 :size="16" />
            </button>
            <button @click="removeProject(p.id)" class="p-2 rounded-lg text-slate-500 hover:text-rose-400 hover:bg-rose-500/10 transition-all" title="Delete">
              <Trash2 :size="16" />
            </button>
            <span :class="['text-xs px-2.5 py-1 rounded-full border font-semibold', statusConfig(p.status)]">{{ p.status }}</span>
          </div>
        </div>

        <div class="mb-4">
          <div class="flex justify-between text-xs mb-1.5">
            <span class="text-slate-400">Project health</span>
            <span :class="['font-bold', healthColor(p.health).text]">{{ p.health }}%</span>
          </div>
          <div class="h-2 w-full bg-slate-800 rounded-full overflow-hidden">
            <div :class="['h-full rounded-full transition-all', healthColor(p.health).bar]" :style="{ width: p.health + '%' }"></div>
          </div>
        </div>

        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
          <div class="p-3 rounded-xl bg-slate-800">
            <p class="text-lg font-bold text-white">{{ p.commits }}</p>
            <p class="text-[10px] text-slate-500 flex items-center justify-center gap-1 mt-0.5"><GitBranch :size="10" /> Commits</p>
            <p v-if="p.lastSyncedAt" class="text-[8px] text-slate-600 mt-1 uppercase tracking-tighter">Synced {{ new Date(p.lastSyncedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }}</p>
          </div>
          <div class="p-3 rounded-xl bg-slate-800">
            <p :class="['text-lg font-bold', p.daysLeft < 0 ? 'text-rose-400' : 'text-white']">
              {{ p.daysLeft < 0 ? Math.abs(p.daysLeft) + 'd late' : p.daysLeft + 'd' }}
            </p>
            <p class="text-xs text-slate-500 flex items-center justify-center gap-1 mt-0.5"><Calendar :size="10" /> Deadline</p>
          </div>
          <div class="p-3 rounded-xl bg-slate-800">
            <p class="text-lg font-bold text-white">{{ formatMoney(p.spent) }}</p>
            <p class="text-xs text-slate-500 flex items-center justify-center gap-1 mt-0.5"><TrendingUp :size="10" /> Spent</p>
          </div>
          <div class="p-3 rounded-xl bg-slate-800">
            <p class="text-lg font-bold text-emerald-400">{{ formatMoney(p.budget) }}</p>
            <p class="text-xs text-slate-500 flex items-center justify-center gap-1 mt-0.5"><CheckCircle2 :size="10" /> Budget</p>
          </div>
        </div>
      </div>
    </div>

    <Teleport to="body">
    <div v-if="showModal" class="fixed inset-0 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4" style="z-index: 2147483000" @click.self="showModal = false">
      <div class="w-full max-w-2xl flex flex-col rounded-2xl border shadow-2xl overflow-hidden" style="background: var(--color-surface); border-color: var(--color-border); max-height: 90vh; height: auto">
        <header class="shrink-0 px-6 py-4 border-b flex items-center justify-between" style="border-color: var(--color-border)">
          <div>
            <h3 class="text-base font-bold text-white">{{ editingId ? 'Edit project' : 'Create a new project' }}</h3>
            <p class="text-[11px] text-slate-500 mt-0.5">Link a GitHub repository so your AI agents can track activity automatically.</p>
          </div>
          <button @click="showModal = false" class="w-8 h-8 rounded-lg flex items-center justify-center text-slate-500 hover:text-white hover:bg-white/5"><X :size="16" /></button>
        </header>

        <form @submit.prevent="saveProject" class="flex-1 min-h-0 overflow-y-auto px-6 py-5 space-y-5">

          <section>
            <div class="flex items-center justify-between mb-2">
              <label class="text-[11px] font-bold uppercase tracking-widest text-slate-400 flex items-center gap-1.5">
                <GitBranch :size="11" /> 1. Link a repository
              </label>
              <button type="button" v-if="githubConnected" @click="loadGithubRepos" :disabled="githubLoading" class="text-[11px] text-slate-500 hover:text-white flex items-center gap-1 disabled:opacity-50">
                <RefreshCw :size="11" :class="githubLoading ? 'animate-spin' : ''" /> Refresh
              </button>
            </div>

            <div v-if="!githubConnected" class="p-4 rounded-xl border border-amber-500/30 bg-amber-500/5 flex items-start gap-3">
              <AlertCircle :size="16" class="text-amber-400 mt-0.5 shrink-0" />
              <div class="flex-1 text-xs text-slate-300">
                <p class="font-medium text-amber-200 mb-1">GitHub isn't connected yet.</p>
                <p>Connect it in <RouterLink to="/integrations" class="text-violet-400 hover:underline">Integrations</RouterLink> to pick from your repositories. You can still create a project without one.</p>
              </div>
            </div>

            <div v-else-if="githubLoading" class="p-4 rounded-xl border border-slate-700 bg-slate-900/40 flex items-center gap-2 text-xs text-slate-400">
              <Loader2 :size="14" class="animate-spin" /> Loading your repositories…
            </div>

            <div v-else-if="githubError" class="p-3 rounded-xl border border-rose-500/30 bg-rose-500/5 text-xs text-rose-300">{{ githubError }}</div>

            <div v-else-if="githubRepos.length === 0" class="p-4 rounded-xl border border-slate-700 bg-slate-900/40 text-xs text-slate-500">No repositories found in your GitHub account.</div>

            <div v-else class="rounded-xl border border-slate-700 bg-slate-900/40 max-h-56 overflow-y-auto divide-y divide-slate-800">
              <button
                v-for="r in githubRepos"
                :key="r.full_name"
                type="button"
                @click="pickRepo(r.full_name)"
                :class="['w-full text-left px-4 py-2.5 flex items-start gap-3 hover:bg-white/5 transition-colors',
                  form.repo === r.full_name ? 'bg-violet-500/10' : '']"
              >
                <GitBranch :size="14" :class="form.repo === r.full_name ? 'text-violet-400 mt-0.5' : 'text-slate-500 mt-0.5'" />
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2">
                    <p class="text-sm font-mono font-semibold text-white truncate">{{ r.full_name }}</p>
                    <span v-if="r.private" class="text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">private</span>
                  </div>
                  <p v-if="r.description" class="text-[11px] text-slate-500 truncate mt-0.5">{{ r.description }}</p>
                </div>
                <span v-if="r.language" class="text-[10px] text-slate-500 shrink-0 mt-1">{{ r.language }}</span>
              </button>
            </div>

            <p v-if="form.repo" class="text-[11px] text-slate-500 mt-2 flex items-center gap-1.5">
              <CheckCircle2 :size="11" class="text-emerald-400" /> Linked to <span class="font-mono text-slate-300">{{ form.repo }}</span>
            </p>
          </section>

          <section>
            <label class="text-[11px] font-bold uppercase tracking-widest text-slate-400 mb-3 block">2. Project details</label>
            <div class="grid grid-cols-2 gap-3">
              <div class="col-span-2">
                <label class="block text-xs text-slate-400 mb-1.5">Project name</label>
                <input v-model="form.name" type="text" placeholder="Payments Platform v2"
                  class="w-full px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white placeholder-slate-600 focus:outline-none focus:border-cyan-500" />
              </div>
              <div class="col-span-2">
                <label class="block text-xs text-slate-400 mb-1.5">Client</label>
                <input v-model="form.client" type="text" placeholder="Acme Corp"
                  class="w-full px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white placeholder-slate-600 focus:outline-none focus:border-cyan-500" />
              </div>
              <div>
                <label class="block text-xs text-slate-400 mb-1.5">Status</label>
                <select v-model="form.status" class="w-full px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white focus:outline-none focus:border-cyan-500">
                  <option>On Track</option>
                  <option>At Risk</option>
                  <option>Critical</option>
                </select>
              </div>
              <div>
                <label class="block text-xs text-slate-400 mb-1.5">Health %</label>
                <input v-model.number="form.health" type="number" min="0" max="100"
                  class="w-full px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white focus:outline-none focus:border-cyan-500" />
              </div>
              <div>
                <label class="block text-xs text-slate-400 mb-1.5">Budget ({{ currencySymbol() }})</label>
                <input v-model.number="form.budget" type="number" min="0"
                  class="w-full px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white focus:outline-none focus:border-cyan-500" />
              </div>
              <div>
                <label class="block text-xs text-slate-400 mb-1.5">Days to deadline</label>
                <input v-model.number="form.daysLeft" type="number"
                  class="w-full px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white focus:outline-none focus:border-cyan-500" />
              </div>
            </div>
          </section>

          <p v-if="error" class="text-xs text-rose-400">{{ error }}</p>
        </form>

        <footer class="shrink-0 px-6 py-4 border-t flex items-center justify-end gap-2" style="border-color: var(--color-border); background: rgba(0,0,0,0.2)">
          <button type="button" @click="showModal = false" class="px-4 py-2 rounded-xl text-sm font-semibold text-slate-300 hover:text-white hover:bg-white/5">Cancel</button>
          <button @click="saveProject" :disabled="submitting" class="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold text-white bg-cyan-600 hover:bg-cyan-500 shadow-lg shadow-cyan-500/20 disabled:opacity-60">
            <Loader2 v-if="submitting" :size="14" class="animate-spin" />
            <template v-else>
              <FolderOpen :size="14" />
              {{ editingId ? 'Update project' : 'Create project' }}
            </template>
          </button>
        </footer>
      </div>
    </div>
    </Teleport>
  </div>
</template>
