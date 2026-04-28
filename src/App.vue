<script setup>
import { RouterView, RouterLink, useRoute, useRouter } from 'vue-router'
import { computed, onMounted, ref } from 'vue'
import {
  LayoutDashboard, Mail, FolderOpen, Receipt, Wallet,
  Settings, Bot, LogOut, Plug, Sparkles
} from 'lucide-vue-next'
import { useAuthStore } from './stores/auth'
import { useAppStore } from './stores/app'
import NotificationsMenu from './components/NotificationsMenu.vue'
import ChatDrawer from './components/ChatDrawer.vue'

const appStore = useAppStore()
// Local toggle stays for the floating chat button; the store is for views
// that want to launch the assistant with a pre-filled prompt.
const chatOpen = ref(false)
// Mirror the store's state into the local ref so existing template bindings
// (`@click="chatOpen = true"`) keep working alongside `appStore.openChatWith`.
import { watch } from 'vue'
watch(() => appStore.chatOpen, (v) => { chatOpen.value = v })
watch(chatOpen, (v) => { if (!v) appStore.closeChat() })

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const navItems = [
  { name: 'Dashboard',    path: '/',             icon: LayoutDashboard },
  { name: 'Inbox Triage', path: '/inbox',        icon: Mail            },
  { name: 'Projects',     path: '/projects',     icon: FolderOpen      },
  { name: 'Billing',      path: '/billing',      icon: Receipt         },
  { name: 'Expenses',     path: '/expenses',     icon: Wallet          },
  { name: 'Integrations', path: '/integrations', icon: Plug            },
]

const pageTitle = computed(() => {
  const found = navItems.find(n => n.path === route.path)
  return found ? found.name : 'Sushmi MCP'
})

const isAuthRoute = computed(() => route.name === 'auth')

const userInitial = computed(() => (auth.user?.name || auth.user?.email || 'U').trim()[0].toUpperCase())
const userLabel   = computed(() => auth.user?.name || auth.user?.email || 'Guest')

onMounted(async () => {
  if (auth.token && !auth.user) await auth.hydrate()
})

async function handleLogout() {
  await auth.logout()
  router.replace({ name: 'auth', query: { mode: 'signin' } })
}
</script>

<template>
  <!-- Auth page (full-screen, no shell) -->
  <RouterView v-if="isAuthRoute" />

  <!-- App shell -->
  <div v-else class="flex h-screen overflow-hidden" style="background: var(--color-bg)">

    <!-- Sidebar -->
    <aside
      class="w-64 shrink-0 flex flex-col border-r"
      style="background: var(--color-surface); border-color: var(--color-border);"
    >
      <!-- Logo -->
      <div class="px-6 py-5 border-b flex items-center gap-3" style="border-color: var(--color-border)">
        <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-500 to-cyan-500 flex items-center justify-center animate-pulse-glow">
          <Bot :size="16" class="text-white" />
        </div>
        <div>
          <h1 class="text-sm font-bold gradient-text leading-none">Sushmi MCP</h1>
          <p class="text-[10px] text-slate-500 mt-0.5">Freelance Intelligence</p>
        </div>
      </div>

      <!-- Nav -->
      <nav class="flex-1 px-3 py-4 space-y-1">
        <RouterLink
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          :class="[
            'flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all duration-200 group',
            route.path === item.path
              ? 'nav-active'
              : 'text-slate-500 hover:text-slate-200 hover:bg-white/5'
          ]"
        >
          <component
            :is="item.icon"
            :size="17"
            :class="route.path === item.path ? 'text-violet-400' : 'text-slate-600 group-hover:text-slate-300'"
          />
          {{ item.name }}
        </RouterLink>
      </nav>

      <!-- Bottom settings -->
      <div class="px-3 pb-4 border-t pt-4 space-y-1" style="border-color: var(--color-border)">
        <RouterLink
          to="/settings"
          :class="['flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all',
            route.path === '/settings'
              ? 'nav-active'
              : 'text-slate-500 hover:text-slate-200 hover:bg-white/5']"
        >
          <Settings :size="17" :class="route.path === '/settings' ? 'text-violet-400' : 'text-slate-600'" />
          Settings
        </RouterLink>
        <button
          @click="handleLogout"
          class="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium text-slate-500 hover:text-rose-300 hover:bg-rose-500/10 transition-all"
        >
          <LogOut :size="17" class="text-slate-600" />
          Sign out
        </button>
      </div>

      <!-- User badge -->
      <div class="px-4 pb-5">
        <div class="flex items-center gap-3 p-3 rounded-xl" style="background: rgba(124,58,237,0.08); border: 1px solid rgba(124,58,237,0.15)">
          <div class="w-8 h-8 rounded-full bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-xs font-bold text-white shrink-0">{{ userInitial }}</div>
          <div class="flex-1 min-w-0">
            <p class="text-xs font-semibold text-slate-200 truncate">{{ userLabel }}</p>
            <p class="text-[10px] text-slate-500 truncate">Active Session</p>
          </div>
          <div class="w-1.5 h-1.5 rounded-full bg-emerald-400"></div>
        </div>
      </div>
    </aside>

    <!-- Main -->
    <main class="flex-1 flex flex-col min-w-0 overflow-hidden">

      <!-- Topbar -->
      <header
        class="h-14 shrink-0 flex items-center justify-between px-6 border-b glass"
        style="border-color: var(--color-border);"
      >
        <div class="flex items-center gap-3">
          <h2 class="text-sm font-semibold text-slate-200">{{ pageTitle }}</h2>
        </div>
        <div class="flex items-center gap-4">
          <button
            @click="chatOpen = true"
            class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-gradient-to-br from-violet-600 to-indigo-600 hover:from-violet-500 hover:to-indigo-500 shadow-lg shadow-violet-500/20"
            title="Ask the AI copilot"
          >
            <Sparkles :size="12" />
            Ask Sushmi
          </button>
          <NotificationsMenu />
        </div>
      </header>

      <!-- Content -->
      <div class="flex-1 overflow-y-auto p-6">
        <RouterView />
      </div>
    </main>

    <!-- Chat drawer -->
    <ChatDrawer :open="chatOpen" @close="chatOpen = false" />
  </div>
</template>
