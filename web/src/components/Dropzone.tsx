/**
 * PDF dropzone.
 *
 * The component is deliberately presentational: it validates selected files and
 * reports them upward, while UploadPage owns the workflow state and API calls.
 */

import { useRef, useState } from "react";

interface DropzoneProps {
  disabled?: boolean;
  onFileSelected: (file: File) => void;
  onFilesSelected?: (files: File[]) => void;
  onInvalidFile: (message: string) => void;
}

function isPdf(file: File) {
  /** Accept PDFs even when the browser omits MIME type metadata. */

  const hasPdfExtension = file.name.toLowerCase().endsWith(".pdf");
  const hasPdfMime = file.type === "application/pdf";
  return hasPdfExtension && (!file.type || hasPdfMime);
}

export default function Dropzone({ disabled = false, onFileSelected, onFilesSelected, onInvalidFile }: DropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const [isDragging, setIsDragging] = useState(false);

  const selectFiles = (fileList: FileList | null) => {
    /** Normalize drag/drop and file-picker inputs into validated File objects. */

    const files = Array.from(fileList || []);
    if (files.length === 0) return;
    const invalid = files.find((file) => !isPdf(file));
    if (invalid) {
      onInvalidFile("Only PDFs accepted.");
      return;
    }
    if (files.length === 1 || !onFilesSelected) onFileSelected(files[0]);
    else onFilesSelected(files);
  };

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => inputRef.current?.click()}
      onDragEnter={(event) => {
        event.preventDefault();
        dragDepth.current += 1;
        setIsDragging(true);
      }}
      onDragOver={(event) => {
        event.preventDefault();
      }}
      onDragLeave={(event) => {
        event.preventDefault();
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (dragDepth.current === 0) setIsDragging(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        dragDepth.current = 0;
        setIsDragging(false);
        selectFiles(event.dataTransfer.files);
      }}
      className={`w-full rounded-lg border-2 border-dashed p-12 text-center transition ${
        isDragging
          ? "border-emerald-500 bg-emerald-50"
          : "border-slate-300 bg-white hover:border-slate-400"
      } ${disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer"}`}
    >
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        multiple
        className="hidden"
        onChange={(event) => selectFiles(event.target.files)}
      />
      <span className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded border border-slate-300 bg-slate-50 text-sm font-semibold text-slate-700">
        PDF
      </span>
      <span className="block text-base font-medium text-slate-900">Drop a PDF here, or click to browse</span>
      <span className="mt-2 block text-sm text-slate-500">Bank statements and scanned PDFs are supported.</span>
    </button>
  );
}
