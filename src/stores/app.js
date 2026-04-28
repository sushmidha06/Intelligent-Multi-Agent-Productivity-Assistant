import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useAppStore = defineStore('app', () => {
  const alerts = ref([])
  const loading = ref(false)
  const lastSync = ref(null)

  // Global chat state — lets any view open the assistant with a pre-filled
  // prompt (e.g. "Draft a proposal for Acme"). The drawer watches `chatOpen`
  // and `chatPrefill`; when both are set it auto-sends the prompt.
  const chatOpen = ref(false)
  const chatPrefill = ref('')

  function setAlerts(newAlerts) {
    alerts.value = newAlerts
  }

  function setLoading(status) {
    loading.value = status
  }

  function setLastSync() {
    lastSync.value = new Date().toISOString()
  }

  function openChatWith(message) {
    chatPrefill.value = message || ''
    chatOpen.value = true
  }

  function closeChat() {
    chatOpen.value = false
    chatPrefill.value = ''
  }

  return {
    alerts, loading, lastSync,
    chatOpen, chatPrefill,
    setAlerts, setLoading, setLastSync,
    openChatWith, closeChat,
  }
})
