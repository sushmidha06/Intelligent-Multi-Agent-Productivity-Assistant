<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { Clock, CheckCircle2, AlertCircle, PlusCircle, Download, X, Edit3, Trash2, Loader2 } from 'lucide-vue-next'
import { billingService } from '../services/api'
import { formatMoney, currencySymbol } from '../services/format'
import { downloadInvoicePdf } from '../services/invoicePdf'

const invoices = ref([])
const loading = ref(true)
const showModal = ref(false)
const editingId = ref(null)
const submitting = ref(false)
const error = ref('')
const toast = ref('')

const form = reactive({
  client: '',
  amount: 1000,
  dueDate: '',
  status: 'Pending',
})

async function load() {
  loading.value = true
  try {
    invoices.value = await billingService.getInvoices()
  } catch (err) {
    console.error('Failed to fetch invoices', err)
  } finally {
    loading.value = false
  }
}

onMounted(load)

const totalRevenue     = computed(() => invoices.value.filter(i => i.status === 'Paid').reduce((s, i) => s + (i.amount || 0), 0))
const totalOutstanding = computed(() => invoices.value.filter(i => i.status !== 'Paid').reduce((s, i) => s + (i.amount || 0), 0))
const totalOverdue     = computed(() => invoices.value.filter(i => i.status === 'Overdue').reduce((s, i) => s + (i.amount || 0), 0))

function flashToast(msg) {
  toast.value = msg
  setTimeout(() => { toast.value = '' }, 2200)
}

function openModal() {
  editingId.value = null
  Object.assign(form, { client: '', amount: 1000, dueDate: '', status: 'Pending' })
  error.value = ''
  showModal.value = true
}

function editInvoice(inv) {
  editingId.value = inv.id
  Object.assign(form, { 
    client: inv.client, 
    amount: inv.amount, 
    dueDate: inv.dueDate || '', 
    status: inv.status 
  })
  error.value = ''
  showModal.value = true
}

async function removeInvoice(id) {
  if (!confirm('Are you sure you want to delete this invoice?')) return
  try {
    // Note: need to add deleteInvoice to billingService in api.js
    await billingService.deleteInvoice(id)
    invoices.value = invoices.value.filter(i => i.id !== id)
    flashToast('Invoice deleted')
  } catch (err) {
    flashToast('Failed to delete invoice')
  }
}

async function saveInvoice() {
  error.value = ''
  if (!form.client.trim() || !form.amount) {
    error.value = 'Client and amount are required.'
    return
  }
  submitting.value = true
  try {
    const payload = {
      client: form.client.trim(),
      amount: Number(form.amount),
      dueDate: form.dueDate || undefined,
      status: form.status,
    }
    
    if (editingId.value) {
      const updated = await billingService.updateInvoice(editingId.value, payload)
      const idx = invoices.value.findIndex(i => i.id === editingId.value)
      if (idx !== -1) invoices.value[idx] = updated
      flashToast('Invoice updated')
    } else {
      const created = await billingService.createInvoice(payload)
      invoices.value = [created, ...invoices.value]
      flashToast(`Invoice ${created.id} created`)
    }
    showModal.value = false
  } catch (e) {
    error.value = e?.response?.data?.error || e.message || 'Failed to save invoice.'
  } finally {
    submitting.value = false
  }
}

async function markPaid(inv) {
  const original = inv.status
  inv.status = 'Paid'
  try {
    await billingService.updateInvoice(inv.id, { status: 'Paid' })
    flashToast(`${inv.id} marked as paid`)
  } catch {
    inv.status = original
    flashToast('Could not update invoice')
  }
}

function downloadInvoice(inv) {
  try {
    downloadInvoicePdf(inv)
    flashToast(`Downloaded ${inv.id}.pdf`)
  } catch (e) {
    console.error('PDF generation failed', e)
    flashToast('Could not generate PDF — please try again')
  }
}

function statusConfig(s) {
  if (s === 'Paid')    return { cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30', icon: CheckCircle2 }
  if (s === 'Overdue') return { cls: 'bg-rose-500/15 text-rose-400 border-rose-500/30',         icon: AlertCircle }
  return                        { cls: 'bg-amber-500/15 text-amber-400 border-amber-500/30',     icon: Clock }
}
</script>

<template>
  <div class="space-y-6 animate-fadeIn">
    <transition name="fade">
      <div v-if="toast" class="fixed top-20 right-6 z-50 px-4 py-2.5 rounded-xl border border-emerald-500/30 bg-slate-900/90 backdrop-blur text-sm text-emerald-200 shadow-lg shadow-emerald-500/20">
        {{ toast }}
      </div>
    </transition>

    <div class="flex items-center justify-between">
      <div>
        <h2 class="text-2xl font-bold text-white">Billing</h2>
        <p class="text-sm text-slate-400 mt-1">Invoice management and revenue tracking</p>
      </div>
      <button
        @click="openModal"
        class="flex items-center gap-2 px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 transition-all text-sm font-semibold text-white shadow-lg shadow-emerald-500/20"
      >
        <PlusCircle :size="14" /> New Invoice
      </button>
    </div>

    <!-- Stats -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div class="p-5 rounded-2xl bg-slate-900 border border-slate-800">
        <p class="text-xs text-slate-500 uppercase tracking-widest mb-2">Revenue Collected</p>
        <p class="text-3xl font-bold text-emerald-400">{{ formatMoney(totalRevenue) }}</p>
      </div>
      <div class="p-5 rounded-2xl bg-slate-900 border border-slate-800">
        <p class="text-xs text-slate-500 uppercase tracking-widest mb-2">Outstanding</p>
        <p class="text-3xl font-bold text-amber-400">{{ formatMoney(totalOutstanding) }}</p>
      </div>
      <div class="p-5 rounded-2xl bg-slate-900 border border-slate-800">
        <p class="text-xs text-slate-500 uppercase tracking-widest mb-2">Overdue</p>
        <p class="text-3xl font-bold text-rose-400">{{ formatMoney(totalOverdue) }}</p>
      </div>
    </div>

    <!-- Invoice table -->
    <div class="rounded-2xl bg-slate-900 border border-slate-800 overflow-hidden">
      <div v-if="loading" class="p-6 text-sm text-slate-500">Loading invoices…</div>
      <div v-else-if="invoices.length === 0" class="p-10 text-center text-sm text-slate-500">No invoices yet.</div>
      <table v-else class="w-full text-sm">
        <thead>
          <tr class="border-b border-slate-800 text-slate-500 text-xs uppercase tracking-widest">
            <th class="text-left px-6 py-4 font-semibold">Invoice</th>
            <th class="text-left px-6 py-4 font-semibold">Client</th>
            <th class="text-left px-6 py-4 font-semibold">Issue Date</th>
            <th class="text-left px-6 py-4 font-semibold">Due Date</th>
            <th class="text-right px-6 py-4 font-semibold">Amount</th>
            <th class="text-right px-6 py-4 font-semibold">Status</th>
            <th class="px-6 py-4 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="inv in invoices" :key="inv.id" class="border-b border-slate-800/50 hover:bg-slate-800/40 transition-colors">
            <td class="px-6 py-4 font-mono text-violet-400 font-semibold">{{ inv.id }}</td>
            <td class="px-6 py-4 text-slate-200 font-medium">{{ inv.client }}</td>
            <td class="px-6 py-4 text-slate-400">{{ inv.issuedDate }}</td>
            <td class="px-6 py-4 text-slate-400">{{ inv.dueDate }}</td>
            <td class="px-6 py-4 text-right font-bold text-white">{{ formatMoney(inv.amount) }}</td>
            <td class="px-6 py-4 text-right">
              <span :class="['text-xs px-2.5 py-1 rounded-full border font-semibold', statusConfig(inv.status).cls]">{{ inv.status }}</span>
            </td>
            <td class="px-6 py-4 text-right whitespace-nowrap">
              <div class="flex items-center justify-end gap-2">
                <button
                  v-if="inv.status !== 'Paid'"
                  @click="markPaid(inv)"
                  class="text-xs font-semibold text-emerald-400 hover:underline mr-1"
                >Mark paid</button>
                <button @click="editInvoice(inv)" class="p-1.5 rounded-lg text-slate-500 hover:text-white hover:bg-white/5 transition-all" title="Edit">
                  <Edit3 :size="14" />
                </button>
                <button @click="removeInvoice(inv.id)" class="p-1.5 rounded-lg text-slate-500 hover:text-rose-400 hover:bg-rose-500/10 transition-all" title="Delete">
                  <Trash2 :size="14" />
                </button>
                <button @click="downloadInvoice(inv)" class="p-1.5 rounded-lg text-slate-500 hover:text-white hover:bg-white/5 transition-all" title="Download">
                  <Download :size="14" />
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- New invoice modal -->
    <Teleport to="body">
    <div v-if="showModal" class="fixed inset-0 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4" style="z-index: 2147483000" @click.self="showModal = false">
      <div class="w-full max-w-lg flex flex-col rounded-2xl border shadow-2xl overflow-hidden" style="background: var(--color-surface); border-color: var(--color-border); max-height: 90vh; height: auto">
        <header class="shrink-0 px-6 py-4 border-b flex items-center justify-between" style="border-color: var(--color-border)">
          <div>
            <h3 class="text-base font-bold text-white">{{ editingId ? 'Edit invoice' : 'Create a new invoice' }}</h3>
            <p class="text-[11px] text-slate-500 mt-0.5">Tracked locally and visible to your AI agents.</p>
          </div>
          <button @click="showModal = false" class="w-8 h-8 rounded-lg flex items-center justify-center text-slate-500 hover:text-white hover:bg-white/5"><X :size="16" /></button>
        </header>

        <form @submit.prevent="saveInvoice" class="flex-1 min-h-0 overflow-y-auto px-6 py-5 space-y-4">
          <div>
            <label class="block text-xs text-slate-400 mb-1.5">Client</label>
            <input v-model="form.client" type="text" placeholder="Acme Corp"
              class="w-full px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500" />
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-xs text-slate-400 mb-1.5">Amount ({{ currencySymbol() }})</label>
              <input v-model.number="form.amount" type="number" min="0"
                class="w-full px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white focus:outline-none focus:border-emerald-500" />
            </div>
            <div>
              <label class="block text-xs text-slate-400 mb-1.5">Status</label>
              <select v-model="form.status" class="w-full px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white focus:outline-none focus:border-emerald-500">
                <option>Pending</option>
                <option>Paid</option>
                <option>Overdue</option>
              </select>
            </div>
          </div>
          <div>
            <label class="block text-xs text-slate-400 mb-1.5">Due date</label>
            <input v-model="form.dueDate" type="date"
              class="w-full px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white focus:outline-none focus:border-emerald-500" />
          </div>

          <p v-if="error" class="text-xs text-rose-400">{{ error }}</p>
        </form>

        <footer class="shrink-0 px-6 py-4 border-t flex items-center justify-end gap-2" style="border-color: var(--color-border); background: rgba(0,0,0,0.2)">
          <button type="button" @click="showModal = false" class="px-4 py-2 rounded-xl text-sm font-semibold text-slate-300 hover:text-white hover:bg-white/5">Cancel</button>
          <button @click="saveInvoice" :disabled="submitting" class="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold text-white bg-emerald-600 hover:bg-emerald-500 shadow-lg shadow-emerald-500/20 disabled:opacity-60">
            <Loader2 v-if="submitting" :size="14" class="animate-spin" />
            <template v-else>
              <PlusCircle v-if="!editingId" :size="14" />
              <Edit3 v-else :size="14" />
              {{ editingId ? 'Update invoice' : 'Create invoice' }}
            </template>
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
