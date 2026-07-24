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

interface UploadRun {
  pauseRequested: boolean;
  cancelRequested: boolean;
  startedAt: number;
  startedBytes: number;
  controllers: Set<AbortController>;
}

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

  let activeRun: UploadRun | null = null;
  let activeTask: Promise<void> | null = null;

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
    ["initializing", "uploading", "pausing", "cancelling", "merging"].includes(
      stage.value,
    ),
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

  function markUploaded(partNumber: number, run: UploadRun) {
    if (!uploadedParts.value.includes(partNumber)) {
      uploadedParts.value = [...uploadedParts.value, partNumber].sort((a, b) => a - b);
    }
    const elapsed = (performance.now() - run.startedAt) / 1000;
    if (elapsed > 0) {
      speed.value = (uploadedBytes.value - run.startedBytes) / elapsed;
    }
  }

  async function runRequest<T>(
    run: UploadRun,
    request: (signal: AbortSignal) => Promise<T>,
  ): Promise<T> {
    if (run.cancelRequested) throw new DOMException("Cancelled", "AbortError");
    const controller = new AbortController();
    run.controllers.add(controller);
    try {
      const value = await request(controller.signal);
      if (run.cancelRequested) throw new DOMException("Cancelled", "AbortError");
      return value;
    } finally {
      run.controllers.delete(controller);
    }
  }

  async function uploadPartWithRetry(partNumber: number, run: UploadRun) {
    if (!file.value) return;
    const start = partNumber * chunkSize.value;
    const chunk = file.value.slice(start, Math.min(start + chunkSize.value, file.value.size));
    const hash = await sha256(chunk);

    for (let retry = 0; retry <= 3; retry += 1) {
      if (run.cancelRequested) throw new DOMException("Cancelled", "AbortError");
      const controller = new AbortController();
      run.controllers.add(controller);
      try {
        await api.uploadPart(uploadId.value, partNumber, chunk, hash, controller.signal);
        markUploaded(partNumber, run);
        return;
      } catch (error) {
        if (controller.signal.aborted || run.cancelRequested) throw error;
        if (retry === 3) throw error;
        await delay(1000 * 2 ** retry);
      } finally {
        run.controllers.delete(controller);
      }
    }
  }

  async function uploadMissingParts(parts: number[], run: UploadRun) {
    let cursor = 0;
    let firstError: unknown;

    async function worker() {
      while (!run.pauseRequested && !run.cancelRequested && !firstError) {
        const position = cursor;
        cursor += 1;
        if (position >= parts.length) return;
        try {
          await uploadPartWithRetry(parts[position], run);
        } catch (error) {
          firstError = error;
        }
      }
    }

    await Promise.all(
      Array.from({ length: Math.min(CONCURRENCY, parts.length) }, () => worker()),
    );
    if (firstError && !run.pauseRequested) throw firstError;
  }

  async function waitForMerge(run: UploadRun): Promise<UploadResult> {
    for (let count = 0; count < 120; count += 1) {
      if (run.cancelRequested) {
        throw new DOMException("Cancelled", "AbortError");
      }
      await delay(1500);
      if (run.cancelRequested) {
        throw new DOMException("Cancelled", "AbortError");
      }
      const status = await runRequest(run, (signal) =>
        api.getUpload(uploadId.value, signal),
      );
      applyStatus(status);
      if (status.status === "completed") {
        if (status.share) return status.share;
        return getResult(
          await runRequest(run, (signal) =>
            api.completeUpload(uploadId.value, signal),
          ),
        );
      }
      if (status.status === "failed") {
        throw new Error("服务器合并失败，可以稍后重新尝试。");
      }
    }
    throw new Error("服务器仍在处理文件，请稍后刷新页面查看。");
  }

  async function finishUpload(run: UploadRun) {
    stage.value = "merging";
    try {
      return getResult(
        await runRequest(run, (signal) =>
          api.completeUpload(uploadId.value, signal),
        ),
      );
    } catch (error) {
      if (error instanceof ApiError && error.code === "UPLOAD_ALREADY_MERGING") {
        return waitForMerge(run);
      }
      throw error;
    }
  }

  async function runUpload(
    expireHours: number,
    downloadLimit: number,
    accessCode: string,
    run: UploadRun,
  ) {
    const selectedFile = file.value;
    if (!selectedFile) return;
    errorMessage.value = "";
    speed.value = 0;

    try {
      let status: UploadStatus | null = null;
      stage.value = "initializing";

      if (uploadId.value) {
        try {
          status = await runRequest(run, (signal) =>
            api.getUpload(uploadId.value, signal),
          );
          applyStatus(status);
        } catch (error) {
          if (!(error instanceof ApiError) || error.code !== "UPLOAD_NOT_FOUND") throw error;
          uploadId.value = "";
          clearResumeRecord();
        }
      }

      if (!uploadId.value) {
        const initialized = await runRequest(run, (signal) =>
          api.initUpload(
            {
              file_name: selectedFile.name,
              total_size: selectedFile.size,
              expire_hours: expireHours,
              download_limit: downloadLimit,
            },
            accessCode.trim(),
            signal,
          ),
        );
        uploadId.value = initialized.upload_id;
        chunkSize.value = initialized.chunk_size;
        totalChunks.value = initialized.total_chunks;
        uploadedParts.value = [];
        saveResumeRecord();
      }

      if (status?.status === "completed") {
        result.value =
          status.share ??
          getResult(
            await runRequest(run, (signal) =>
              api.completeUpload(uploadId.value, signal),
            ),
          );
        stage.value = "completed";
        clearResumeRecord();
        return;
      }
      if (status?.status === "merging") {
        stage.value = "merging";
        result.value = await waitForMerge(run);
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
      run.startedAt = performance.now();
      run.startedBytes = uploadedBytes.value;
      await uploadMissingParts(missing, run);

      if (run.cancelRequested) return;
      if (run.pauseRequested) {
        stage.value = "paused";
        return;
      }
      result.value = await finishUpload(run);
      stage.value = "completed";
      clearResumeRecord();
    } catch (error) {
      if (run.cancelRequested) return;
      stage.value = "failed";
      errorMessage.value =
        error instanceof Error ? error.message : "上传失败，请稍后重试。";
    }
  }

  async function start(
    expireHours: number,
    downloadLimit: number,
    accessCode: string,
  ) {
    if (!file.value || activeRun || stage.value === "cancelling") return;

    const run: UploadRun = {
      pauseRequested: false,
      cancelRequested: false,
      startedAt: 0,
      startedBytes: 0,
      controllers: new Set(),
    };
    activeRun = run;
    const task = runUpload(expireHours, downloadLimit, accessCode, run);
    activeTask = task;
    try {
      await task;
    } finally {
      if (activeRun === run) activeRun = null;
      if (activeTask === task) activeTask = null;
    }
  }

  function pause() {
    if (stage.value !== "uploading" || !activeRun) return;
    // 已领取的分片继续完成；所有 worker 收尾后才允许继续上传。
    activeRun.pauseRequested = true;
    stage.value = "pausing";
  }

  async function cancel() {
    const run = activeRun;
    stage.value = "cancelling";
    if (run) {
      run.cancelRequested = true;
      run.pauseRequested = true;
      run.controllers.forEach((controller) => controller.abort());
    }
    await activeTask?.catch(() => undefined);
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
    if (!activeRun) return;
    activeRun.cancelRequested = true;
    activeRun.controllers.forEach((controller) => controller.abort());
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
