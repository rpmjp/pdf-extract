import { useEffect, useRef, useState } from "react";
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
  const [rotation, setRotation] = useState(0);
  const [fitWidth, setFitWidth] = useState(true);
  const [search, setSearch] = useState("");
  const [viewerWidth, setViewerWidth] = useState(0);
  const viewerRef = useRef<HTMLDivElement | null>(null);

  const canGoBack = pageNumber > 1;
  const canGoForward = pageNumber < numPages;

  useEffect(() => {
    if (!viewerRef.current) return;
    const observer = new ResizeObserver(([entry]) => setViewerWidth(entry.contentRect.width));
    observer.observe(viewerRef.current);
    return () => observer.disconnect();
  }, []);

  const runFind = () => {
    if (!search.trim()) return;
    const find = (window as Window & { find?: (text: string, caseSensitive?: boolean, backwards?: boolean, wrap?: boolean) => boolean }).find;
    find?.(search.trim(), false, false, true);
  };

  return (
    <section className="flex min-h-0 flex-col rounded-lg border border-slate-200 bg-white">
      <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
        <div>
          <h2 className="font-semibold text-slate-900">Source PDF</h2>
          <p className="text-xs text-slate-500">
            Page {numPages ? pageNumber : "—"} of {numPages || "—"}
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <div className="flex items-center gap-1">
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") runFind();
              }}
              placeholder="Find text"
              className="w-28 rounded border border-slate-300 px-2 py-1 text-sm outline-none focus:border-slate-500"
            />
            <button
              type="button"
              onClick={runFind}
              className="rounded border border-slate-300 px-2 py-1 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              Find
            </button>
          </div>
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className="rounded border border-slate-300 px-2 py-1 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Open
          </a>
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
            onClick={() => {
              setFitWidth(false);
              setScale((value) => Math.max(0.75, Number((value - 0.15).toFixed(2))));
            }}
            className="rounded border border-slate-300 px-2 py-1 text-sm font-medium text-slate-700"
          >
            −
          </button>
          <span className="w-16 text-center text-sm tabular-nums text-slate-600">{fitWidth ? "Fit" : `${Math.round(scale * 100)}%`}</span>
          <button
            type="button"
            onClick={() => {
              setFitWidth(false);
              setScale((value) => Math.min(1.75, Number((value + 0.15).toFixed(2))));
            }}
            className="rounded border border-slate-300 px-2 py-1 text-sm font-medium text-slate-700"
          >
            +
          </button>
          <button
            type="button"
            onClick={() => setFitWidth(true)}
            className="rounded border border-slate-300 px-2 py-1 text-sm font-medium text-slate-700"
          >
            Fit width
          </button>
          <button
            type="button"
            onClick={() => {
              setFitWidth(false);
              setScale(1);
            }}
            className="rounded border border-slate-300 px-2 py-1 text-sm font-medium text-slate-700"
          >
            100%
          </button>
          <button
            type="button"
            onClick={() => setRotation((value) => (value + 90) % 360)}
            className="rounded border border-slate-300 px-2 py-1 text-sm font-medium text-slate-700"
          >
            Rotate
          </button>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[84px_minmax(0,1fr)] bg-slate-100">
        <div className="overflow-y-auto border-r border-slate-200 bg-white p-2">
          <div className="space-y-1">
            {Array.from({ length: numPages }, (_, index) => index + 1).map((page) => (
              <button
                key={page}
                type="button"
                onClick={() => setPageNumber(page)}
                className={`w-full rounded px-2 py-2 text-sm font-medium ${page === pageNumber ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}
              >
                {page}
              </button>
            ))}
          </div>
        </div>
        <div ref={viewerRef} className="min-h-0 overflow-auto p-4">
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
            <Page
              pageNumber={pageNumber}
              scale={fitWidth ? undefined : scale}
              width={fitWidth && viewerWidth ? Math.max(320, viewerWidth - 40) : undefined}
              rotate={rotation}
              renderTextLayer
              renderAnnotationLayer
            />
          </Document>
          </div>
        </div>
      </div>
    </section>
  );
}
