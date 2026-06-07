import axios from "axios";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import Dropzone from "../components/Dropzone";
import UploadResult from "../components/UploadResult";
import { api, type BatchUploadResponse, type Document, type JobResponse, type ParseResponse } from "../api";

type BatchItem = {
  file: File;
  status: "selected" | "uploading" | "queued" | "parsing" | "done" | "error";
  document?: Document;
  result?: ParseResponse;
  error?: string;
  duplicateId?: number | null;
};

type UploadStage =
  | { stage: "idle" }
  | { stage: "selected"; file: File }
  | { stage: "batch_selected"; files: File[] }
  | { stage: "batch_running"; items: BatchItem[] }
  | { stage: "batch_done"; items: BatchItem[] }
  | { stage: "uploading"; file: File }
  | { stage: "queued"; file: File; document: Document; jobId: string }
  | { stage: "parsing"; file: File; document: Document; jobId: string }
  | { stage: "done"; file: File; document: Document; result: ParseResponse }
  | { stage: "error"; message: string; file?: File; duplicateId?: number };

function formatFileSize(size: number) {
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function wait(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function getErrorState(error: unknown, file?: File): UploadStage {
  if (!axios.isAxiosError(error)) {
    return { stage: "error", message: "Something went wrong.", file };
  }

  if (!error.response) {
    return { stage: "error", message: "Couldn't reach the API.", file };
  }

  const detail = error.response.data?.detail;
  const message = typeof detail === "string" ? detail : "Something went wrong.";

  if (error.response.status === 400) {
    return { stage: "error", message: "Only PDFs accepted.", file };
  }

  if (error.response.status === 409) {
    const duplicateId = message.match(/id=(\d+)/)?.[1];
    return {
      stage: "error",
      message,
      file,
      duplicateId: duplicateId ? Number(duplicateId) : undefined,
    };
  }

  return { stage: "error", message: `Parse failed. ${message}`, file };
}

export default function UploadPage() {
  const queryClient = useQueryClient();
  const [state, setState] = useState<UploadStage>({ stage: "idle" });

  const upload = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return api.post<Document>("/documents", fd).then((response) => response.data);
    },
  });

  const enqueueParse = useMutation({
    mutationFn: (id: number) => api.post<JobResponse>(`/documents/${id}/parse`).then((response) => response.data),
  });

  const jobId = state.stage === "queued" || state.stage === "parsing" ? state.jobId : null;
  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: async () => (await api.get<JobResponse>(`/jobs/${jobId}`)).data,
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const data = query.state.data;
      return data?.status === "success" || data?.status === "failed" ? false : 1500;
    },
  });

  const selectedFile = "file" in state ? state.file : undefined;
  const isPolling = state.stage === "queued" || state.stage === "parsing";
  const isBatch = state.stage === "batch_selected" || state.stage === "batch_running" || state.stage === "batch_done";
  const activeJobStatus = job.data?.status;
  const visibleStage = isPolling && activeJobStatus === "started" ? "parsing" : state.stage;
  const completedResult = isPolling && activeJobStatus === "success" ? job.data?.result : undefined;
  const failedJobError = isPolling && activeJobStatus === "failed" ? job.data?.error || "Parse failed." : undefined;
  const isBusy = state.stage === "uploading" || state.stage === "batch_running" || (isPolling && !completedResult && !failedJobError);

  const runUpload = async (file: File) => {
    try {
      setState({ stage: "uploading", file });
      const document = await upload.mutateAsync(file);
      const parseJob = await enqueueParse.mutateAsync(document.id);
      await queryClient.invalidateQueries({ queryKey: ["documents"] });
      setState({ stage: "queued", file, document, jobId: parseJob.job_id });
    } catch (error) {
      setState(getErrorState(error, file));
    }
  };

  const waitForJob = async (jobId: string): Promise<ParseResponse> => {
    for (;;) {
      const response = await api.get<JobResponse>(`/jobs/${jobId}`);
      if (response.data.status === "success" && response.data.result) return response.data.result;
      if (response.data.status === "failed") throw new Error(response.data.error || "Parse failed.");
      await wait(1500);
    }
  };

  const updateBatchItem = (index: number, patch: Partial<BatchItem>) => {
    setState((current) => {
      if (current.stage !== "batch_running") return current;
      const items = current.items.map((item, itemIndex) => (itemIndex === index ? { ...item, ...patch } : item));
      return { stage: "batch_running", items };
    });
  };

  const runBatch = async (files: File[]) => {
    const items: BatchItem[] = files.map((file) => ({ file, status: "selected" }));
    setState({ stage: "batch_running", items });

    const fd = new FormData();
    files.forEach((file) => fd.append("files", file));

    try {
      files.forEach((_, index) => updateBatchItem(index, { status: "uploading" }));
      const uploadResponse = await api.post<BatchUploadResponse>("/documents/batch", fd);

      await Promise.all(
        uploadResponse.data.results.map(async (result, index) => {
          if (!result.document) {
            updateBatchItem(index, { status: "error", error: result.error || "Upload failed.", duplicateId: result.duplicate_id });
            return;
          }

          updateBatchItem(index, { status: "queued", document: result.document });
          try {
            const parseJob = await enqueueParse.mutateAsync(result.document.id);
            updateBatchItem(index, { status: "parsing" });
            const parseResult = await waitForJob(parseJob.job_id);
            updateBatchItem(index, { status: "done", result: parseResult });
          } catch (error) {
            const message = error instanceof Error ? error.message : "Parse failed.";
            updateBatchItem(index, { status: "error", error: message });
          }
        }),
      );

      await queryClient.invalidateQueries({ queryKey: ["documents"] });
      setState((current) => (current.stage === "batch_running" ? { stage: "batch_done", items: current.items } : current));
    } catch (error) {
      setState({ stage: "error", message: "Batch upload failed." });
    }
  };

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-8">
        <h1 className="text-2xl font-semibold text-slate-900">Upload</h1>
        <p className="mt-2 text-sm text-slate-500">Upload a PDF statement and parse it automatically.</p>
      </div>

      <div className="space-y-6">
        <Dropzone
          disabled={isBusy}
          onFileSelected={(file) => setState({ stage: "selected", file })}
          onFilesSelected={(files) => setState({ stage: "batch_selected", files })}
          onInvalidFile={(message) => setState({ stage: "error", message })}
        />

        {state.stage === "batch_selected" && (
          <div className="rounded-lg border border-slate-200 bg-white p-5">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="font-medium text-slate-900">{state.files.length} PDFs selected</p>
                <p className="mt-1 text-sm text-slate-500">
                  {state.files.reduce((total, file) => total + file.size, 0) > 0
                    ? formatFileSize(state.files.reduce((total, file) => total + file.size, 0))
                    : ""}
                </p>
              </div>
              <button
                type="button"
                onClick={() => runBatch(state.files)}
                className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
              >
                Upload all
              </button>
            </div>
          </div>
        )}

        {isBatch && state.stage !== "batch_selected" && (
          <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
            {state.items.map((item) => (
              <div key={`${item.file.name}-${item.file.size}`} className="flex items-center justify-between border-t border-slate-100 px-4 py-3 first:border-t-0">
                <div>
                  <p className="font-medium text-slate-900">{item.file.name}</p>
                  <p className="text-sm text-slate-500">{item.error || item.status}</p>
                </div>
                {item.document ? (
                  <Link to={`/documents/${item.document.id}`} className="text-sm font-medium text-slate-700 underline">
                    View
                  </Link>
                ) : null}
              </div>
            ))}
          </div>
        )}

        {selectedFile && !completedResult && !isBatch && (
          <div className="rounded-lg border border-slate-200 bg-white p-5">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="font-medium text-slate-900">{selectedFile.name}</p>
                <p className="mt-1 text-sm text-slate-500">{formatFileSize(selectedFile.size)}</p>
              </div>
              {state.stage === "selected" || state.stage === "error" ? (
                <button
                  type="button"
                  onClick={() => runUpload(selectedFile)}
                  className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
                >
                  Upload
                </button>
              ) : null}
            </div>
          </div>
        )}

        {state.stage === "uploading" && (
          <div className="rounded-lg border border-slate-200 bg-white p-5 text-sm text-slate-600">
            <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-slate-900 align-[-3px]" />{" "}
            Uploading...
          </div>
        )}

        {isPolling && !completedResult && !failedJobError && (
          <div className="rounded-lg border border-slate-200 bg-white p-5 text-sm text-slate-600">
            <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-slate-900 align-[-3px]" />{" "}
            {visibleStage === "queued" ? "Queued..." : "Extracting..."}
          </div>
        )}

        {completedResult && <UploadResult result={completedResult} />}

        {(state.stage === "error" || failedJobError) && (
          <div className="rounded-lg border border-rose-200 bg-rose-50 p-5 text-sm text-rose-900">
            <p className="font-medium">{failedJobError || (state.stage === "error" ? state.message : "Parse failed.")}</p>
            {state.stage === "error" && state.duplicateId && (
              <Link to={`/documents/${state.duplicateId}`} className="mt-3 inline-block font-medium text-rose-700 underline">
                View document
              </Link>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
