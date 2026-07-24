<script setup lang="ts">
import { computed, ref } from "vue";
import { ApiError, api, downloadUrl } from "../api";
import type { ShareFile } from "../types";
import { formatBytes, formatDate } from "../utils";

const code = ref("");
const share = ref<ShareFile | null>(null);
const loading = ref(false);
const downloading = ref(false);
const errorMessage = ref("");

const canSearch = computed(() => code.value.length === 6 && !loading.value);

function normalizeCode(event: Event) {
  const input = event.target as HTMLInputElement;
  code.value = input.value.replace(/\D/g, "").slice(0, 6);
  input.value = code.value;
  share.value = null;
  errorMessage.value = "";
}

async function findShare() {
  if (!canSearch.value) return;
  loading.value = true;
  errorMessage.value = "";
  try {
    share.value = await api.getShare(code.value);
  } catch {
    share.value = null;
    errorMessage.value = "文件不存在或已经失效，请检查取件码。";
  } finally {
    loading.value = false;
  }
}

async function download() {
  if (!share.value || downloading.value) return;
  downloading.value = true;
  errorMessage.value = "";
  try {
    const ticket = await api.createDownloadTicket(share.value.code);
    share.value.download_count += 1;
    share.value.remaining_downloads = Math.max(0, share.value.remaining_downloads - 1);
    window.location.assign(downloadUrl(ticket.download_url));
  } catch (error) {
    if (error instanceof ApiError && error.code === "DOWNLOAD_LIMIT_REACHED") {
      errorMessage.value = "下载次数已用完，无法继续领取。";
    } else {
      errorMessage.value =
        error instanceof Error ? error.message : "下载凭证签发失败，请稍后重试。";
    }
  } finally {
    downloading.value = false;
  }
}
</script>

<template>
  <section class="workspace pickup-workspace" aria-labelledby="pickup-title">
    <div class="workspace-heading">
      <div>
        <p class="section-kicker">取件工作区</p>
        <h1 id="pickup-title">领取文件</h1>
        <p>输入发送方提供的六位取件码，查看文件信息后下载。</p>
      </div>
      <div class="limit-note">
        <span>无需账号</span>
        <strong>直接领取</strong>
      </div>
    </div>

    <div class="pickup-layout">
      <div class="code-entry">
        <label for="pickup-code">六位取件码</label>
        <div class="code-input-row">
          <input
            id="pickup-code"
            :value="code"
            type="text"
            inputmode="numeric"
            autocomplete="one-time-code"
            maxlength="6"
            placeholder="000000"
            aria-describedby="code-help"
            @input="normalizeCode"
            @keydown.enter="findShare"
          />
          <button
            class="primary-button"
            type="button"
            :disabled="!canSearch"
            @click="findShare"
          >
            {{ loading ? "查询中…" : "查询文件" }}
          </button>
        </div>
        <p id="code-help">取件码可能以 0 开头，请完整输入。</p>
      </div>

      <div class="pickup-result" aria-live="polite">
        <div v-if="!share && !errorMessage" class="empty-state">
          <span class="empty-glyph" aria-hidden="true">06</span>
          <div>
            <strong>等待取件码</strong>
            <p>查询文件信息不会消耗下载次数。</p>
          </div>
        </div>

        <div v-else-if="errorMessage && !share" class="empty-state error">
          <span class="empty-glyph" aria-hidden="true">×</span>
          <div>
            <strong>没有找到可领取的文件</strong>
            <p>{{ errorMessage }}</p>
          </div>
        </div>

        <div v-if="share" class="share-sheet">
          <div class="share-title">
            <div class="file-icon large" aria-hidden="true">
              <svg viewBox="0 0 24 24">
                <path d="M6 3h8l4 4v14H6zM14 3v5h5" />
              </svg>
            </div>
            <div>
              <span>文件已找到</span>
              <h2>{{ share.file_name }}</h2>
            </div>
          </div>

          <dl class="metadata-list">
            <div>
              <dt>文件大小</dt>
              <dd>{{ formatBytes(share.size) }}</dd>
            </div>
            <div>
              <dt>失效时间</dt>
              <dd>{{ formatDate(share.expires_at) }}</dd>
            </div>
            <div>
              <dt>剩余下载</dt>
              <dd>{{ share.remaining_downloads }} / {{ share.download_limit }} 次</dd>
            </div>
            <div>
              <dt>SHA-256</dt>
              <dd class="hash-value" :title="share.sha256">{{ share.sha256 }}</dd>
            </div>
          </dl>

          <div v-if="errorMessage" class="error-message compact" role="alert">
            <span>{{ errorMessage }}</span>
          </div>

          <button
            class="primary-button download-button"
            type="button"
            :disabled="downloading || share.remaining_downloads <= 0"
            @click="download"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 4v11m0 0 4-4m-4 4-4-4M5 20h14" />
            </svg>
            {{ downloading ? "正在签发凭证…" : "下载文件" }}
          </button>
          <p class="ticket-note">
            点击后会消耗一次下载次数；凭证有效期内支持断点续传。
          </p>
        </div>
      </div>
    </div>
  </section>
</template>
