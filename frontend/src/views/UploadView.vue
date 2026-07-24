<script setup lang="ts">
import { computed, ref } from "vue";
import { useUpload } from "../composables/useUpload";
import { copyText, formatBytes, formatDuration } from "../utils";

const {
  stage,
  file,
  uploadId,
  chunkSize,
  totalChunks,
  uploadedParts,
  uploadedBytes,
  progress,
  speed,
  remainingSeconds,
  result,
  errorMessage,
  resumeRecord,
  isWorking,
  selectFile,
  start,
  pause,
  cancel,
  discardResume,
  reset,
} = useUpload();

const fileInput = ref<HTMLInputElement | null>(null);
const isDragging = ref(false);
const expireHours = ref(6);
const downloadLimit = ref(3);
const accessCode = ref("");
const copied = ref(false);

const stageLabel = computed(
  () =>
    ({
      idle: "等待选择",
      selected: "准备上传",
      initializing: "正在创建会话",
      uploading: "正在上传分片",
      pausing: "正在暂停上传",
      paused: "上传已暂停",
      cancelling: "正在取消上传",
      merging: "服务器正在合并",
      completed: "上传完成",
      failed: "上传遇到问题",
    })[stage.value],
);

const actionLabel = computed(() => {
  if (stage.value === "paused") return "继续上传";
  if (stage.value === "failed") return "重试缺失分片";
  return "开始上传";
});

function chooseFile() {
  fileInput.value?.click();
}

function takeFile(files: FileList | null) {
  const selected = files?.[0];
  if (selected) selectFile(selected);
  if (fileInput.value) fileInput.value.value = "";
}

function handleDrop(event: DragEvent) {
  isDragging.value = false;
  takeFile(event.dataTransfer?.files ?? null);
}

async function copyCode() {
  if (!result.value?.code) return;
  await copyText(result.value.code);
  copied.value = true;
  window.setTimeout(() => (copied.value = false), 1600);
}
</script>

<template>
  <section class="workspace" aria-labelledby="upload-title">
    <div class="workspace-heading">
      <div>
        <p class="section-kicker">上传工作区</p>
        <h1 id="upload-title">发送文件</h1>
        <p>文件按 5 MiB 分片传输；网络中断后可从缺失分片继续。</p>
      </div>
      <div class="limit-note">
        <span>单文件上限</span>
        <strong>200 MiB</strong>
      </div>
    </div>

    <div
      v-if="resumeRecord && !file"
      class="resume-notice"
      role="status"
    >
      <div>
        <strong>检测到未完成上传</strong>
        <span>
          {{ resumeRecord.fileName }} · {{ formatBytes(resumeRecord.fileSize) }}。
          请重新选择同一文件继续。
        </span>
      </div>
      <div class="inline-actions">
        <button class="text-button" type="button" @click="chooseFile">选择原文件</button>
        <button class="text-button muted" type="button" @click="discardResume">放弃记录</button>
      </div>
    </div>

    <div class="upload-layout">
      <div class="upload-primary">
        <input
          ref="fileInput"
          class="visually-hidden"
          type="file"
          @change="takeFile(($event.target as HTMLInputElement).files)"
        />

        <button
          v-if="!file"
          class="drop-zone"
          :class="{ dragging: isDragging }"
          type="button"
          @click="chooseFile"
          @dragenter.prevent="isDragging = true"
          @dragover.prevent="isDragging = true"
          @dragleave.prevent="isDragging = false"
          @drop.prevent="handleDrop"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 13v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5" />
          </svg>
          <strong>拖入一个文件，或点击选择</strong>
          <span>一次上传一个文件，支持暂停和断点续传</span>
        </button>
        <div v-if="!file && errorMessage" class="error-message" role="alert">
          <span>{{ errorMessage }}</span>
        </div>

        <div v-if="file" class="file-workbench">
          <div class="file-line">
            <div class="file-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24">
                <path d="M6 3h8l4 4v14H6zM14 3v5h5" />
              </svg>
            </div>
            <div class="file-copy">
              <strong :title="file.name">{{ file.name }}</strong>
              <span>
                {{ formatBytes(file.size) }}
                <template v-if="totalChunks">
                  · {{ totalChunks }} 个分片
                </template>
              </span>
            </div>
            <button
              v-if="stage === 'selected' || stage === 'idle'"
              class="text-button"
              type="button"
              @click="chooseFile"
            >
              更换
            </button>
            <span v-else class="stage-indicator" :class="stage">
              <i></i>{{ stageLabel }}
            </span>
          </div>

          <div
            v-if="stage !== 'selected' || uploadedParts.length"
            class="progress-region"
          >
            <div class="progress-copy">
              <strong>{{ progress.toFixed(progress < 10 ? 1 : 0) }}%</strong>
              <span v-if="stage === 'uploading'">
                {{ formatBytes(speed) }}/s · {{ formatDuration(remainingSeconds) }}
              </span>
              <span v-else-if="stage === 'merging'">
                分片已上传，正在生成最终文件
              </span>
              <span v-else>
                {{ formatBytes(uploadedBytes) }} / {{ formatBytes(file.size) }}
              </span>
            </div>
            <div
              class="progress-track"
              role="progressbar"
              :aria-valuenow="progress"
              aria-valuemin="0"
              aria-valuemax="100"
            >
              <span :style="{ width: `${progress}%` }"></span>
            </div>
            <div class="part-count">
              <span>已完成 {{ uploadedParts.length }} / {{ totalChunks || "—" }} 个分片</span>
              <span v-if="chunkSize">{{ formatBytes(chunkSize) }} / 片</span>
            </div>
          </div>

          <div v-if="stage === 'merging'" class="merge-status" role="status">
            <span class="spinner" aria-hidden="true"></span>
            <div>
              <strong>服务器正在合并分片</strong>
              <span>请保持页面打开，这一步不会再次上传文件。</span>
            </div>
          </div>

          <div v-if="stage === 'completed' && result" class="completion">
            <p>取件码</p>
            <button class="pickup-code" type="button" @click="copyCode">
              {{ result.code }}
              <span>{{ copied ? "已复制" : "点击复制" }}</span>
            </button>
            <p class="completion-note">
              将取件码发送给接收方。文件到期或次数用完后将无法领取。
            </p>
            <div class="button-row">
              <button class="primary-button" type="button" @click="copyCode">
                {{ copied ? "取件码已复制" : "复制取件码" }}
              </button>
              <button class="secondary-button" type="button" @click="reset">
                再传一个
              </button>
            </div>
          </div>

          <div v-if="errorMessage" class="error-message" role="alert">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 8v5m0 3h.01M10.3 4.3 3.6 16a2 2 0 0 0 1.7 3h13.4a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0Z" />
            </svg>
            <span>{{ errorMessage }}</span>
          </div>
        </div>
      </div>

      <aside class="upload-controls" aria-label="上传设置">
        <div class="control-heading">
          <span>传输设置</span>
          <small v-if="uploadId">续传沿用原设置</small>
        </div>

        <label class="field">
          <span>保存时间</span>
          <select v-model="expireHours" :disabled="Boolean(uploadId)">
            <option :value="1">1 小时</option>
            <option :value="6">6 小时</option>
            <option :value="12">12 小时</option>
            <option :value="24">24 小时</option>
          </select>
        </label>

        <label class="field">
          <span>可下载次数</span>
          <select v-model="downloadLimit" :disabled="Boolean(uploadId)">
            <option v-for="count in 5" :key="count" :value="count">
              {{ count }} 次
            </option>
          </select>
        </label>

        <label class="field">
          <span>上传口令</span>
          <input
            v-model="accessCode"
            type="password"
            autocomplete="off"
            placeholder="由服务管理员提供"
            :disabled="Boolean(uploadId)"
          />
          <small>口令只用于创建上传会话，不会保存在浏览器中。</small>
        </label>

        <div v-if="file && stage !== 'completed'" class="control-actions">
          <button
            v-if="stage === 'selected' || stage === 'paused' || stage === 'failed'"
            class="primary-button full"
            type="button"
            @click="start(expireHours, downloadLimit, accessCode)"
          >
            {{ actionLabel }}
          </button>
          <button
            v-if="stage === 'uploading'"
            class="primary-button full"
            type="button"
            @click="pause"
          >
            暂停上传
          </button>
          <button
            v-if="stage === 'pausing'"
            class="primary-button full"
            type="button"
            disabled
          >
            正在等待在途分片…
          </button>
          <button
            v-if="stage === 'cancelling'"
            class="primary-button full"
            type="button"
            disabled
          >
            正在删除临时分片…
          </button>
          <button
            v-if="
              stage !== 'merging' &&
              stage !== 'initializing' &&
              stage !== 'cancelling'
            "
            class="secondary-button full"
            type="button"
            @click="cancel"
          >
            取消并删除分片
          </button>
          <p v-if="isWorking" class="action-hint">
            可切换到其他工作区，当前任务会继续运行。
          </p>
        </div>

        <dl class="transfer-facts">
          <div>
            <dt>分片大小</dt>
            <dd>5 MiB</dd>
          </div>
          <div>
            <dt>并发数</dt>
            <dd>3</dd>
          </div>
          <div>
            <dt>失败重试</dt>
            <dd>最多 3 次</dd>
          </div>
        </dl>
      </aside>
    </div>
  </section>
</template>
