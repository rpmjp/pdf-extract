import axios from "axios";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import Dropzone from "../components/Dropzone";
import UploadResult from "../components/UploadResult";
import { api, type Document, type ParseResponse } from "../api";

type UploadStage =
  | { stage: "idle" }
  | { stage: "selected"; file: File }
  | { stage: "uploading"; file: File }
  | { stage: "parsing"; file: File; document: Document }
  | { stage: "done"; file: File; document: Document; result: ParseResponse }
  | { stage: "error"; message: string; file?: File; duplicateId?: number };

function formatFileSize(size: number) {
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
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

  const parse = useMutation({
    mutationFn: (id: number) => api.post<ParseResponse>(`/documents/${id}/parse`).then((response) => response.data),
  });

  const selectedFile = "file" in state ? state.file : undefined;
  const isBusy = state.stage === "uploading" || state.stage === "parsing";

  const runUpload = async (file: File) => {
    try {
      setState({ stage: "uploading", file });
      const document = await upload.mutateAsync(file);
      setState({ stage: "parsing", file, document });
      const result = await parse.mutateAsync(document.id);
      await queryClient.invalidateQueries({ queryKey: ["documents"] });
      setState({ stage: "done", file, document, result });
    } catch (error) {
      setState(getErrorState(error, file));
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
          onInvalidFile={(message) => setState({ stage: "error", message })}
        />

        {selectedFile && state.stage !== "done" && (
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

        {state.stage === "parsing" && (
          <div className="rounded-lg border border-slate-200 bg-white p-5 text-sm text-slate-600">
            <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-slate-900 align-[-3px]" />{" "}
            Extracting...
          </div>
        )}

        {state.stage === "done" && <UploadResult result={state.result} />}

        {state.stage === "error" && (
          <div className="rounded-lg border border-rose-200 bg-rose-50 p-5 text-sm text-rose-900">
            <p className="font-medium">{state.message}</p>
            {state.duplicateId && (
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
