<script setup>
import { ref, computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import {
  Tag, ChevronRight, Zap, Inbox, RefreshCw, AlertCircle, Mail,
  Users, Receipt, GitBranch, Calendar, Folder, Trash2, FolderOpen, ArrowRight, Sparkles, Loader2, X
} from 'lucide-vue-next'
import { inboxService, integrationsService, expensesService, projectService } from '../services/api'
import { formatMoney, currencySymbol } from '../services/format'
import { useAppStore } from '../stores/app'

const appStore = useAppStore()

const emails = ref([])           // all emails (excluding deleted by default)
const folders = ref([])          // [{ path, top, label, count }]
const selectedFolder = ref(null) // null = all
const loading = ref(true)
const triaging = ref(false)
const toast = ref('')
const expandedId = ref(null)
const errorMsg = ref('')
const gmailConnected = ref(false)
const moveMenuFor = ref(null)    // email id whose move-menu is open

// "Convert to expense" flow state
const expenseModalOpen = ref(false)
const expenseExtracting = ref(false)
const expenseSaving = ref(false)
const draftingFor = ref(null)   // email id currently being drafted

// Sync-to-knowledge-base state
const syncing = ref(false)
const syncStatus = ref({ count: 0, lastSyncedAt: null })
const expenseError = ref('')
const expenseSourceEmail = ref(null)  // { id, from, subject }
const expenseDraft = ref({
  vendor: '', amount: 0, date: '', category: 'Other', projectId: '', notes: '', confidence: '', reasoning: '',
})
const projectsForPicker = ref([])
const categories = ['Software','Hardware','Hosting & infra','Subcontractor','Travel','Meals','Office','Marketing','Taxes & fees','Other']

async function startExpenseConvert(email) {
  moveMenuFor.value = null
  expenseError.value = ''
  expenseExtracting.value = true
  expenseSourceEmail.value = { id: email.id, from: email.from, subject: email.subject }
  expenseDraft.value = { vendor: email.from || '', amount: 0, date: '', category: 'Other', projectId: '', notes: '', confidence: '', reasoning: '' }
  expenseModalOpen.value = true
  // Pull projects in the background so the picker is populated by the time the modal opens.
  projectService.getProjects().then(p => { projectsForPicker.value = p || [] }).catch(() => {})
  try {
    const data = await inboxService.extractExpense(email.id)
    if (data?.draft?.isExpense === false) {
      expenseError.value = `This email doesn't look like an expense. ${data.draft.reasoning || ''}`.trim()
    } else {
      const d = data?.draft || {}
      expenseDraft.value = {
        vendor: d.vendor || email.from || '',
        amount: Number(d.amount) || 0,
        date: d.date || new Date().toISOString().slice(0, 10),
        category: d.category || 'Other',
        projectId: '',
        notes: `Auto-extracted from email: "${email.subject}"`,
        confidence: d.confidence || '',
        reasoning: d.reasoning || '',
      }
    }
  } catch (e) {
    expenseError.value = e?.response?.data?.error || e.message
  } finally {
    expenseExtracting.value = false
  }
}

async function saveExpense() {
  expenseError.value = ''
  if (!expenseDraft.value.vendor?.trim()) { expenseError.value = 'Vendor is required'; return }
  if (!expenseDraft.value.amount || expenseDraft.value.amount <= 0) { expenseError.value = 'Amount must be > 0'; return }
  expenseSaving.value = true
  try {
    const project = expenseDraft.value.projectId
      ? projectsForPicker.value.find(p => p.id === expenseDraft.value.projectId)
      : null
    await expensesService.create({
      vendor: expenseDraft.value.vendor.trim(),
      amount: Number(expenseDraft.value.amount),
      date: expenseDraft.value.date,
      category: expenseDraft.value.category,
      projectId: expenseDraft.value.projectId || null,
      projectClient: project?.client || null,
      notes: expenseDraft.value.notes,
      source: `email:${expenseSourceEmail.value?.id || ''}`,
    })
    flashToast(`Logged ${formatMoney(expenseDraft.value.amount)} expense`)
    expenseModalOpen.value = false
  } catch (e) {
    expenseError.value = e?.response?.data?.error || e.message
  } finally {
    expenseSaving.value = false
  }
}

async function loadSyncStatus() {
  try { syncStatus.value = await inboxService.syncRagStatus() } catch { /* ignore */ }
}

async function syncToKnowledgeBase() {
  if (syncing.value) return
  syncing.value = true
  try {
    const r = await inboxService.syncRag({ days: 30, limit: 100 })
    flashToast(`Indexed ${r.indexed} emails — Ask Sushmi can now cite them`)
    await loadSyncStatus()
  } catch (e) {
    flashToast(`Sync failed: ${e?.response?.data?.error || e.message}`)
  } finally {
    syncing.value = false
  }
}

async function draftReply(email) {
  if (draftingFor.value) return
  draftingFor.value = email.id
  try {
    const r = await inboxService.draftReply(email.id)
    flashToast(`Drafted reply saved to ${r.draftsMailbox || 'Gmail Drafts'} — open Gmail to review`)
  } catch (e) {
    flashToast(`Draft failed: ${e?.response?.data?.error || e.message}`)
  } finally {
    draftingFor.value = null
  }
}

// One-click "file as Linear issue". The agent reads the full email body via
// gmail__get_email_body, then calls issues__create_linear_issue, which is
// approval-gated — the user reviews the proposed ticket before it ships.
function fileAsIssue(email) {
  const subject = email?.subject || '(no subject)'
  const sender = email?.from || email?.fromAddress || 'a client'
  const id = email?.id || email?.uid
  const prompt = (
    `Open the email with id ${id} (subject: "${subject}", from: ${sender}). ` +
    `Read the full body with gmail__get_email_body, then file it as a Linear ` +
    `issue using issues__create_linear_issue. Title should be short and ` +
    `descriptive; description should include the relevant quoted excerpt.`
  )
  appStore.openChatWith(prompt)
}

async function checkGmailConnected() {
  try {
    const data = await integrationsService.list()
    const g = (data.integrations || []).find(i => i.provider === 'gmail')
    gmailConnected.value = !!g?.connected
  } catch { /* ignore */ }
}

async function load() {
  loading.value = true
  errorMsg.value = ''
  await checkGmailConnected()

  if (gmailConnected.value) {
    try {
      const data = await inboxService.getGmail()
      emails.value = (data.emails || []).filter(e => !e.deleted)
      folders.value = data.folders || []
      return
    } catch (err) {
      const msg = err?.response?.data?.error || err.message
      errorMsg.value = `Couldn't reach Gmail: ${msg}`
    } finally {
      loading.value = false
    }
  }
  loading.value = false
}

onMounted(() => { load(); loadSyncStatus() })

const visibleEmails = computed(() => {
  if (!selectedFolder.value) return emails.value
  return emails.value.filter(e => e.folder === selectedFolder.value)
})

const folderTree = computed(() => {
  // Group by top-level
  const tree = []
  const tops = new Map()
  for (const f of folders.value) {
    if (!tops.has(f.top)) tops.set(f.top, { top: f.top, count: 0, children: [] })
    const t = tops.get(f.top)
    t.count += f.count
    if (f.path !== f.top) t.children.push(f)
  }
  for (const [top, data] of tops) tree.push({ top, ...data })
  // Stable order
  const order = ['clients', 'expenses', 'github', 'calendar', 'other']
  tree.sort((a, b) => order.indexOf(a.top) - order.indexOf(b.top))
  return tree
})

const TOP_META = {
  clients:  { icon: Users,    color: 'text-violet-400', label: 'Clients'   },
  expenses: { icon: Receipt,  color: 'text-emerald-400', label: 'Expenses' },
  github:   { icon: GitBranch, color: 'text-slate-300', label: 'GitHub'    },
  calendar: { icon: Calendar, color: 'text-amber-400',  label: 'Calendar'  },
  other:    { icon: Folder,   color: 'text-slate-500',  label: 'Other'     },
}
function topMeta(top) { return TOP_META[top] || { icon: Folder, color: 'text-slate-500', label: top } }

function flashToast(msg) { toast.value = msg; setTimeout(() => toast.value = '', 2200) }

async function triageAll() {
  if (triaging.value) return
  triaging.value = true
  try {
    const order = { high: 0, medium: 1, low: 2 }
    emails.value = [...emails.value].sort((a, b) => order[a.priority] - order[b.priority])
    await new Promise(r => setTimeout(r, 600))
    flashToast(`Triaged ${emails.value.length} emails by priority`)
  } finally { triaging.value = false }
}

async function deleteEmail(email) {
  emails.value = emails.value.filter(e => e.id !== email.id)
  try { await inboxService.deleteEmail(email.id) } catch { /* ignore */ }
  flashToast(`Deleted "${email.subject}"`)
  // Recount folders client-side
  recountFolders()
}

async function moveEmail(email, target) {
  const old = email.folder
  email.folder = target
  moveMenuFor.value = null
  try { await inboxService.moveEmail(email.id, target) }
  catch { email.folder = old; flashToast('Move failed'); return }
  flashToast(`Moved to ${target}`)
  recountFolders()
}

function recountFolders() {
  const counts = new Map()
  for (const e of emails.value) counts.set(e.folder, (counts.get(e.folder) || 0) + 1)
  // Rebuild folders array from the new counts
  const newFolders = []
  for (const [path, count] of counts) {
    const top = path.includes('/') ? path.split('/')[0] : path
    const label = path.includes('/') ? path.split('/').slice(1).join('/') : path
    newFolders.push({ path, top, label, count })
  }
  folders.value = newFolders
}

function toggleExpand(id) {
  expandedId.value = expandedId.value === id ? null : id
  moveMenuFor.value = null
}

function selectFolder(path) { selectedFolder.value = path; moveMenuFor.value = null }
function clearFilter() { selectedFolder.value = null }

const allFolderPaths = computed(() => {
  // Where the user can move emails to. Includes a "+ new client" placeholder.
  const set = new Set(folders.value.map(f => f.path))
  for (const top of ['expenses', 'github', 'calendar', 'other']) set.add(top)
  return [...set].sort((a, b) => a.localeCompare(b))
})

const priorityConfig = {
  high:   { cls: 'bg-rose-500/15 text-rose-400 border-rose-500/30',     dot: 'bg-rose-400'   },
  medium: { cls: 'bg-amber-500/15 text-amber-400 border-amber-500/30',  dot: 'bg-amber-400'  },
  low:    { cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30', dot: 'bg-emerald-400' },
}
</script>

<template>
  <div class="space-y-6 animate-fadeIn">

    <transition name="fade">
      <div v-if="toast" class="fixed top-20 right-6 z-50 px-4 py-2.5 rounded-xl border border-violet-500/30 bg-slate-900/90 backdrop-blur text-sm text-violet-200 shadow-lg shadow-violet-500/20">
        {{ toast }}
      </div>
    </transition>

    <div class="flex items-center justify-between">
      <div>
        <h2 class="text-2xl font-bold text-white">Inbox Triage</h2>
        <p class="text-sm text-slate-400 mt-1 flex items-center gap-2">
          AI-prioritised messages from your inbox
          <span v-if="gmailConnected" class="inline-flex items-center gap-1 text-[10px] uppercase tracking-widest font-bold px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
            <Mail :size="9" /> Gmail · last 7 days
          </span>
        </p>
      </div>
      <div class="flex items-center gap-2">
        <button v-if="gmailConnected" @click="load" :disabled="loading"
                class="flex items-center gap-1.5 px-3 py-2 rounded-xl border border-slate-700 hover:border-slate-500 text-xs font-semibold text-slate-300 hover:text-white disabled:opacity-50">
          <RefreshCw :size="13" :class="loading ? 'animate-spin' : ''" /> Refresh
        </button>
        <button @click="triageAll" :disabled="triaging || emails.length === 0"
                class="flex items-center gap-2 px-4 py-2 rounded-xl bg-violet-600 hover:bg-violet-500 text-sm font-semibold text-white shadow-lg shadow-violet-500/20 disabled:opacity-50">
          <Zap :size="14" /> {{ triaging ? 'Triaging…' : 'Triage All' }}
        </button>
      </div>
    </div>

    <!-- RAG sync banner: only visible when Gmail is connected -->
    <div v-if="gmailConnected"
         class="rounded-2xl border border-violet-500/30 bg-violet-500/5 p-4 flex items-center justify-between gap-4 flex-wrap">
      <div class="flex items-start gap-3 flex-1 min-w-0">
        <div class="w-9 h-9 rounded-lg bg-violet-500/15 border border-violet-500/30 flex items-center justify-center shrink-0">
          <Sparkles :size="16" class="text-violet-400" />
        </div>
        <div class="min-w-0">
          <p class="text-sm font-semibold text-white">Knowledge base sync</p>
          <p class="text-[11px] text-slate-400 mt-0.5">
            <template v-if="syncStatus.count">
              <span class="text-violet-300 font-mono">{{ syncStatus.count }}</span> emails indexed.
              <span v-if="syncStatus.lastSyncedAt"> Last sync {{ new Date(syncStatus.lastSyncedAt).toLocaleString() }}.</span>
              Ask Sushmi questions about your inbox in chat.
            </template>
            <template v-else>
              Index the last 30 days of your inbox so the AI agent can search and cite emails when you ask.
            </template>
          </p>
        </div>
      </div>
      <button @click="syncToKnowledgeBase"
              :disabled="syncing"
              class="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold text-white bg-violet-600 hover:bg-violet-500 shadow-lg shadow-violet-500/20 disabled:opacity-50 disabled:cursor-not-allowed shrink-0">
        <Loader2 v-if="syncing" :size="14" class="animate-spin" />
        <Sparkles v-else :size="14" />
        {{ syncing ? 'Syncing…' : (syncStatus.count ? 'Re-sync now' : 'Sync inbox to knowledge base') }}
      </button>
    </div>

    <div v-if="errorMsg" class="p-3 rounded-xl border border-amber-500/30 bg-amber-500/5 text-xs text-amber-200 flex items-start gap-2">
      <AlertCircle :size="14" class="mt-0.5 shrink-0" />
      <span>{{ errorMsg }}</span>
    </div>

    <div v-if="loading" class="text-sm text-slate-500">Loading inbox…</div>

    <!-- Empty: not connected -->
    <div v-else-if="!gmailConnected" class="rounded-2xl border border-slate-800 bg-slate-900 p-10 flex flex-col items-center text-center">
      <Inbox :size="36" class="mb-3 text-slate-700" />
      <p class="text-sm font-semibold text-white">Connect Gmail to start triaging</p>
      <p class="text-xs text-slate-500 mt-2 max-w-sm">
        Inbox Triage uses the Gmail MCP server to read your recent inbox and auto-organise it into client / expense / GitHub / calendar folders.
        <RouterLink to="/integrations" class="text-violet-400 hover:underline">Connect Gmail</RouterLink>.
      </p>
    </div>

    <!-- Empty: connected, no mail -->
    <div v-else-if="emails.length === 0" class="rounded-2xl border border-slate-800 bg-slate-900 p-10 flex flex-col items-center text-center">
      <Inbox :size="36" class="mb-3 text-slate-700" />
      <p class="text-sm font-semibold text-white">Inbox zero for the last 7 days. Beautiful.</p>
    </div>

    <!-- Main grid: folders | mail list -->
    <div v-else class="grid grid-cols-1 md:grid-cols-[220px_1fr] gap-4">

      <!-- Folder sidebar -->
      <aside class="rounded-2xl border border-slate-800 bg-slate-900/60 p-2 h-fit md:sticky md:top-4">
        <button @click="clearFilter"
                :class="['w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg text-sm transition-all',
                  !selectedFolder ? 'bg-violet-500/15 text-violet-200 border border-violet-500/30'
                                  : 'text-slate-300 hover:bg-white/5 border border-transparent']">
          <span class="flex items-center gap-2"><Inbox :size="14" /> All</span>
          <span class="text-[10px] text-slate-500">{{ emails.length }}</span>
        </button>

        <div class="mt-2 space-y-0.5">
          <div v-for="t in folderTree" :key="t.top">
            <button @click="t.children.length === 0 ? selectFolder(t.top) : null"
                    :class="['w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg text-sm transition-all',
                      selectedFolder === t.top ? 'bg-violet-500/15 text-violet-200 border border-violet-500/30'
                                               : 'text-slate-300 hover:bg-white/5 border border-transparent']">
              <span class="flex items-center gap-2">
                <component :is="topMeta(t.top).icon" :size="14" :class="topMeta(t.top).color" />
                <span class="capitalize">{{ topMeta(t.top).label }}</span>
              </span>
              <span class="text-[10px] text-slate-500">{{ t.count }}</span>
            </button>

            <div v-if="t.children.length" class="ml-4 mt-0.5 space-y-0.5 border-l border-slate-800 pl-2">
              <button v-for="c in t.children" :key="c.path"
                      @click="selectFolder(c.path)"
                      :class="['w-full flex items-center justify-between gap-2 px-2 py-1.5 rounded-lg text-xs transition-all',
                        selectedFolder === c.path ? 'bg-violet-500/15 text-violet-200'
                                                  : 'text-slate-400 hover:bg-white/5']">
                <span class="flex items-center gap-1.5 truncate">
                  <FolderOpen :size="11" /> <span class="truncate">{{ c.label }}</span>
                </span>
                <span class="text-[10px] text-slate-500 shrink-0">{{ c.count }}</span>
              </button>
            </div>
          </div>
        </div>
      </aside>

      <!-- Email list -->
      <div class="space-y-3 min-w-0">
        <div v-if="selectedFolder" class="text-xs text-slate-500 flex items-center gap-2">
          <span>Viewing</span>
          <span class="font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300">{{ selectedFolder }}</span>
          <button @click="clearFilter" class="text-violet-400 hover:underline">clear filter</button>
        </div>

        <div v-if="visibleEmails.length === 0" class="rounded-2xl border border-slate-800 bg-slate-900 p-8 text-center text-slate-500 text-sm">
          No emails in this folder.
        </div>

        <div v-for="email in visibleEmails" :key="email.id"
             @click="toggleExpand(email.id)"
             class="group p-5 rounded-2xl bg-slate-900 border border-slate-800 hover:border-slate-600 hover:bg-slate-800/70 transition-all cursor-pointer relative">
          <div class="flex items-start gap-4">
            <div class="w-10 h-10 rounded-full bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-sm font-bold text-white shrink-0 mt-0.5">
              {{ (email.from || '?')[0].toUpperCase() }}
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex items-center justify-between gap-3 mb-1">
                <span class="font-semibold text-white text-sm truncate">{{ email.from }}</span>
                <div class="flex items-center gap-2 shrink-0">
                  <span class="text-xs text-slate-500">{{ email.time }}</span>
                  <span :class="['text-xs px-2 py-0.5 rounded-full border font-medium', priorityConfig[email.priority].cls]">
                    <span :class="['inline-block w-1.5 h-1.5 rounded-full mr-1 align-middle', priorityConfig[email.priority].dot]"></span>
                    {{ email.priority }}
                  </span>
                </div>
              </div>
              <p class="text-sm font-medium text-slate-200 truncate">{{ email.subject }}</p>
              <p class="text-xs text-slate-500 mt-0.5" :class="expandedId === email.id ? '' : 'truncate'">{{ email.preview || email.fromAddress }}</p>
              <div class="flex items-center gap-2 mt-2">
                <span class="text-xs px-2 py-0.5 rounded-md bg-slate-800 text-slate-400 border border-slate-700">
                  <Tag :size="10" class="inline mr-1" />{{ email.label }}
                </span>
                <span class="text-[10px] font-mono px-2 py-0.5 rounded-md bg-violet-500/10 text-violet-300 border border-violet-500/20">
                  <FolderOpen :size="9" class="inline mr-1" />{{ email.folder }}
                </span>
              </div>

              <!-- Expanded actions -->
              <div v-if="expandedId === email.id" class="mt-4 flex items-center gap-2 flex-wrap" @click.stop>
                <div class="relative">
                  <button @click="moveMenuFor = moveMenuFor === email.id ? null : email.id"
                          class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700">
                    <ArrowRight :size="12" /> Move to…
                  </button>
                  <div v-if="moveMenuFor === email.id"
                       class="absolute left-0 mt-1 z-30 w-56 max-h-64 overflow-y-auto rounded-xl border border-slate-700 bg-slate-900 shadow-xl py-1">
                    <button v-for="path in allFolderPaths" :key="path"
                            @click="moveEmail(email, path)"
                            :disabled="path === email.folder"
                            class="w-full text-left px-3 py-1.5 text-xs font-mono text-slate-300 hover:bg-violet-500/10 hover:text-violet-200 disabled:opacity-40 disabled:bg-transparent disabled:cursor-default">
                      {{ path }}
                    </button>
                  </div>
                </div>

                <button @click="deleteEmail(email)"
                        class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-rose-500/10 hover:bg-rose-500/20 text-rose-300 border border-rose-500/30">
                  <Trash2 :size="12" /> Delete
                </button>

                <button @click="startExpenseConvert(email)"
                        class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                  <Receipt :size="12" /> Convert to expense
                </button>

                <button @click="draftReply(email)"
                        :disabled="draftingFor === email.id"
                        class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-violet-600 hover:bg-violet-500 text-white disabled:opacity-60 disabled:cursor-not-allowed">
                  <Loader2 v-if="draftingFor === email.id" :size="12" class="animate-spin" />
                  <Zap v-else :size="12" />
                  {{ draftingFor === email.id ? 'Drafting…' : 'Draft AI reply' }}
                </button>

                <button @click="fileAsIssue(email)"
                        title="Open Sushmi and file this email as a Linear issue (approval-gated)"
                        class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
                  <AlertCircle :size="12" /> File as Linear issue
                </button>
              </div>
            </div>
            <ChevronRight :size="16" :class="['text-slate-600 group-hover:text-slate-400 transition-transform shrink-0 mt-1', expandedId === email.id ? 'rotate-90' : '']" />
          </div>
        </div>
      </div>
    </div>

    <!-- Convert-to-expense modal (Teleported to body for viewport safety) -->
    <Teleport to="body">
      <div v-if="expenseModalOpen"
           class="fixed inset-0 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
           style="z-index: 2147483000"
           @click.self="expenseModalOpen = false">
        <div class="w-full max-w-lg flex flex-col rounded-2xl border shadow-2xl overflow-hidden"
             style="background: var(--color-surface); border-color: var(--color-border); max-height: 90vh">
          <header class="shrink-0 px-6 py-4 border-b flex items-center justify-between" style="border-color: var(--color-border)">
            <div>
              <h3 class="text-base font-bold text-white flex items-center gap-2">
                <Sparkles :size="14" class="text-emerald-400" /> Convert to expense
              </h3>
              <p class="text-[11px] text-slate-500 mt-0.5 truncate max-w-md">From "{{ expenseSourceEmail?.subject || '' }}" — {{ expenseSourceEmail?.from }}</p>
            </div>
            <button @click="expenseModalOpen = false" class="w-8 h-8 rounded-lg flex items-center justify-center text-slate-500 hover:text-white hover:bg-white/5"><X :size="16" /></button>
          </header>

          <form @submit.prevent="saveExpense" class="flex-1 min-h-0 overflow-y-auto px-6 py-5 space-y-4">

            <div v-if="expenseExtracting" class="p-4 rounded-xl border border-violet-500/30 bg-violet-500/5 text-xs text-violet-200 flex items-center gap-2">
              <Loader2 :size="14" class="animate-spin" />
              <span>Reading the email and extracting amount, vendor, date…</span>
            </div>

            <div v-else-if="expenseDraft.confidence" class="p-3 rounded-xl border border-slate-700 bg-slate-900/40 text-[11px]">
              <div class="flex items-center gap-2 mb-1">
                <Sparkles :size="11" class="text-emerald-400" />
                <span class="font-bold uppercase tracking-widest text-emerald-300">Gemini extracted ({{ expenseDraft.confidence }} confidence)</span>
              </div>
              <p class="text-slate-400">{{ expenseDraft.reasoning }}</p>
            </div>

            <div v-if="expenseError" class="p-3 rounded-xl border border-amber-500/30 bg-amber-500/5 text-xs text-amber-200 flex items-start gap-2">
              <AlertCircle :size="14" class="mt-0.5 shrink-0" /> <span>{{ expenseError }}</span>
            </div>

            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-xs text-slate-400 mb-1.5">Amount ({{ currencySymbol() }})</label>
                <input v-model.number="expenseDraft.amount" type="number" min="0" step="0.01"
                  class="w-full px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white focus:outline-none focus:border-emerald-500" />
              </div>
              <div>
                <label class="block text-xs text-slate-400 mb-1.5">Date</label>
                <input v-model="expenseDraft.date" type="date"
                  class="w-full px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white focus:outline-none focus:border-emerald-500" />
              </div>
            </div>

            <div>
              <label class="block text-xs text-slate-400 mb-1.5">Vendor</label>
              <input v-model="expenseDraft.vendor" type="text"
                class="w-full px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white focus:outline-none focus:border-emerald-500" />
            </div>

            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-xs text-slate-400 mb-1.5">Category</label>
                <select v-model="expenseDraft.category"
                  class="w-full px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white focus:outline-none focus:border-emerald-500">
                  <option v-for="c in categories" :key="c" :value="c">{{ c }}</option>
                </select>
              </div>
              <div>
                <label class="block text-xs text-slate-400 mb-1.5">Project (optional)</label>
                <select v-model="expenseDraft.projectId"
                  class="w-full px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white focus:outline-none focus:border-emerald-500">
                  <option value="">— unattributed —</option>
                  <option v-for="p in projectsForPicker" :key="p.id" :value="p.id">{{ p.name }} ({{ p.client }})</option>
                </select>
              </div>
            </div>

            <div>
              <label class="block text-xs text-slate-400 mb-1.5">Notes</label>
              <textarea v-model="expenseDraft.notes" rows="2"
                class="w-full px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500"></textarea>
            </div>
          </form>

          <footer class="shrink-0 px-6 py-4 border-t flex items-center justify-end gap-2" style="border-color: var(--color-border); background: rgba(0,0,0,0.2)">
            <button type="button" @click="expenseModalOpen = false" class="px-4 py-2 rounded-xl text-sm font-semibold text-slate-300 hover:text-white hover:bg-white/5">Cancel</button>
            <button @click="saveExpense" :disabled="expenseSaving || expenseExtracting" class="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold text-white bg-emerald-600 hover:bg-emerald-500 disabled:opacity-60">
              <Loader2 v-if="expenseSaving" :size="14" class="animate-spin" />
              <Receipt v-else :size="14" />
              {{ expenseSaving ? 'Saving…' : 'Save expense' }}
            </button>
          </footer>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity .25s ease, transform .25s ease; }
.fade-enter-from, .fade-leave-to       { opacity: 0; transform: translateY(-6px); }
</style>
