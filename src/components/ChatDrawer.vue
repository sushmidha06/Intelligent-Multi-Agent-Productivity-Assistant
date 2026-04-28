<script setup>
import { ref, computed, nextTick, watch } from 'vue'
import { Bot, X, Send, Loader2, Wrench, User, Mic, Square } from 'lucide-vue-next'
import { chatService } from '../services/api'
import { useAppStore } from '../stores/app'

const appStore = useAppStore()

const props = defineProps({
  open: Boolean,
})
const emit = defineEmits(['close'])

const messages = ref([])      // { role, content, toolCalls? }
const input = ref('')
const sending = ref(false)
const scrollEl = ref(null)
const error = ref('')

const isRecording = ref(false)
let mediaRecorder = null
let audioChunks = []

async function toggleRecording() {
  if (isRecording.value) {
    stopRecording()
  } else {
    startRecording()
  }
}

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    mediaRecorder = new MediaRecorder(stream)
    audioChunks = []

    mediaRecorder.ondataavailable = (event) => {
      audioChunks.push(event.data)
    }

    mediaRecorder.onstop = async () => {
      const audioBlob = new Blob(audioChunks, { type: 'audio/webm' })
      if (audioBlob.size > 1000) {
        await sendAudio(audioBlob)
      }
    }

    mediaRecorder.start()
    isRecording.value = true
  } catch (err) {
    console.error('Recording failed:', err)
    error.value = 'Microphone access denied.'
  }
}

function stopRecording() {
  if (mediaRecorder) {
    mediaRecorder.stop()
    isRecording.value = false
    mediaRecorder.stream.getTracks().forEach(t => t.stop())
  }
}

async function sendAudio(blob) {
  sending.value = true
  messages.value.push({ role: 'user', content: '🎤 [Voice Memo]' })
  await scrollBottom()
  try {
    const data = await chatService.sendAudio(blob)
    messages.value.push({
      role: 'assistant',
      content: data.response || '(no response)',
      toolCalls: data.tool_calls || [],
    })
  } catch (e) {
    error.value = e?.response?.data?.error || e.message || 'Audio chat failed'
    messages.value.push({
      role: 'assistant',
      content: `Sorry — ${error.value}`,
      isError: true
    })
  } finally {
    sending.value = false
    await scrollBottom()
  }
}

const suggestions = [
  'What projects are active right now?',
  'Summarise my billing — any overdue invoices?',
  'What did my agents alert me about?',
  'Show recent GitHub PRs waiting on review',
]

async function send(text) {
  const msg = (text ?? input.value).trim()
  if (!msg || sending.value) return
  error.value = ''
  messages.value.push({ role: 'user', content: msg })
  input.value = ''
  sending.value = true
  await scrollBottom()
  try {
    const history = messages.value.slice(0, -1).slice(-12) // last 6 turns
    const data = await chatService.send(msg, history)
    messages.value.push({
      role: 'assistant',
      content: data.response || '(no response)',
      toolCalls: data.tool_calls || [],
    })
  } catch (e) {
    error.value = e?.response?.data?.error || e.message || 'Chat failed'
    messages.value.push({
      role: 'assistant',
      content: `Sorry — ${error.value}`,
      toolCalls: [],
      isError: true,
    })
  } finally {
    sending.value = false
    await scrollBottom()
  }
}

async function scrollBottom() {
  await nextTick()
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
}

function clear() {
  messages.value = []
  error.value = ''
}

watch(() => props.open, (v) => {
  if (v) {
    scrollBottom()
    chatService.warmup()
    // If a view opened the drawer with a pre-filled prompt, send it once
    // and then clear it so we don't re-send on the next open.
    const prefill = appStore.chatPrefill
    if (prefill) {
      appStore.chatPrefill = ''
      // Defer one tick so the drawer has fully mounted before we fire.
      nextTick(() => send(prefill))
    }
  }
})

function onKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}
</script>

<template>
  <!-- Backdrop -->
  <transition name="fade">
    <div v-if="open" class="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm" @click="emit('close')" />
  </transition>

  <!-- Drawer -->
  <transition name="slide">
    <aside
      v-if="open"
      class="fixed top-0 right-0 bottom-0 z-50 w-full sm:w-[480px] border-l flex flex-col"
      style="background: var(--color-surface); border-color: var(--color-border);"
    >
      <!-- Header -->
      <header class="h-14 shrink-0 flex items-center justify-between px-4 border-b" style="border-color: var(--color-border);">
        <div class="flex items-center gap-2">
          <div class="w-7 h-7 rounded-lg bg-gradient-to-br from-violet-500 to-cyan-500 flex items-center justify-center">
            <Bot :size="14" class="text-white" />
          </div>
          <div>
            <h3 class="text-sm font-bold text-white leading-none">Sushmi</h3>
            <p class="text-[10px] text-slate-500 mt-0.5">MCP-powered freelance copilot</p>
          </div>
        </div>
        <div class="flex items-center gap-1">
          <button v-if="messages.length" @click="clear" class="text-[10px] text-slate-500 hover:text-white px-2 py-1 rounded">Clear</button>
          <button @click="emit('close')" class="w-8 h-8 rounded-lg flex items-center justify-center text-slate-500 hover:text-white hover:bg-white/5"><X :size="16" /></button>
        </div>
      </header>

      <!-- Messages -->
      <div ref="scrollEl" class="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        <!-- Welcome -->
        <div v-if="messages.length === 0" class="space-y-4">
          <div class="text-sm text-slate-400">
            Ask me anything about your workspace. I'll call the right MCP tools to find the answer.
          </div>
          <div class="space-y-2">
            <p class="text-[10px] uppercase tracking-widest text-slate-500 font-semibold">Try</p>
            <button
              v-for="s in suggestions"
              :key="s"
              @click="send(s)"
              class="block w-full text-left text-xs px-3 py-2.5 rounded-xl border border-slate-800 bg-slate-900/40 text-slate-300 hover:text-white hover:border-slate-600 transition-all"
            >
              {{ s }}
            </button>
          </div>
        </div>

        <div v-for="(m, i) in messages" :key="i" class="space-y-2">
          <!-- User -->
          <div v-if="m.role === 'user'" class="flex items-start gap-2 justify-end">
            <div class="max-w-[80%] px-3 py-2 rounded-2xl bg-gradient-to-br from-violet-600 to-indigo-600 text-white text-sm whitespace-pre-wrap">{{ m.content }}</div>
            <div class="w-7 h-7 rounded-full bg-slate-800 flex items-center justify-center shrink-0"><User :size="13" class="text-slate-400" /></div>
          </div>
          <!-- Assistant -->
          <div v-else class="flex items-start gap-2">
            <div class="w-7 h-7 rounded-full bg-gradient-to-br from-violet-500 to-cyan-500 flex items-center justify-center shrink-0"><Bot :size="13" class="text-white" /></div>
            <div class="flex-1 min-w-0 space-y-2">
              <div v-if="m.toolCalls?.length" class="space-y-1">
                <p class="text-[10px] uppercase tracking-widest text-violet-400 font-semibold flex items-center gap-1"><Wrench :size="10" /> MCP tools invoked</p>
                <div class="flex flex-wrap gap-1.5">
                  <span v-for="(tc, idx) in m.toolCalls" :key="idx" class="text-[10px] font-mono px-2 py-0.5 rounded-full bg-violet-500/15 text-violet-300 border border-violet-500/25">{{ tc.tool }}</span>
                </div>
              </div>
              <div :class="['text-sm whitespace-pre-wrap leading-relaxed', m.isError ? 'text-rose-300' : 'text-slate-200']">{{ m.content }}</div>
            </div>
          </div>
        </div>

        <!-- Thinking indicator -->
        <div v-if="sending" class="flex items-start gap-2">
          <div class="w-7 h-7 rounded-full bg-gradient-to-br from-violet-500 to-cyan-500 flex items-center justify-center shrink-0"><Bot :size="13" class="text-white" /></div>
          <div class="flex items-center gap-2 text-sm text-slate-400">
            <Loader2 :size="14" class="animate-spin text-violet-400" />
            <span>Thinking &amp; calling tools…</span>
          </div>
        </div>
      </div>

      <!-- Input -->
      <div class="shrink-0 border-t p-3" style="border-color: var(--color-border);">
        <div class="flex items-end gap-2">
          <textarea
            v-model="input"
            @keydown="onKey"
            rows="1"
            placeholder="Ask about your projects, emails, invoices…"
            class="flex-1 resize-none px-3 py-2.5 rounded-xl text-sm bg-slate-900/60 border border-slate-700 text-white placeholder-slate-600 focus:outline-none focus:border-violet-500"
          />
          <button
            @click="toggleRecording"
            :class="['shrink-0 w-10 h-10 rounded-xl flex items-center justify-center transition-all', 
              isRecording ? 'bg-rose-600 text-white animate-pulse' : 'bg-slate-800 text-slate-400 hover:text-white hover:bg-slate-700']"
          >
            <Square v-if="isRecording" :size="15" />
            <Mic v-else :size="15" />
          </button>
          <button
            @click="send()"
            :disabled="sending || !input.trim()"
            class="shrink-0 w-10 h-10 rounded-xl bg-violet-600 hover:bg-violet-500 text-white flex items-center justify-center disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <Send :size="15" />
          </button>
        </div>
      </div>
    </aside>
  </transition>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity .2s ease; }
.fade-enter-from, .fade-leave-to       { opacity: 0; }

.slide-enter-active, .slide-leave-active { transition: transform .25s ease; }
.slide-enter-from, .slide-leave-to       { transform: translateX(100%); }
</style>
