import { useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import workerSrc from "../../node_modules/react-pdf/node_modules/pdfjs-dist/build/pdf.worker.min.mjs?url";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;

interface PdfViewerProps {
  url: string;
}

export default function PdfViewer({ url }: PdfViewerProps) {
  const [pageNumber, setPageNumber] = useState(1);
  const [numPages, setNumPages] = useState(0);
  const [scale, setScale] = useState(1);

  const canGoBack = pageNumber > 1;
  const canGoForward = pageNumber < numPages;

  return (
    <section className="flex min-h-0 flex-col rounded-lg border border-slate-200 bg-white">
      <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
        <div>
          <h2 className="font-semibold text-slate-900">Source PDF</h2>
          <p className="text-xs text-slate-500">
            Page {numPages ? pageNumber : "—"} of {numPages || "—"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={!canGoBack}
            onClick={() => setPageNumber((page) => Math.max(1, page - 1))}
            className="rounded border border-slate-300 px-2 py-1 text-sm font-medium text-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Prev
          </button>
          <button
            type="button"
            disabled={!canGoForward}
            onClick={() => setPageNumber((page) => Math.min(numPages, page + 1))}
            className="rounded border border-slate-300 px-2 py-1 text-sm font-medium text-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Next
          </button>
          <button
            type="button"
            onClick={() => setScale((value) => Math.max(0.75, Number((value - 0.15).toFixed(2))))}
            className="rounded border border-slate-300 px-2 py-1 text-sm font-medium text-slate-700"
          >
            −
          </button>
          <span className="w-12 text-center text-sm tabular-nums text-slate-600">{Math.round(scale * 100)}%</span>
          <button
            type="button"
            onClick={() => setScale((value) => Math.min(1.75, Number((value + 0.15).toFixed(2))))}
            className="rounded border border-slate-300 px-2 py-1 text-sm font-medium text-slate-700"
          >
            +
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto bg-slate-100 p-4">
        <div className="flex min-h-full justify-center">
          <Document
            file={url}
            loading={<p className="text-sm text-slate-500">Loading PDF...</p>}
            error={<p className="text-sm text-rose-600">Failed to load PDF.</p>}
            onLoadSuccess={({ numPages: nextNumPages }) => {
              setNumPages(nextNumPages);
              setPageNumber(1);
            }}
          >
            <Page pageNumber={pageNumber} scale={scale} renderTextLayer renderAnnotationLayer />
          </Document>
        </div>
      </div>
    </section>
  );
}
