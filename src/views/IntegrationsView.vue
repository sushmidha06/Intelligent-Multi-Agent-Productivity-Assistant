<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import {
  Github, Mail, CreditCard, Calendar, CheckCircle2, XCircle, Loader2,
  Plug, X, ExternalLink, Lock
} from 'lucide-vue-next'
import { integrationsService } from '../services/api'

const integrations = ref([])
const loading = ref(true)
const toast = ref({ kind: '', msg: '' })

const modal = reactive({ open: false, provider: null })
const form = reactive({ /* per-provider fields */ })
const submitting = ref(false)
const formError = ref('')

const providers = {
  github: {
    label: 'GitHub',
    icon: Github,
    blurb: 'Project Monitor agent reads commits, PRs, and repo activity for projects you create.',
    color: 'from-slate-400 to-slate-700',
    fields: [
      { key: 'token',    label: 'Personal Access Token (classic)', type: 'password', placeholder: 'ghp_…',
        help: 'Create at github.com/settings/tokens with scopes: repo, read:user' },
      { key: 'username', label: 'GitHub username',                  type: 'text',     placeholder: 'octocat' },
    ],
    helpUrl: 'https://github.com/settings/tokens/new?scopes=repo,read:user&description=Sushmi%20MCP',
  },
  gmail: {
    label: 'Gmail',
    icon: Mail,
    blurb: 'Inbox Triage agent reads recent emails to prioritise client requests and surface action items.',
    color: 'from-rose-500 to-rose-700',
    fields: [
      { key: 'email',       label: 'Gmail address',  type: 'email',    placeholder: 'you@gmail.com' },
      { key: 'appPassword', label: 'App password',   type: 'password', placeholder: '16-char app password',
        help: 'Generate at myaccount.google.com/apppasswords (2-step verification required).' },
    ],
    helpUrl: 'https://myaccount.google.com/apppasswords',
  },
  calendar: {
    label: 'Google Calendar',
    icon: Calendar,
    blurb: 'Deadline Predictor + Inbox Triage agents read upcoming events to weigh meeting density and surface scheduling risk.',
    color: 'from-amber-400 to-orange-600',
    fields: [
      { key: 'icalUrl', label: 'Secret iCal address', type: 'password', placeholder: 'https://calendar.google.com/calendar/ical/.../basic.ics',
        help: 'In Google Calendar → Settings → your calendar → "Secret address in iCal format". Paste the full URL.' },
    ],
    helpUrl: 'https://calendar.google.com/calendar/u/0/r/settings',
  },
  razorpay: {
    label: 'Razorpay',
    icon: CreditCard,
    blurb: 'Billing Engine agent reads invoices and payment status from your Razorpay account.',
    color: 'from-blue-500 to-blue-700',
    fields: [
      { key: 'keyId',     label: 'Key ID',     type: 'text',     placeholder: 'rzp_test_…' },
      { key: 'keySecret', label: 'Key secret', type: 'password', placeholder: '••••••••',
        help: 'Find these in Razorpay dashboard → Account & Settings → API Keys.' },
    ],
    helpUrl: 'https://dashboard.razorpay.com/app/keys',
  },
  google_docs: {
    label: 'Google Docs',
    icon: ExternalLink,
    blurb: 'Proposal Generator agent creates and formats professional proposals directly in your Google Drive.',
    color: 'from-blue-400 to-blue-600',
    fields: [
      { key: 'clientId',     label: 'Client ID',     type: 'text',     placeholder: '...googleusercontent.com' },
      { key: 'clientSecret', label: 'Client Secret', type: 'password', placeholder: 'GOCSPX-...' },
      { key: 'refreshToken', label: 'Refresh Token', type: 'password', placeholder: '1//...',
        help: 'Enable Google Docs API in Cloud Console, create OAuth 2.0 credentials, and use a Playground or tool to get a long-lived Refresh Token.' },
    ],
    helpUrl: 'https://console.cloud.google.com/apis/library/docs.googleapis.com',
  },
  toggl: {
    label: 'Toggl Track',
    icon: Loader2,
    blurb: 'Timesheet agent reads your time entries to generate detailed reports and automate your billing.',
    color: 'from-pink-500 to-rose-600',
    fields: [
      { key: 'apiToken', label: 'API Token', type: 'password', placeholder: 'Paste your API token here',
        help: 'Find this at the bottom of your Toggl Profile settings (track.toggl.com/profile).' },
    ],
    helpUrl: 'https://track.toggl.com/profile',
  },
  linear: {
    label: 'Linear',
    icon: CheckCircle2,
    blurb: 'Issue Tracker agent turns client bug reports in Gmail into structured Linear tickets and tracks their progress.',
    color: 'from-indigo-500 to-blue-700',
    fields: [
      { key: 'apiKey', label: 'Personal API Key', type: 'password', placeholder: 'lin_api_…',
        help: 'Create in Linear → Settings → API → Personal API Keys.' },
    ],
    helpUrl: 'https://linear.app/settings/api',
  },
  slack: {
    label: 'Slack Bot',
    icon: Loader2,
    blurb: 'Talk to Sushmi directly from Slack. Ask for status updates or draft emails from your phone.',
    color: 'from-purple-500 to-indigo-600',
    fields: [
      { key: 'botToken',      label: 'Bot User OAuth Token', type: 'password', placeholder: 'xoxb-…' },
      { key: 'signingSecret', label: 'Signing Secret',       type: 'password', placeholder: '••••••••' },
    ],
    helpUrl: 'https://api.slack.com/apps',
  },
  discord: {
    label: 'Discord Bot',
    icon: Loader2,
    blurb: 'Expose your AI Orchestrator as a Discord bot for quick commands and mobile access.',
    color: 'from-blue-600 to-indigo-800',
    fields: [
      { key: 'botToken', label: 'Bot Token', type: 'password', placeholder: 'MTA0...' },
    ],
    helpUrl: 'https://discord.com/developers/applications',
  },
}

async function load() {
  loading.value = true
  try {
    const data = await integrationsService.list()
    integrations.value = data.integrations || []
  } catch (e) {
    flash('error', e?.response?.data?.error || e.message)
  } finally {
    loading.value = false
  }
}

onMounted(load)

function flash(kind, msg) {
  toast.value = { kind, msg }
  setTimeout(() => { toast.value = { kind: '', msg: '' } }, 2400)
}

function openConnect(provider) {
  modal.provider = provider
  modal.open = true
  formError.value = ''
  for (const f of providers[provider].fields) form[f.key] = ''
}

function closeModal() {
  modal.open = false
  modal.provider = null
}

async function submit() {
  formError.value = ''
  const cfg = providers[modal.provider]
  const secrets = {}
  const metadata = {}
  for (const f of cfg.fields) {
    const v = (form[f.key] || '').trim()
    if (!v) { formError.value = `${f.label} is required`; return }
    if (f.type === 'password') secrets[f.key] = v
    else metadata[f.key] = v
  }
  // Move sensitive fields into secrets, plaintext display fields into metadata
  if (cfg.fields.some(f => f.type !== 'password')) {
    // already split
  }
  submitting.value = true
  try {
    await integrationsService.connect(modal.provider, secrets, metadata)
    flash('success', `${cfg.label} connected`)
    closeModal()
    await load()
  } catch (e) {
    formError.value = e?.response?.data?.error || e.message
  } finally {
    submitting.value = false
  }
}

async function disconnect(provider) {
  if (!confirm(`Disconnect ${providers[provider].label}? Your AI agents will lose access until reconnected.`)) return
  try {
    await integrationsService.disconnect(provider)
    flash('success', `${providers[provider].label} disconnected`)
    await load()
  } catch (e) {
    flash('error', e?.response?.data?.error || e.message)
  }
}

const integrationsByProvider = computed(() => {
  const map = new Map(integrations.value.map(i => [i.provider, i]))
  return Object.keys(providers).map(p => ({ ...providers[p], provider: p, state: map.get(p) }))
})
</script>

<template>
  <div class="max-w-4xl mx-auto space-y-6 animate-fadeIn">

    <!-- Toast -->
    <transition name="fade">
      <div v-if="toast.msg"
           :class="['fixed top-20 right-6 z-50 px-4 py-2.5 rounded-xl border backdrop-blur text-sm shadow-lg flex items-center gap-2',
             toast.kind === 'success' ? 'border-emerald-500/30 bg-slate-900/90 text-emerald-200 shadow-emerald-500/20'
             : 'border-rose-500/30 bg-slate-900/90 text-rose-200 shadow-rose-500/20']">
        <CheckCircle2 v-if="toast.kind === 'success'" :size="14" />
        <XCircle v-else :size="14" />
        {{ toast.msg }}
      </div>
    </transition>

    <div>
      <h2 class="text-2xl font-bold text-white">Integrations</h2>
      <p class="text-sm text-slate-400 mt-1">
        Connect external accounts so your AI agents can act on real data. Credentials are
        <span class="text-violet-300 font-medium">AES-256 encrypted at rest</span> and only decrypted server-side
        when an agent invokes a tool on your behalf.
      </p>
    </div>

    <div v-if="loading" class="text-sm text-slate-500">Loading integrations…</div>

    <div v-else class="grid gap-4 md:grid-cols-1">
      <div
        v-for="p in integrationsByProvider"
        :key="p.provider"
        class="rounded-2xl border p-5 flex items-start gap-4"
        style="background: var(--color-surface); border-color: var(--color-border);"
      >
        <div :class="['w-11 h-11 rounded-xl bg-gradient-to-br flex items-center justify-center shrink-0', p.color]">
          <component :is="p.icon" :size="20" class="text-white" />
        </div>

        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 mb-1">
            <h3 class="text-sm font-bold text-white">{{ p.label }}</h3>
            <span v-if="p.state?.connected" class="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 font-bold uppercase tracking-wider">
              <CheckCircle2 :size="10" class="inline mr-1 -mt-0.5" />Connected
            </span>
            <span v-else class="text-[10px] px-2 py-0.5 rounded-full bg-slate-700/50 text-slate-400 border border-slate-700 font-bold uppercase tracking-wider">
              Not connected
            </span>
          </div>
          <p class="text-xs text-slate-400 leading-relaxed">{{ p.blurb }}</p>

          <div v-if="p.state?.connected && p.state.metadata" class="mt-2 text-[11px] text-slate-500 font-mono">
            <span v-for="(v, k) in p.state.metadata" :key="k" class="mr-3">{{ k }}: {{ v }}</span>
            <span v-if="p.state.connectedAt">since {{ new Date(p.state.connectedAt).toLocaleDateString() }}</span>
          </div>
        </div>

        <div class="shrink-0 flex flex-col gap-2">
          <button
            v-if="!p.state?.connected"
            @click="openConnect(p.provider)"
            class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-violet-600 hover:bg-violet-500"
          >
            <Plug :size="12" /> Connect
          </button>
          <template v-else>
            <button
              @click="openConnect(p.provider)"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-slate-200 bg-slate-800 hover:bg-slate-700"
            >Update</button>
            <button
              @click="disconnect(p.provider)"
              class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-rose-300 bg-rose-500/10 hover:bg-rose-500/20"
            >Disconnect</button>
          </template>
        </div>
      </div>
    </div>

    <div class="rounded-2xl border border-violet-500/20 bg-violet-500/5 p-4 flex items-start gap-3">
      <Lock :size="16" class="text-violet-400 mt-0.5 shrink-0" />
      <div class="text-xs text-slate-300 leading-relaxed">
        <strong class="text-violet-200">How credentials are stored:</strong>
        Every secret is encrypted with AES-256-GCM using a server-side key before being written to Firestore.
        The Python AI service can only request decrypted credentials by presenting a short-lived JWT
        signed by the backend on your behalf.
      </div>
    </div>

    <!-- Connect modal -->
    <div v-if="modal.open" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" @click.self="closeModal">
      <div class="w-full max-w-md rounded-2xl border p-6" style="background: var(--color-surface); border-color: var(--color-border)">
        <div class="flex items-center justify-between mb-5">
          <div class="flex items-center gap-2">
            <component :is="providers[modal.provider].icon" :size="18" class="text-violet-400" />
            <h3 class="text-lg font-bold text-white">Connect {{ providers[modal.provider].label }}</h3>
          </div>
          <button @click="closeModal" class="text-slate-500 hover:text-white"><X :size="18" /></button>
        </div>

        <a :href="providers[modal.provider].helpUrl" target="_blank" rel="noopener"
           class="inline-flex items-center gap-1 text-xs text-violet-400 hover:underline mb-4">
          Open {{ providers[modal.provider].label }} setup page <ExternalLink :size="11" />
        </a>

        <form @submit.prevent="submit" class="space-y-3">
          <div v-for="f in providers[modal.provider].fields" :key="f.key">
            <label class="block text-xs font-medium text-slate-400 mb-1.5">{{ f.label }}</label>
            <input
              v-model="form[f.key]"
              :type="f.type"
              :placeholder="f.placeholder"
              :autocomplete="f.type === 'password' ? 'new-password' : 'off'"
              class="w-full px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white placeholder-slate-600 focus:outline-none focus:border-violet-500"
            />
            <p v-if="f.help" class="text-[11px] text-slate-500 mt-1">{{ f.help }}</p>
          </div>

          <p v-if="formError" class="text-xs text-rose-400">{{ formError }}</p>

          <div class="flex justify-end gap-2 pt-2">
            <button type="button" @click="closeModal" class="px-4 py-2 rounded-xl text-sm font-semibold text-slate-300 hover:text-white hover:bg-white/5">Cancel</button>
            <button type="submit" :disabled="submitting"
                    class="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold text-white bg-violet-600 hover:bg-violet-500 disabled:opacity-60">
              <Loader2 v-if="submitting" :size="14" class="animate-spin" />
              <Plug v-else :size="14" />
              {{ submitting ? 'Connecting…' : 'Connect' }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity .25s ease, transform .25s ease; }
.fade-enter-from, .fade-leave-to       { opacity: 0; transform: translateY(-6px); }
</style>
