<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { api } from "./api";
import AdminView from "./views/AdminView.vue";
import PickupView from "./views/PickupView.vue";
import UploadView from "./views/UploadView.vue";

type Workspace = "upload" | "pickup" | "admin";
type ServiceState = "checking" | "available" | "unavailable";

const route = ref<Workspace>("upload");
const serviceState = ref<ServiceState>("checking");
const year = new Date().getFullYear();
let healthTimer: number | undefined;
let healthController: AbortController | null = null;
let appMounted = false;

const readHash = () => {
  const value = location.hash.slice(1);
  route.value = ["upload", "pickup", "admin"].includes(value)
    ? (value as Workspace)
    : "upload";
};

const pageLabel = computed(
  () =>
    ({
      upload: "上传工作区",
      pickup: "取件工作区",
      admin: "管理工作区",
    })[route.value],
);

const serviceLabel = computed(
  () =>
    ({
      checking: "正在检测服务",
      available: "服务可用",
      unavailable: "服务暂不可用",
    })[serviceState.value],
);

function navigate(next: Workspace) {
  location.hash = next;
}

async function checkHealth() {
  if (healthController) return;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 5000);
  healthController = controller;

  try {
    const health = await api.health(controller.signal);
    if (appMounted) {
      serviceState.value = health.status === "ok" ? "available" : "unavailable";
    }
  } catch {
    if (appMounted) serviceState.value = "unavailable";
  } finally {
    window.clearTimeout(timeout);
    if (healthController === controller) healthController = null;
  }
}

onMounted(() => {
  appMounted = true;
  readHash();
  window.addEventListener("hashchange", readHash);
  void checkHealth();
  healthTimer = window.setInterval(checkHealth, 60_000);
});

onBeforeUnmount(() => {
  appMounted = false;
  window.removeEventListener("hashchange", readHash);
  if (healthTimer) window.clearInterval(healthTimer);
  healthController?.abort();
});
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <div class="header-inner">
        <button class="brand" type="button" @click="navigate('upload')">
          <span class="brand-mark" aria-hidden="true">
            <span></span><span></span><span></span>
          </span>
          <span>CodeDrop</span>
        </button>

        <nav class="main-nav" aria-label="主导航">
          <button
            v-for="item in [
              { key: 'upload', label: '上传' },
              { key: 'pickup', label: '取件' },
              { key: 'admin', label: '管理' },
            ]"
            :key="item.key"
            type="button"
            :class="{ active: route === item.key }"
            @click="navigate(item.key as Workspace)"
          >
            {{ item.label }}
          </button>
        </nav>

        <div
          class="header-status"
          :class="serviceState"
          :title="`${pageLabel} · ${serviceLabel}`"
          aria-live="polite"
        >
          <span></span>
          <span class="status-label">{{ serviceLabel }}</span>
        </div>
      </div>
    </header>

    <main class="app-main">
      <!-- v-show 保留上传组件，切换工作区时上传任务不会中断。 -->
      <UploadView v-show="route === 'upload'" />
      <PickupView v-show="route === 'pickup'" />
      <AdminView v-show="route === 'admin'" />
    </main>

    <footer class="app-footer">
      <span>CodeDrop · 临时文件传输</span>
      <span>{{ year }} · 单文件上限 200 MiB</span>
    </footer>
  </div>
</template>
