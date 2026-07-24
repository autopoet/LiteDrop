export type UploadStage =
  | "idle"
  | "selected"
  | "initializing"
  | "uploading"
  | "paused"
  | "merging"
  | "completed"
  | "failed";

export interface UploadStatus {
  upload_id: string;
  file_name: string;
  total_size: number;
  chunk_size: number;
  total_chunks: number;
  uploaded_parts: number[];
  uploaded_bytes: number;
  status: "uploading" | "merging" | "completed" | "failed";
  expires_at: string;
  share?: UploadResult;
}

export interface UploadResult {
  code: string;
  file_name?: string;
  size?: number;
  sha256?: string;
  expires_at?: string;
}

export interface ShareFile {
  id?: string;
  code: string;
  file_name: string;
  size: number;
  sha256: string;
  created_at: string;
  expires_at: string;
  download_limit: number;
  download_count: number;
  remaining_downloads: number;
  status?: string;
}

export interface AdminOverview {
  file_count: number;
  completed_bytes: number;
  uploading_bytes: number;
  free_disk_bytes: number;
  storage_quota_bytes?: number;
  public_upload_enabled: boolean;
}

export interface AdminFile {
  id: string;
  code: string;
  file_name: string;
  size: number;
  download_count: number;
  download_limit: number;
  created_at: string;
  expires_at: string;
  status?: string;
  deleted_at?: string | null;
}

export interface ResumeRecord {
  uploadId: string;
  fileName: string;
  fileSize: number;
  lastModified: number;
  chunkSize: number;
  totalChunks: number;
  savedAt: string;
}
