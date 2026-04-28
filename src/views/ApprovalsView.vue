<script setup>
import { ref, onMounted } from 'vue'
import { CheckCircle2, XCircle, Clock, ShieldCheck, Zap, ArrowRight, Loader2 } from 'lucide-vue-next'
import api from '../services/api'

const approvals = ref([])
const loading = ref(true)
const processing = ref(null) // ID of approval being processed

async function load() {
  loading.value = true
  try {
    const r = await api.get('/approvals')
    approvals.value = r.data.approvals || []
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

async function handle(id, action) {
  processing.value = id
  try {
    await api.post(`/approvals/${id}/${action}`)
    approvals.value = approvals.value.filter(a => a.id !== id)
  } catch (e) {
    alert(e?.response?.data?.error || e.message)
  } finally {
    processing.value = null
  }
}

onMounted(load)
</script>

<template>
  <div class="max-w-4xl mx-auto space-y-6 animate-fadeIn">
    <div>
      <h2 class="text-2xl font-bold text-white flex items-center gap-2">
        <ShieldCheck :size="24" class="text-violet-400" /> Human-in-the-Loop Approvals
      </h2>
      <p class="text-sm text-slate-400 mt-1">
        Sensitive actions (like sending invoices or emails) are held here for your final review.
      </p>
    </div>

    <div v-if="loading" class="text-slate-500 text-sm">Loading pending actions...</div>
    
    <div v-else-if="approvals.length === 0" class="p-12 rounded-3xl border border-dashed border-slate-800 text-center space-y-3">
      <div class="w-12 h-12 rounded-full bg-slate-900 flex items-center justify-center mx-auto text-slate-600">
        <CheckCircle2 :size="24" />
      </div>
      <p class="text-slate-500 text-sm italic">All caught up! No pending approvals.</p>
    </div>

    <div v-else class="space-y-3">
      <div 
        v-for="a in approvals" 
        :key="a.id"
        class="rounded-2xl border p-5 flex items-start gap-4 transition-all hover:border-slate-600"
        style="background: var(--color-surface); border-color: var(--color-border)"
      >
        <div class="w-10 h-10 rounded-xl bg-violet-500/10 flex items-center justify-center shrink-0">
          <Zap :size="18" class="text-violet-400" />
        </div>

        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 mb-1">
            <h3 class="text-sm font-bold text-white">Sushmi wants to {{ a.summary }}</h3>
            <span class="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-400 border border-amber-500/30 font-bold uppercase tracking-wider">
              <Clock :size="10" class="inline mr-1" />Pending
            </span>
          </div>
          
          <div class="mt-3 p-3 rounded-xl bg-slate-900/60 border border-slate-800">
            <p class="text-[10px] uppercase tracking-widest text-slate-500 font-bold mb-2">Technical Details (MCP Tool: {{ a.tool }})</p>
            <pre class="text-xs text-slate-400 overflow-x-auto">{{ JSON.stringify(a.arguments, null, 2) }}</pre>
          </div>

          <div class="flex items-center gap-3 mt-4">
            <button 
              @click="handle(a.id, 'approve')"
              :disabled="processing === a.id"
              class="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold text-white bg-violet-600 hover:bg-violet-500 disabled:opacity-50"
            >
              <Loader2 v-if="processing === a.id" :size="14" class="animate-spin" />
              <CheckCircle2 v-else :size="14" />
              Approve & Execute
            </button>
            <button 
              @click="handle(a.id, 'reject')"
              :disabled="processing === a.id"
              class="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold text-rose-400 bg-rose-500/10 hover:bg-rose-500/20 disabled:opacity-50"
            >
              <XCircle :size="14" />
              Reject
            </button>
          </div>
        </div>
      </div>
    </div>

    <div class="rounded-2xl border border-violet-500/20 bg-violet-500/5 p-4 flex items-start gap-3">
      <ShieldCheck :size="16" class="text-violet-400 mt-0.5 shrink-0" />
      <div class="text-xs text-slate-300 leading-relaxed">
        <strong class="text-violet-200">Security Note:</strong>
        Actions in this list will NOT be executed until you click Approve. Sushmi is highly autonomous, but she always defers to your judgment for financial or public-facing tasks.
      </div>
    </div>
  </div>
</template>
