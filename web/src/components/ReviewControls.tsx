/**
 * Sticky reviewer action controls.
 *
 * Approval captures learning artifacts on the backend; rejection records a
 * reason and removes the document from the active dashboard list.
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, type Document } from "../api";

export default function ReviewControls({ documentId }: { documentId: number }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [isRejecting, setIsRejecting] = useState(false);
  const [reason, setReason] = useState("");

  const refresh = async () => {
    /** Refresh detail and dashboard caches after a terminal review action. */

    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["document", String(documentId)] }),
      queryClient.invalidateQueries({ queryKey: ["documents"] }),
    ]);
  };

  const approve = useMutation({
    mutationFn: () => api.post<Document>(`/documents/${documentId}/approve`).then((response) => response.data),
    onSuccess: async () => {
      await refresh();
      navigate(`/documents/${documentId}`);
    },
  });

  const reject = useMutation({
    mutationFn: () => api.post<Document>(`/documents/${documentId}/reject`, { reason }).then((response) => response.data),
    onSuccess: async () => {
      await refresh();
      navigate("/");
    },
  });

  return (
    <section className="sticky bottom-0 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex gap-3">
        <button
          type="button"
          onClick={() => approve.mutate()}
          disabled={approve.isPending || reject.isPending}
          className="flex-1 rounded bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {approve.isPending ? "Approving" : "Approve"}
        </button>
        <button
          type="button"
          onClick={() => setIsRejecting(true)}
          disabled={approve.isPending || reject.isPending}
          className="rounded border border-rose-300 px-4 py-2 text-sm font-semibold text-rose-700 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Reject
        </button>
      </div>

      {isRejecting && (
        <div className="fixed inset-0 z-20 flex items-center justify-center bg-slate-900/30 px-4">
          <div className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl">
            <h2 className="text-lg font-semibold text-slate-900">Reject document</h2>
            <label className="mt-4 block text-sm font-medium text-slate-700" htmlFor="reject-reason">
              Reason
            </label>
            <textarea
              id="reject-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              rows={4}
              className="mt-2 w-full rounded border border-slate-300 px-3 py-2 text-sm"
              placeholder="Why is this document unusable?"
            />
            <div className="mt-5 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setIsRejecting(false)}
                className="rounded border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => reject.mutate()}
                disabled={reject.isPending}
                className="rounded bg-rose-600 px-4 py-2 text-sm font-semibold text-white hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {reject.isPending ? "Rejecting" : "Reject"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
