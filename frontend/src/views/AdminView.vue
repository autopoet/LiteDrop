<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ApiError, api } from "../api";
import type { AdminFile, AdminOverview, PageResult } from "../types";
import { formatBytes, formatDate } from "../utils";

const TOKEN_KEY = "litedrop:admin-token";
const PAGE_SIZE = 10;

const token = ref(sessionStorage.getItem(TOKEN_KEY) ?? "");
const username = ref("");
const password = ref("");
const overview = ref<AdminOverview | null>(null);
const files = ref<AdminFile[]>([]);
const query = ref("");
const status = ref("all");
const page = ref(1);
const total = ref(0);
const loading = ref(false);
const fileLoading = ref(false);
const actionMessage = ref("");
const errorMessage = ref("");
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)));

function fileStatus(file: AdminFile) {
  if (file.deleted_at) return "已删除";
  if (file.status === "expired" || new Date(file.expires_at).getTime() <= Date.now()) {
    return "已过期";
  }
  return "可领取";
}

function logout() {
  token.value = "";
  overview.value = null;
  files.value = [];
  total.value = 0;
  page.value = 1;
  sessionStorage.removeItem(TOKEN_KEY);
}

function handleError(error: unknown) {
  if (error instanceof ApiError && error.status === 401) {
    logout();
    errorMessage.value = "登录已失效，请重新登录。";
    return;
  }
  errorMessage.value = error instanceof Error ? error.message : "操作失败，请稍后重试。";
}

function fileQuery() {
  return {
    query: query.value.trim(),
    status: status.value,
    page: page.value,
    pageSize: PAGE_SIZE,
  };
}

function applyFilePage(result: PageResult<AdminFile>) {
  files.value = result.items;
  total.value = result.total;
}

async function fetchFilePage() {
  return api.adminFiles(token.value, fileQuery());
}

async function fetchValidFilePage() {
  let result = await fetchFilePage();
  const lastPage = Math.max(1, Math.ceil(result.total / PAGE_SIZE));
  if (page.value > lastPage) {
    page.value = lastPage;
    result = await fetchFilePage();
  }
  return result;
}

async function loadFiles(resetPage = false) {
  if (!token.value || loading.value || fileLoading.value) return false;
  const previousPage = page.value;
  if (resetPage) page.value = 1;
  fileLoading.value = true;
  errorMessage.value = "";
  try {
    applyFilePage(await fetchValidFilePage());
    return true;
  } catch (error) {
    if (resetPage) page.value = previousPage;
    handleError(error);
    return false;
  } finally {
    fileLoading.value = false;
  }
}

async function changePage(nextPage: number) {
  if (
    nextPage < 1 ||
    nextPage > pageCount.value ||
    loading.value ||
    fileLoading.value
  ) {
    return;
  }
  const previousPage = page.value;
  page.value = nextPage;
  if (!(await loadFiles())) page.value = previousPage;
}

async function loadAdmin() {
  if (!token.value) return;
  loading.value = true;
  errorMessage.value = "";
  try {
    const [overviewResult, fileResult] = await Promise.all([
      api.adminOverview(token.value),
      fetchValidFilePage(),
    ]);
    overview.value = overviewResult;
    applyFilePage(fileResult);
  } catch (error) {
    handleError(error);
  } finally {
    loading.value = false;
  }
}

async function login() {
  if (!username.value || !password.value || loading.value) return;
  loading.value = true;
  errorMessage.value = "";
  try {
    const result = await api.adminLogin(username.value, password.value);
    token.value = result.access_token;
    sessionStorage.setItem(TOKEN_KEY, result.access_token);
    password.value = "";
    await loadAdmin();
  } catch (error) {
    handleError(error);
  } finally {
    loading.value = false;
  }
}

async function removeFile(file: AdminFile) {
  if (!confirm(`确定删除“${file.file_name}”吗？此操作不可恢复。`)) return;
  errorMessage.value = "";
  try {
    await api.deleteAdminFile(token.value, file.id);
    actionMessage.value = "文件已删除。";
    if (files.value.length === 1 && page.value > 1) page.value -= 1;
    await loadAdmin();
  } catch (error) {
    handleError(error);
  }
}

async function cleanup() {
  if (!confirm("立即清理过期文件和未完成上传？")) return;
  loading.value = true;
  errorMessage.value = "";
  try {
    const result = await api.cleanup(token.value);
    actionMessage.value =
      `清理完成：${result.files_deleted} 个文件，${result.uploads_deleted} 个上传会话。`;
    await loadAdmin();
  } catch (error) {
    handleError(error);
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  if (token.value) void loadAdmin();
});
</script>

<template>
  <section class="workspace" aria-labelledby="admin-title">
    <div class="workspace-heading">
      <div>
        <p class="section-kicker">管理工作区</p>
        <h1 id="admin-title">服务管理</h1>
        <p>查看存储占用、分享文件和过期清理状态。</p>
      </div>
      <button v-if="token" class="text-button muted" type="button" @click="logout">
        退出登录
      </button>
    </div>

    <form v-if="!token" class="admin-login" @submit.prevent="login">
      <div class="login-intro">
        <span class="lock-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path d="M7 10V8a5 5 0 0 1 10 0v2m-11 0h12v10H6z" />
          </svg>
        </span>
        <div>
          <h2>管理员登录</h2>
          <p>登录凭证仅保存在当前浏览器标签页。</p>
        </div>
      </div>

      <label class="field">
        <span>用户名</span>
        <input
          v-model.trim="username"
          type="text"
          autocomplete="username"
          placeholder="admin"
        />
      </label>
      <label class="field">
        <span>密码</span>
        <input
          v-model="password"
          type="password"
          autocomplete="current-password"
          placeholder="输入管理员密码"
        />
      </label>
      <button
        class="primary-button full"
        type="submit"
        :disabled="!username || !password || loading"
      >
        {{ loading ? "正在登录…" : "登录管理台" }}
      </button>
      <div v-if="errorMessage" class="error-message compact" role="alert">
        <span>{{ errorMessage }}</span>
      </div>
    </form>

    <template v-else>
      <div v-if="overview" class="overview-strip">
        <div>
          <span>分享文件</span>
          <strong>{{ overview.file_count }}</strong>
          <small>个记录</small>
        </div>
        <div>
          <span>正式文件</span>
          <strong>{{ formatBytes(overview.completed_bytes) }}</strong>
          <small>磁盘占用</small>
        </div>
        <div>
          <span>上传分片</span>
          <strong>{{ formatBytes(overview.uploading_bytes) }}</strong>
          <small>临时占用</small>
        </div>
        <div>
          <span>磁盘剩余</span>
          <strong>{{ formatBytes(overview.free_disk_bytes) }}</strong>
          <small>
            上传{{ overview.public_upload_enabled ? "已开放" : "已关闭" }}
          </small>
        </div>
      </div>

      <div class="admin-toolbar">
        <div class="search-control">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="11" cy="11" r="6"></circle>
            <path d="m16 16 4 4"></path>
          </svg>
          <input
            v-model="query"
            type="search"
            placeholder="搜索文件名或取件码"
            :disabled="loading || fileLoading"
            @keydown.enter.prevent="loadFiles(true)"
          />
        </div>
        <select
          v-model="status"
          aria-label="文件状态"
          :disabled="loading || fileLoading"
          @change="loadFiles(true)"
        >
          <option value="all">全部状态</option>
          <option value="active">可领取</option>
          <option value="expired">已过期</option>
        </select>
        <button
          class="secondary-button"
          type="button"
          :disabled="loading || fileLoading"
          @click="loadFiles(true)"
        >
          {{ fileLoading ? "查询中…" : "查询" }}
        </button>
        <button
          class="secondary-button cleanup-button"
          type="button"
          :disabled="loading || fileLoading"
          @click="cleanup"
        >
          手动清理
        </button>
      </div>

      <div v-if="actionMessage" class="action-message" role="status">
        {{ actionMessage }}
        <button type="button" aria-label="关闭" @click="actionMessage = ''">×</button>
      </div>
      <div v-if="errorMessage" class="error-message compact" role="alert">
        <span>{{ errorMessage }}</span>
      </div>

      <div class="file-table-wrap">
        <table class="file-table">
          <thead>
            <tr>
              <th>文件</th>
              <th>取件码</th>
              <th>大小</th>
              <th>下载</th>
              <th>失效时间</th>
              <th>状态</th>
              <th><span class="visually-hidden">操作</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in files" :key="item.id">
              <td>
                <strong :title="item.file_name">{{ item.file_name }}</strong>
                <small>{{ formatDate(item.created_at) }} 上传</small>
              </td>
              <td class="mono">{{ item.code }}</td>
              <td>{{ formatBytes(item.size) }}</td>
              <td>{{ item.download_count }} / {{ item.download_limit }}</td>
              <td>{{ formatDate(item.expires_at) }}</td>
              <td>
                <span
                  class="status-badge"
                  :class="{ inactive: fileStatus(item) !== '可领取' }"
                >
                  {{ fileStatus(item) }}
                </span>
              </td>
              <td>
                <button class="danger-button" type="button" @click="removeFile(item)">
                  删除
                </button>
              </td>
            </tr>
            <tr v-if="!files.length && fileLoading">
              <td colspan="7" class="table-empty">正在读取文件列表…</td>
            </tr>
            <tr v-else-if="!files.length && !loading">
              <td colspan="7" class="table-empty">没有符合条件的文件。</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="pagination-bar" aria-label="文件列表分页">
        <span>共 {{ total }} 个文件 · 第 {{ page }} / {{ pageCount }} 页</span>
        <div>
          <button
            class="secondary-button"
            type="button"
            :disabled="page <= 1 || loading || fileLoading"
            @click="changePage(page - 1)"
          >
            上一页
          </button>
          <button
            class="secondary-button"
            type="button"
            :disabled="page >= pageCount || loading || fileLoading"
            @click="changePage(page + 1)"
          >
            下一页
          </button>
        </div>
      </div>
    </template>
  </section>
</template>
