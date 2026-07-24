import { computed, onBeforeUnmount, ref } from "vue";
import { ApiError, api } from "../api";
import type {
  ResumeRecord,
  UploadResult,
  UploadStage,
  UploadStatus,
} from "../types";
import { sha256 } from "../utils";

const MAX_FILE_SIZE = 200 * 1024 * 1024;
const CONCURRENCY = 3;
const RESUME_KEY = "codedrop:pending-upload";

function readResumeRecord(): ResumeRecord | null {
  try {
    const value = localStorage.getItem(RESUME_KEY);
    return value ? (JSON.parse(value) as ResumeRecord) : null;
  } catch {
    localStorage.removeItem(RESUME_KEY);
    return null;
  }
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function getResult(value: UploadResult | { share?: UploadResult }): UploadResult {
  return "share" in value && value.share ? value.share : (value as UploadResult);
}

export function useUpload() {
  const stage = ref<UploadStage>("idle");
  const file = ref<File | null>(null);
  const uploadId = ref("");
  const chunkSize = ref(5 * 1024 * 1024);
  const totalChunks = ref(0);
  const uploadedParts = ref<number[]>([]);
  const speed = ref(0);
  const result = ref<UploadResult | null>(null);
  const errorMessage = ref("");
  const resumeRecord = ref<ResumeRecord | null>(readResumeRecord());

  let pauseRequested = false;
  let cancelRequested = false;
  let runStartTime = 0;
  let runStartBytes = 0;
  const activeControllers = new Set<AbortController>();

  const uploadedBytes = computed(() => {
    if (!file.value) return 0;
    return uploadedParts.value.reduce((sum, partNumber) => {
      const start = partNumber * chunkSize.value;
      return sum + Math.min(chunkSize.value, file.value!.size - start);
    }, 0);
  });

  const progress = computed(() =>
    file.value ? Math.min(100, (uploadedBytes.value / file.value.size) * 100) : 0,
  );

  const remainingSeconds = computed(() =>
    speed.value > 0 && file.value
      ? (file.value.size - uploadedBytes.value) / speed.value
      : 0,
  );

  const isWorking = computed(() =>
    ["initializing", "uploading", "merging"].includes(stage.value),
  );

  function saveResumeRecord() {
    if (!file.value || !uploadId.value) return;
    const record: ResumeRecord = {
      uploadId: uploadId.value,
      fileName: file.value.name,
      fileSize: file.value.size,
      lastModified: file.value.lastModified,
      chunkSize: chunkSize.value,
      totalChunks: totalChunks.value,
      savedAt: new Date().toISOString(),
    };
    localStorage.setItem(RESUME_KEY, JSON.stringify(record));
    resumeRecord.value = record;
  }

  function clearResumeRecord() {
    localStorage.removeItem(RESUME_KEY);
    resumeRecord.value = null;
  }

  function matchesResume(selected: File, record: ResumeRecord) {
    return (
      selected.name === record.fileName &&
      selected.size === record.fileSize &&
      selected.lastModified === record.lastModified
    );
  }

  function selectFile(selected: File) {
    errorMessage.value = "";
    result.value = null;

    if (selected.size === 0) {
      errorMessage.value = "不能上传空文件，请重新选择。";
      return;
    }
    if (selected.size > MAX_FILE_SIZE) {
      errorMessage.value = "文件超过 200 MiB 上限，请选择更小的文件。";
      return;
    }

    if (resumeRecord.value && !matchesResume(selected, resumeRecord.value)) {
      errorMessage.value = "所选文件与断点记录不一致，请选择原文件或先放弃记录。";
      return;
    }

    file.value = selected;
    uploadedParts.value = [];
    speed.value = 0;

    if (resumeRecord.value) {
      uploadId.value = resumeRecord.value.uploadId;
      chunkSize.value = resumeRecord.value.chunkSize;
      totalChunks.value = resumeRecord.value.totalChunks;
      stage.value = "paused";
      return;
    }

    uploadId.value = "";
    stage.value = "selected";
  }

  function applyStatus(status: UploadStatus) {
    chunkSize.value = status.chunk_size;
    totalChunks.value = status.total_chunks;
    uploadedParts.value = [...status.uploaded_parts].sort((a, b) => a - b);
  }

  function markUploaded(partNumber: number) {
    if (!uploadedParts.value.includes(partNumber)) {
      uploadedParts.value = [...uploadedParts.value, partNumber].sort((a, b) => a - b);
    }
    const elapsed = (performance.now() - runStartTime) / 1000;
    if (elapsed > 0) {
      speed.value = (uploadedBytes.value - runStartBytes) / elapsed;
    }
  }

  async function uploadPartWithRetry(partNumber: number) {
    if (!file.value) return;
    const start = partNumber * chunkSize.value;
    const chunk = file.value.slice(start, Math.min(start + chunkSize.value, file.value.size));
    const hash = await sha256(chunk);

    for (let retry = 0; retry <= 3; retry += 1) {
      if (cancelRequested) throw new DOMException("Cancelled", "AbortError");
      const controller = new AbortController();
      activeControllers.add(controller);
      try {
        await api.uploadPart(uploadId.value, partNumber, chunk, hash, controller.signal);
        markUploaded(partNumber);
        return;
      } catch (error) {
        if (controller.signal.aborted || cancelRequested) throw error;
        if (retry === 3) throw error;
        await delay(1000 * 2 ** retry);
      } finally {
        activeControllers.delete(controller);
      }
    }
  }

  async function uploadMissingParts(parts: number[]) {
    let cursor = 0;
    let firstError: unknown;

    async function worker() {
      while (!pauseRequested && !firstError) {
        const position = cursor;
        cursor += 1;
        if (position >= parts.length) return;
        try {
          await uploadPartWithRetry(parts[position]);
        } catch (error) {
          firstError = error;
        }
      }
    }

    await Promise.all(
      Array.from({ length: Math.min(CONCURRENCY, parts.length) }, () => worker()),
    );
    if (firstError) throw firstError;
  }

  async function waitForMerge(): Promise<UploadResult> {
    for (let count = 0; count < 120; count += 1) {
      await delay(1500);
      const status = await api.getUpload(uploadId.value);
      applyStatus(status);
      if (status.status === "completed") {
        if (status.share) return status.share;
        return getResult(await api.completeUpload(uploadId.value));
      }
      if (status.status === "failed") {
        throw new Error("服务器合并失败，可以稍后重新尝试。");
      }
    }
    throw new Error("服务器仍在处理文件，请稍后刷新页面查看。");
  }

  async function finishUpload() {
    stage.value = "merging";
    try {
      return getResult(await api.completeUpload(uploadId.value));
    } catch (error) {
      if (error instanceof ApiError && error.code === "UPLOAD_ALREADY_MERGING") {
        return waitForMerge();
      }
      throw error;
    }
  }

  async function start(
    expireHours: number,
    downloadLimit: number,
    accessCode: string,
  ) {
    if (!file.value || isWorking.value) return;

    pauseRequested = false;
    cancelRequested = false;
    errorMessage.value = "";
    speed.value = 0;

    try {
      let status: UploadStatus | null = null;
      stage.value = "initializing";

      if (uploadId.value) {
        try {
          status = await api.getUpload(uploadId.value);
          applyStatus(status);
        } catch (error) {
          if (!(error instanceof ApiError) || error.code !== "UPLOAD_NOT_FOUND") throw error;
          uploadId.value = "";
          clearResumeRecord();
        }
      }

      if (!uploadId.value) {
        const initialized = await api.initUpload(
          {
            file_name: file.value.name,
            total_size: file.value.size,
            expire_hours: expireHours,
            download_limit: downloadLimit,
          },
          accessCode.trim(),
        );
        uploadId.value = initialized.upload_id;
        chunkSize.value = initialized.chunk_size;
        totalChunks.value = initialized.total_chunks;
        uploadedParts.value = [];
        saveResumeRecord();
      }

      if (status?.status === "completed") {
        result.value = status.share ?? getResult(await api.completeUpload(uploadId.value));
        stage.value = "completed";
        clearResumeRecord();
        return;
      }
      if (status?.status === "merging") {
        stage.value = "merging";
        result.value = await waitForMerge();
        stage.value = "completed";
        clearResumeRecord();
        return;
      }

      const uploaded = new Set(uploadedParts.value);
      const missing = Array.from(
        { length: totalChunks.value },
        (_, index) => index,
      ).filter((index) => !uploaded.has(index));

      stage.value = "uploading";
      runStartTime = performance.now();
      runStartBytes = uploadedBytes.value;
      await uploadMissingParts(missing);

      if (pauseRequested || cancelRequested) return;
      result.value = await finishUpload();
      stage.value = "completed";
      clearResumeRecord();
    } catch (error) {
      if (cancelRequested) return;
      stage.value = "failed";
      errorMessage.value =
        error instanceof Error ? error.message : "上传失败，请稍后重试。";
    }
  }

  function pause() {
    if (stage.value !== "uploading") return;
    // 暂停不终止已发出的分片，只阻止 worker 领取下一片。
    pauseRequested = true;
    stage.value = "paused";
  }

  async function cancel() {
    cancelRequested = true;
    pauseRequested = true;
    activeControllers.forEach((controller) => controller.abort());
    if (uploadId.value) {
      await api.cancelUpload(uploadId.value).catch(() => undefined);
    }
    reset();
  }

  async function discardResume() {
    const pendingId = resumeRecord.value?.uploadId;
    if (pendingId) await api.cancelUpload(pendingId).catch(() => undefined);
    clearResumeRecord();
    if (!file.value) stage.value = "idle";
  }

  function reset() {
    file.value = null;
    uploadId.value = "";
    uploadedParts.value = [];
    totalChunks.value = 0;
    speed.value = 0;
    result.value = null;
    errorMessage.value = "";
    stage.value = "idle";
    clearResumeRecord();
  }

  onBeforeUnmount(() => {
    cancelRequested = true;
    activeControllers.forEach((controller) => controller.abort());
  });

  return {
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
  };
}
