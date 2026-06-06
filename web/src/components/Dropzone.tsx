import { useRef, useState } from "react";

interface DropzoneProps {
  disabled?: boolean;
  onFileSelected: (file: File) => void;
  onInvalidFile: (message: string) => void;
}

function isPdf(file: File) {
  const hasPdfExtension = file.name.toLowerCase().endsWith(".pdf");
  const hasPdfMime = file.type === "application/pdf";
  return hasPdfExtension && (!file.type || hasPdfMime);
}

export default function Dropzone({ disabled = false, onFileSelected, onInvalidFile }: DropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const [isDragging, setIsDragging] = useState(false);

  const selectFile = (file: File | undefined) => {
    if (!file) return;
    if (!isPdf(file)) {
      onInvalidFile("Only PDFs accepted.");
      return;
    }
    onFileSelected(file);
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
        selectFile(event.dataTransfer.files[0]);
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
        className="hidden"
        onChange={(event) => selectFile(event.target.files?.[0])}
      />
      <span className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded border border-slate-300 bg-slate-50 text-sm font-semibold text-slate-700">
        PDF
      </span>
      <span className="block text-base font-medium text-slate-900">Drop a PDF here, or click to browse</span>
      <span className="mt-2 block text-sm text-slate-500">Bank statements and scanned PDFs are supported.</span>
    </button>
  );
}
