import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { GlobalWorkerOptions, getDocument, type PDFDocumentProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { I18nProvider, useI18n, type Language } from "./i18n";

GlobalWorkerOptions.workerSrc = workerUrl;

type Rows = string[][];
type Status = "unreviewed" | "approved" | "ignored";
type ViewMode = "review" | "comparison";

interface TableSummary {
  id: string;
  page: number;
  source_pages: number[];
  name: string;
  status: Status;
  difference_count: number;
  lineage_action?: string;
}

interface SourceSummary {
  id: string;
  name: string;
  result_dir: string;
  pdf_available: boolean;
  page_count: number;
  selected_pages: number[];
  table_count: number;
  reviewed_count: number;
  application_state: "never_applied" | "applied" | "needs_reapply";
  last_applied_at?: string;
  draft_updated_at?: string;
  tables: TableSummary[];
}

interface Catalog {
  source_count: number;
  table_count: number;
  reviewed_count: number;
  sources: SourceSummary[];
}

interface BBox {
  x0: number;
  top: number;
  x1: number;
  bottom: number;
}

interface Fragment {
  id: string;
  page: number;
  status: string;
  markdown: string;
  rows: Rows;
}

interface Draft {
  status: Status;
  selected_source?: string;
  note?: string;
  rows?: Rows;
}

interface Detail {
  source_id: string;
  table: TableSummary & {
    bbox: BBox;
    header_rows: number;
    lineage?: { action: string; docling_table_ids: string[] };
  };
  ai_rows: Rows;
  docling_fragments: Fragment[];
  docling_rows: Rows;
  preview_regions: Record<string, BBox | undefined>;
  pdf_url: string;
  draft?: Draft;
}

interface ComparisonPayload {
  source_id: string;
  name: string;
  page_count: number;
  selected_pages: number[];
  pdf_url: string;
  artifact_base_url: string;
  markdown: string;
}

const statusIcons: Record<Status, string> = {
  unreviewed: "○",
  approved: "✓",
  ignored: "–",
};

async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

function cloneRows(rows: Rows): Rows {
  return rows.map((row) => [...row]);
}

function columnLabel(index: number): string {
  let value = index + 1;
  let label = "";
  while (value > 0) {
    label = String.fromCharCode(65 + ((value - 1) % 26)) + label;
    value = Math.floor((value - 1) / 26);
  }
  return label;
}

function pageLabel(table: TableSummary): string {
  const pages = table.source_pages;
  return pages.length > 1 ? `P${pages[0]}–${pages[pages.length - 1]}` : `P${table.page}`;
}

function countDifferences(ai: Rows, docling: Rows): number {
  const rowCount = Math.max(ai.length, docling.length);
  const width = Math.max(
    ...ai.map((row) => row.length),
    ...docling.map((row) => row.length),
    0,
  );
  let count = 0;
  for (let row = 0; row < rowCount; row += 1) {
    for (let column = 0; column < width; column += 1) {
      count += Number((ai[row]?.[column] || "") !== (docling[row]?.[column] || ""));
    }
  }
  return count;
}

function Sidebar({
  catalog,
  selected,
  selectedSourceId,
  viewMode,
  collapsed,
  onSelect,
  onSelectSource,
  onViewMode,
  onToggleCollapsed,
  saving,
  onPublish,
}: {
  catalog: Catalog;
  selected?: string;
  selectedSourceId?: string;
  viewMode: ViewMode;
  collapsed: boolean;
  onSelect: (sourceId: string, tableId: string) => void;
  onSelectSource: (sourceId: string) => void;
  onViewMode: (mode: ViewMode) => void;
  onToggleCollapsed: () => void;
  saving: boolean;
  onPublish: () => void;
}) {
  const { language, setLanguage, t } = useI18n();
  const [filter, setFilter] = useState("");
  const [onlyPending, setOnlyPending] = useState(false);
  const selectedReviewSourceId = selected?.split(":", 1)[0];
  const selectedSource = catalog.sources.find((source) => source.id === selectedReviewSourceId);
  const applicationLabel = selectedSource?.application_state === "applied"
    ? t("applicationApplied")
    : selectedSource?.application_state === "needs_reapply"
      ? t("applicationStale")
      : t("applicationNever");
  return (
    <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
      <button className="sidebar-toggle" title={t(collapsed ? "showSidebar" : "hideSidebar")} aria-label={t(collapsed ? "showSidebar" : "hideSidebar")} onClick={onToggleCollapsed}>{collapsed ? "›" : "‹"}</button>
      <div className="brand">
        <div className="brand-mark">TR</div>
        <div>
          <strong>{t("brand")}</strong>
          <span>{t("progress", { reviewed: catalog.reviewed_count, total: catalog.table_count })}</span>
        </div>
        <select className="language-select" aria-label={t("language")} value={language} onChange={(event) => setLanguage(event.target.value as Language)}>
          <option value="de">DE</option>
          <option value="en">EN</option>
        </select>
      </div>
      <div className="overall-progress">
        <i style={{ width: `${catalog.table_count ? (catalog.reviewed_count / catalog.table_count) * 100 : 0}%` }} />
      </div>
      <div className="view-switch">
        <button className={viewMode === "review" ? "active" : ""} onClick={() => onViewMode("review")}>{t("tableReview")}</button>
        <button className={viewMode === "comparison" ? "active" : ""} onClick={() => onViewMode("comparison")}>{t("documentComparison")}</button>
      </div>
      {viewMode === "review" ? <div className="filters">
        <input
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder={t("search")}
        />
        <label>
          <input
            type="checkbox"
            checked={onlyPending}
            onChange={(event) => setOnlyPending(event.target.checked)}
          />
          {t("pendingOnly")}
        </label>
      </div> : null}
      {viewMode === "review" ? <div className="source-list">
        {catalog.sources.map((source) => {
          const tables = source.tables.filter((table) => {
            const matches = `${table.name} ${pageLabel(table)}`.toLowerCase().includes(filter.toLowerCase());
            const pending = table.status === "unreviewed";
            return matches && (!onlyPending || pending);
          });
          return (
            <section className="source-group" key={source.id}>
              <header>
                <span title={source.name}>{source.name}</span>
                <small>{source.reviewed_count}/{source.table_count}</small>
              </header>
              {tables.map((table) => {
                const key = `${source.id}:${table.id}`;
                return (
                  <button
                    className={`table-nav ${selected === key ? "selected" : ""} status-${table.status}`}
                    key={key}
                    onClick={() => onSelect(source.id, table.id)}
                  >
                    <span className="status-icon">{statusIcons[table.status]}</span>
                    <span className="nav-copy">
                      <b><span>{pageLabel(table)} · {table.id}</span><small className={table.difference_count === 0 ? "diff-zero" : "diff-found"}>{t("difference", { count: table.difference_count })}</small></b>
                      <em>{table.name}</em>
                    </span>
                  </button>
                );
              })}
            </section>
          );
        })}
      </div> : <div className="source-list comparison-source-list">
        {catalog.sources.map((source) => <button
          className={`comparison-source ${selectedSourceId === source.id ? "selected" : ""}`}
          key={source.id}
          onClick={() => onSelectSource(source.id)}
        >
          <span title={source.name}>{source.name}</span>
          <small>{t("pageCount", { count: source.page_count })}</small>
        </button>)}
      </div>}
      {viewMode === "review" && selected ? <div className="sidebar-review">
        <div className={`application-state ${selectedSource?.application_state || "never_applied"}`}>
          <span>{applicationLabel}</span>
          {selectedSource?.last_applied_at ? <time title={selectedSource.last_applied_at}>{t("recorded")}</time> : null}
        </div>
        <div className="sidebar-actions">
          <button className="primary" disabled={saving} onClick={onPublish}>{t("applyPdf")}</button>
        </div>
      </div> : null}
    </aside>
  );
}

function PDFPreview({
  url,
  page,
  region,
  pages,
  onPage,
  available,
}: {
  url: string;
  page: number;
  region?: BBox;
  pages: number[];
  onPage: (page: number) => void;
  available: boolean;
}) {
  const { t } = useI18n();
  const canvas = useRef<HTMLCanvasElement>(null);
  const [document, setDocument] = useState<PDFDocumentProxy>();
  const [zoom, setZoom] = useState(1);
  const [focus, setFocus] = useState(true);
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
  const [error, setError] = useState("");

  useEffect(() => {
    if (!available) return;
    let active = true;
    const task = getDocument({ url });
    task.promise.then((value) => active && setDocument(value)).catch((reason) => setError(String(reason)));
    return () => {
      active = false;
      task.destroy();
    };
  }, [url, available]);

  useEffect(() => {
    if (!document || !canvas) return;
    let cancelled = false;
    let renderTask: ReturnType<Awaited<ReturnType<typeof document.getPage>>["render"]> | undefined;
    document.getPage(page).then((pdfPage) => {
      if (cancelled || !canvas.current) return;
      const scale = 1.35 * zoom;
      const viewport = pdfPage.getViewport({ scale });
      const outputScale = window.devicePixelRatio || 1;
      const target = canvas.current;
      target.width = Math.floor(viewport.width * outputScale);
      target.height = Math.floor(viewport.height * outputScale);
      target.style.width = `${viewport.width}px`;
      target.style.height = `${viewport.height}px`;
      setViewportSize({ width: viewport.width, height: viewport.height });
      const context = target.getContext("2d")!;
      renderTask = pdfPage.render({
        canvas: target,
        canvasContext: context,
        viewport,
        transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
      });
      return renderTask.promise;
    }).catch((reason) => {
      if (!cancelled && !String(reason).includes("RenderingCancelledException")) setError(String(reason));
    });
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [document, page, zoom]);

  if (!available) return <div className="pdf-empty">{t("pdfUnavailable")}</div>;
  const scale = 1.35 * zoom;
  const padding = 24;
  const focused = Boolean(focus && region);
  const stageStyle = focused && region
    ? { width: (region.x1 - region.x0) * scale + padding * 2, height: (region.bottom - region.top) * scale + padding * 2 }
    : { width: viewportSize.width, height: viewportSize.height };
  const canvasStyle = focused && region
    ? { left: padding - region.x0 * scale, top: padding - region.top * scale }
    : { left: 0, top: 0 };
  const overlayStyle = region
    ? focused
      ? { left: padding, top: padding, width: (region.x1 - region.x0) * scale, height: (region.bottom - region.top) * scale }
      : { left: region.x0 * scale, top: region.top * scale, width: (region.x1 - region.x0) * scale, height: (region.bottom - region.top) * scale }
    : undefined;
  return (
    <div className="pdf-panel">
      <div className="pdf-toolbar">
        <div className="page-chips">
          {pages.map((value) => <button key={value} className={value === page ? "active" : ""} onClick={() => onPage(value)}>P{value}</button>)}
        </div>
        <div className="viewer-actions">
          <button onClick={() => setFocus(!focus)}>{focus ? t("showFullPage") : t("focusTable")}</button>
          <button onClick={() => setZoom(Math.max(0.6, zoom - 0.15))}>−</button>
          <span>{Math.round(zoom * 100)}%</span>
          <button onClick={() => setZoom(Math.min(2, zoom + 0.15))}>＋</button>
        </div>
      </div>
      <div className={`pdf-scroll ${focused ? "focused" : ""}`}>
        {error ? <div className="error-box">{error}</div> : null}
        <div className="pdf-stage" style={stageStyle}>
          <canvas ref={canvas} style={canvasStyle} />
          {overlayStyle ? <div className="bbox-overlay" style={overlayStyle} /> : null}
        </div>
      </div>
    </div>
  );
}

function TableGrid({
  rows,
  editable = false,
  onChange,
  columnWidthKey,
  physicalPages,
  pageWeights,
  activePhysicalPage,
  onPhysicalPage,
}: {
  rows: Rows;
  editable?: boolean;
  onChange?: (rows: Rows) => void;
  columnWidthKey?: string;
  physicalPages?: number[];
  pageWeights?: number[];
  activePhysicalPage?: number;
  onPhysicalPage?: (page: number) => void;
}) {
  const { t } = useI18n();
  const pageSize = 80;
  const [localPage, setLocalPage] = useState(0);
  const [gridSelection, setGridSelection] = useState<{ kind: "row" | "column"; index: number }>();
  const width = Math.max(...rows.map((row) => row.length), 0);
  const [columnWidths, setColumnWidths] = useState<number[]>([]);
  const linkedPages = physicalPages?.length ? physicalPages : undefined;
  const pageCount = linkedPages?.length || Math.max(1, Math.ceil(rows.length / pageSize));
  const linkedPageIndex = linkedPages
    ? Math.max(0, linkedPages.indexOf(activePhysicalPage ?? linkedPages[0]))
    : -1;
  const page = linkedPageIndex >= 0 ? linkedPageIndex : Math.min(localPage, pageCount - 1);
  const rowBounds = useMemo(() => {
    if (!linkedPages) return undefined;
    const weights = linkedPages.map((_, index) => Math.max(0, pageWeights?.[index] ?? 1));
    const totalWeight = weights.reduce((sum, value) => sum + value, 0) || linkedPages.length;
    const bounds = [0];
    let cumulative = 0;
    weights.forEach((weight, index) => {
      cumulative += weight;
      bounds.push(index + 1 === weights.length ? rows.length : Math.round((cumulative / totalWeight) * rows.length));
    });
    return bounds;
  }, [linkedPages, pageWeights, rows.length]);
  useEffect(() => setLocalPage(0), [rows.length]);
  useEffect(() => setGridSelection(undefined), [rows.length, width]);
  useEffect(() => {
    let stored: number[] = [];
    if (columnWidthKey) {
      try {
        const parsed = JSON.parse(window.localStorage.getItem(columnWidthKey) || "[]");
        if (Array.isArray(parsed)) stored = parsed.filter((value) => typeof value === "number");
      } catch {
        stored = [];
      }
    }
    setColumnWidths(Array.from({ length: width }, (_, index) => stored[index] || 160));
  }, [columnWidthKey, width]);
  useEffect(() => {
    if (!columnWidthKey || columnWidths.length !== width) return;
    window.localStorage.setItem(columnWidthKey, JSON.stringify(columnWidths));
  }, [columnWidthKey, columnWidths, width]);
  const pageStart = rowBounds?.[page] ?? page * pageSize;
  const pageEnd = rowBounds?.[page + 1] ?? (page + 1) * pageSize;
  const visible = rows.slice(pageStart, pageEnd);
  const changePage = (nextPage: number) => {
    if (linkedPages) onPhysicalPage?.(linkedPages[nextPage]);
    else setLocalPage(nextPage);
  };
  const update = (rowIndex: number, column: number, value: string) => {
    const copy = cloneRows(rows);
    copy[rowIndex][column] = value;
    onChange?.(copy);
  };
  const insertRow = (rowIndex: number) => {
    const copy = cloneRows(rows);
    copy.splice(rowIndex, 0, Array(width || 1).fill(""));
    onChange?.(copy);
  };
  const removeRow = (rowIndex: number) => {
    if (rows.length <= 1) return;
    onChange?.(rows.filter((_, index) => index !== rowIndex));
  };
  const insertColumn = (column: number) => onChange?.(rows.map((row) => {
    const copy = [...row, ...Array(Math.max(0, width - row.length)).fill("")];
    copy.splice(column, 0, "");
    return copy;
  }));
  const removeColumn = (column: number) => {
    if (width <= 1) return;
    onChange?.(rows.map((row) => {
      const copy = [...row, ...Array(Math.max(0, width - row.length)).fill("")];
      copy.splice(column, 1);
      return copy;
    }));
  };
  const beginColumnResize = (column: number, event: ReactMouseEvent<HTMLSpanElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const startX = event.clientX;
    const startWidth = columnWidths[column] || 160;
    const onMove = (moveEvent: MouseEvent) => {
      const nextWidth = Math.min(640, Math.max(72, startWidth + moveEvent.clientX - startX));
      setColumnWidths((current) => current.map((value, index) => index === column ? nextWidth : value));
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.classList.remove("resizing-column");
    };
    document.body.classList.add("resizing-column");
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };
  if (!rows.length) return <div className="empty-table">{t("emptyTable")}</div>;
  return (
    <div className="grid-wrap">
      <div className="grid-meta">
        <span>{t("dimensions", { rows: rows.length, columns: width })}</span>
        <div className="grid-controls">
          {editable && gridSelection?.kind === "row" ? <div className="selection-tools">
            <b>{t("rowNumber", { number: gridSelection.index + 1 })}</b>
            <button onClick={() => insertRow(gridSelection.index)}>{t("insertAbove")}</button>
            <button onClick={() => insertRow(gridSelection.index + 1)}>{t("insertBelow")}</button>
            <button className="danger" disabled={rows.length <= 1} onClick={() => removeRow(gridSelection.index)}>{t("deleteRow")}</button>
          </div> : null}
          {editable && gridSelection?.kind === "column" ? <div className="selection-tools">
            <b>{t("columnNumber", { label: columnLabel(gridSelection.index) })}</b>
            <button onClick={() => insertColumn(gridSelection.index)}>{t("insertLeft")}</button>
            <button onClick={() => insertColumn(gridSelection.index + 1)}>{t("insertRight")}</button>
            <button className="danger" disabled={width <= 1} onClick={() => removeColumn(gridSelection.index)}>{t("deleteColumn")}</button>
          </div> : null}
          {pageCount > 1 ? <><button disabled={page === 0} onClick={() => changePage(page - 1)}>{t("previousPage")}</button><span>P{linkedPages?.[page] ?? page + 1} · {page + 1}/{pageCount}</span><button disabled={page + 1 === pageCount} onClick={() => changePage(page + 1)}>{t("nextPage")}</button></> : null}
        </div>
      </div>
      <div className="table-scroll">
        <table className="data-grid">
          {editable ? <thead className="sheet-columns"><tr>
            <th className="sheet-corner" />
            {Array.from({ length: width }, (_, column) => <th key={column} style={{ width: columnWidths[column], minWidth: columnWidths[column], maxWidth: columnWidths[column] }} className={gridSelection?.kind === "column" && gridSelection.index === column ? "selected" : ""}>
              <button title={t("selectColumn", { label: columnLabel(column) })} onClick={() => setGridSelection({ kind: "column", index: column })}>{columnLabel(column)}</button>
              <span className="column-resizer" title={t("resizeColumn")} onMouseDown={(event) => beginColumnResize(column, event)} />
            </th>)}
          </tr></thead> : null}
          <tbody>
            {visible.map((row, visibleIndex) => {
              const rowIndex = pageStart + visibleIndex;
              return (
                <tr key={rowIndex} className={rowIndex === 0 ? "header-row" : ""}>
                  <th className="row-number">
                    {editable
                      ? <button className={gridSelection?.kind === "row" && gridSelection.index === rowIndex ? "selected" : ""} title={t("selectRow", { number: rowIndex + 1 })} onClick={() => setGridSelection({ kind: "row", index: rowIndex })}>{rowIndex + 1}</button>
                      : rowIndex + 1}
                  </th>
                  {Array.from({ length: width }, (_, column) => (
                    <td key={column} style={editable ? { width: columnWidths[column], minWidth: columnWidths[column], maxWidth: columnWidths[column] } : undefined}>
                      {editable ? <textarea value={row[column] || ""} onChange={(event) => update(rowIndex, column, event.target.value)} /> : <span>{row[column] || " "}</span>}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DiffView({ ai, docling, count }: { ai: Rows; docling: Rows; count: number }) {
  const { t } = useI18n();
  const rowCount = Math.max(ai.length, docling.length);
  const width = Math.max(...ai.map((row) => row.length), ...docling.map((row) => row.length), 0);
  const differences = Array.from({ length: rowCount }, (_, row) =>
    Array.from({ length: width }, (_, column) => (ai[row]?.[column] || "") !== (docling[row]?.[column] || "")),
  );
  return (
    <div className="diff-view">
      <div className="diff-summary">{count ? t("diffCells", { count }) : t("diffEqual")}</div>
      <div className="table-scroll">
        <table className="data-grid diff-grid"><tbody>
          {differences.slice(0, 200).map((row, rowIndex) => <tr key={rowIndex}>
            <th className="row-number">{rowIndex + 1}</th>
            {row.map((different, column) => <td key={column} className={different ? "different" : ""}>
              <small>AI</small><span>{ai[rowIndex]?.[column] || " "}</span>
              <small>Docling</small><span>{docling[rowIndex]?.[column] || " "}</span>
            </td>)}
          </tr>)}
        </tbody></table>
      </div>
    </div>
  );
}

function markdownPages(markdown: string): { page: number; content: string }[] {
  const marker = /<!--\s*page:(\d+)\s*-->/g;
  const found = [...markdown.matchAll(marker)];
  if (!found.length) return [{ page: 1, content: markdown }];
  const byPage = new Map<number, string>();
  found.forEach((match, index) => {
    const page = Number(match[1]);
    const start = (match.index || 0) + match[0].length;
    const end = found[index + 1]?.index ?? markdown.length;
    const content = markdown.slice(start, end).replace(/<!--[\s\S]*?-->/g, "").trim();
    byPage.set(page, `${byPage.get(page) || ""}\n${content}`.trim());
  });
  return [...byPage].map(([page, content]) => ({ page, content }));
}

function publicArtifactUrl(base: string, relative?: string): string | undefined {
  if (!relative) return undefined;
  if (!relative.startsWith("assets/")) return relative.startsWith("#") ? relative : undefined;
  return `${base}${relative.split("/").map(encodeURIComponent).join("/")}`;
}

function ComparisonPDFPage({
  document,
  page,
  scrollRoot,
  zoom,
}: {
  document: PDFDocumentProxy;
  page: number;
  scrollRoot: HTMLDivElement | null;
  zoom: number;
}) {
  const host = useRef<HTMLElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [visible, setVisible] = useState(false);
  const [ratio, setRatio] = useState(1 / 1.4142);
  const [error, setError] = useState("");

  useEffect(() => {
    document.getPage(page).then((pdfPage) => {
      const viewport = pdfPage.getViewport({ scale: 1 });
      setRatio(viewport.width / viewport.height);
    }).catch((reason) => setError(String(reason)));
  }, [document, page]);

  useEffect(() => {
    if (!host.current) return;
    const observer = new IntersectionObserver(
      ([entry]) => entry.isIntersecting && setVisible(true),
      { root: scrollRoot, rootMargin: "900px 0px" },
    );
    observer.observe(host.current);
    return () => observer.disconnect();
  }, [scrollRoot]);

  useEffect(() => {
    if (!visible || !canvas.current) return;
    let cancelled = false;
    let renderTask: ReturnType<Awaited<ReturnType<typeof document.getPage>>["render"]> | undefined;
    document.getPage(page).then((pdfPage) => {
      if (cancelled || !canvas.current) return;
      const viewport = pdfPage.getViewport({ scale: 1.35 * zoom });
      const outputScale = window.devicePixelRatio || 1;
      canvas.current.width = Math.floor(viewport.width * outputScale);
      canvas.current.height = Math.floor(viewport.height * outputScale);
      const context = canvas.current.getContext("2d")!;
      renderTask = pdfPage.render({
        canvas: canvas.current,
        canvasContext: context,
        viewport,
        transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
      });
      return renderTask.promise;
    }).catch((reason) => {
      if (!cancelled && !String(reason).includes("RenderingCancelledException")) setError(String(reason));
    });
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [document, page, visible, zoom]);

  return <article className="comparison-pdf-page" data-page={page} ref={host} style={{ aspectRatio: ratio, width: `${zoom * 100}%` }}>
    <span className="comparison-page-label">P{page}</span>
    {error ? <span className="comparison-page-error">{error}</span> : null}
    <canvas ref={canvas} />
  </article>;
}

function ContinuousPDF({
  url,
  available,
  pageCount,
  pages,
  scrollRoot,
  zoom,
}: {
  url: string;
  available: boolean;
  pageCount: number;
  pages: number[];
  scrollRoot: HTMLDivElement | null;
  zoom: number;
}) {
  const { t } = useI18n();
  const [document, setDocument] = useState<PDFDocumentProxy>();
  const [error, setError] = useState("");
  useEffect(() => {
    setDocument(undefined);
    setError("");
    if (!available) return;
    let active = true;
    const task = getDocument({ url });
    task.promise.then((value) => active && setDocument(value)).catch((reason) => active && setError(String(reason)));
    return () => {
      active = false;
      task.destroy();
    };
  }, [available, url]);
  if (!available) return <div className="pdf-empty">{t("pdfUnavailable")}</div>;
  if (error) return <div className="error-box comparison-load-error">{error}</div>;
  if (!document) return <div className="comparison-loading">{t("loadingPdf")}</div>;
  return <div className="comparison-pdf-pages">
    {(pages.length ? pages : Array.from({ length: document.numPages || pageCount }, (_, index) => index + 1)).map((page) => (
      <ComparisonPDFPage key={page} document={document} page={page} scrollRoot={scrollRoot} zoom={zoom} />
    ))}
  </div>;
}

function ComparisonView({ source }: { source: SourceSummary }) {
  const { t } = useI18n();
  const [comparison, setComparison] = useState<ComparisonPayload>();
  const [error, setError] = useState("");
  const [syncEnabled, setSyncEnabled] = useState(true);
  const [pdfZoom, setPdfZoom] = useState(1);
  const left = useRef<HTMLDivElement>(null);
  const right = useRef<HTMLDivElement>(null);
  const synchronizing = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setComparison(undefined);
    setError("");
    api<ComparisonPayload>(`/api/sources/${source.id}/comparison`)
      .then(setComparison)
      .catch((reason) => setError(String(reason)));
  }, [source.id]);

  const pages = useMemo(
    () => markdownPages(comparison?.markdown || ""),
    [comparison],
  );

  const synchronize = useCallback((origin: HTMLDivElement | null, target: HTMLDivElement | null) => {
    if (!syncEnabled || !origin || !target || synchronizing.current === origin) return;
    const originPages = Array.from(origin.querySelectorAll<HTMLElement>("[data-page]"));
    const targetPages = Array.from(target.querySelectorAll<HTMLElement>("[data-page]"));
    if (!originPages.length || !targetPages.length) return;
    const anchor = origin.scrollTop + 24;
    const originPage = [...originPages].reverse().find((item) => item.offsetTop <= anchor) || originPages[0];
    const page = originPage.dataset.page;
    const targetPage = targetPages.find((item) => item.dataset.page === page) || targetPages[0];
    const progress = Math.max(0, Math.min(1, (anchor - originPage.offsetTop) / Math.max(1, originPage.offsetHeight)));
    synchronizing.current = target;
    target.scrollTop = Math.max(0, targetPage.offsetTop + progress * targetPage.offsetHeight - 24);
    window.requestAnimationFrame(() => {
      if (synchronizing.current === target) synchronizing.current = null;
    });
  }, [syncEnabled]);

  if (error) return <div className="empty-workspace error-box">{error}</div>;
  if (!comparison) return <div className="empty-workspace">{t("loadingComparison")}</div>;
  return <main className="comparison-workspace">
    <header className="comparison-header">
      <div><strong>{comparison.name}</strong><span>{t("comparisonHint")}</span></div>
      <label className="sync-toggle"><input type="checkbox" checked={syncEnabled} onChange={(event) => setSyncEnabled(event.target.checked)} />{t("syncScroll")}</label>
    </header>
    <div className="comparison-columns">
      <section className="comparison-pane">
        <div className="comparison-pane-title">
          <strong>PDF</strong>
          <div className="comparison-zoom">
            <span>{t("pageCount", { count: comparison.selected_pages.length || comparison.page_count })}</span>
            <button aria-label={t("zoomOut")} title={t("zoomOut")} disabled={pdfZoom <= 0.6} onClick={() => setPdfZoom((value) => Math.max(0.6, value - 0.2))}>−</button>
            <b>{Math.round(pdfZoom * 100)}%</b>
            <button aria-label={t("zoomIn")} title={t("zoomIn")} disabled={pdfZoom >= 2} onClick={() => setPdfZoom((value) => Math.min(2, value + 0.2))}>＋</button>
          </div>
        </div>
        <div className="comparison-scroll pdf-comparison-scroll" ref={left} onScroll={() => synchronize(left.current, right.current)}>
          <ContinuousPDF url={comparison.pdf_url} available={source.pdf_available} pageCount={comparison.page_count} pages={comparison.selected_pages} scrollRoot={left.current} zoom={pdfZoom} />
        </div>
      </section>
      <section className="comparison-pane">
        <div className="comparison-pane-title"><strong>Markdown</strong><span>output.md</span></div>
        <div className="comparison-scroll markdown-comparison-scroll" ref={right} onScroll={() => synchronize(right.current, left.current)}>
          {pages.map(({ page, content }) => <article className="markdown-page" data-page={page} key={page}>
            <span className="comparison-page-label">P{page}</span>
            {content ? <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                img: ({ src, alt }) => <img src={publicArtifactUrl(comparison.artifact_base_url, src)} alt={alt || ""} loading="lazy" />,
                a: ({ href, children }) => <a href={publicArtifactUrl(comparison.artifact_base_url, href) || href} target="_blank" rel="noreferrer">{children}</a>,
              }}
            >{content}</ReactMarkdown> : <p className="empty-markdown-page">{t("emptyMarkdownPage")}</p>}
          </article>)}
        </div>
      </section>
    </div>
  </main>;
}

function ReviewApp() {
  const { t } = useI18n();
  const [catalog, setCatalog] = useState<Catalog>();
  const [viewMode, setViewMode] = useState<ViewMode>("review");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [comparisonSourceId, setComparisonSourceId] = useState<string>();
  const [selection, setSelection] = useState<{ sourceId: string; tableId: string }>();
  const [detail, setDetail] = useState<Detail>();
  const [activeTab, setActiveTab] = useState("ai");
  const [fragment, setFragment] = useState("combined");
  const [rows, setRows] = useState<Rows>([]);
  const [status, setStatus] = useState<Status>("unreviewed");
  const [selectedSource, setSelectedSource] = useState("ai");
  const [note, setNote] = useState("");
  const [showNote, setShowNote] = useState(false);
  const [page, setPage] = useState(1);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const reloadCatalog = useCallback(async () => {
    const value = await api<Catalog>("/api/project");
    setCatalog(value);
    setComparisonSourceId((current) => current || value.sources[0]?.id);
    if (!selection && value.sources[0]?.tables[0]) {
      setSelection({ sourceId: value.sources[0].id, tableId: value.sources[0].tables[0].id });
    }
  }, [selection]);

  useEffect(() => { reloadCatalog().catch((reason) => setError(String(reason))); }, []);
  useEffect(() => {
    if (!selection) return;
    setError("");
    api<Detail>(`/api/sources/${selection.sourceId}/tables/${selection.tableId}`)
      .then((value) => {
        setDetail(value);
        setRows(cloneRows(value.draft?.rows || value.ai_rows));
        setStatus(value.draft?.status || "unreviewed");
        setSelectedSource(value.draft?.selected_source || "ai");
        setNote(value.draft?.note || "");
        setPage(value.table.page);
        setActiveTab("final");
        setFragment("combined");
        setShowNote(false);
        setDirty(false);
      })
      .catch((reason) => setError(String(reason)));
  }, [selection]);

  const save = useCallback(async () => {
    if (!selection || !dirty) return;
    setSaving(true);
    setMessage("");
    try {
      await api(`/api/sources/${selection.sourceId}/tables/${selection.tableId}/draft`, {
        method: "PUT",
        body: JSON.stringify({ status, selected_source: selectedSource, note, rows }),
      });
      setDirty(false);
      setMessage(t("draftSaved"));
      await reloadCatalog();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setSaving(false);
    }
  }, [selection, dirty, status, selectedSource, note, rows, reloadCatalog, t]);

  useEffect(() => {
    if (!dirty) return;
    const timer = window.setTimeout(save, 900);
    return () => window.clearTimeout(timer);
  }, [dirty, save]);

  const useRows = (nextRows: Rows, source: string) => {
    setRows(cloneRows(nextRows));
    setSelectedSource(source);
    setActiveTab("final");
    setDirty(true);
  };
  const editRows = (nextRows: Rows) => {
    setRows(nextRows);
    setSelectedSource("custom");
    setDirty(true);
  };
  const publish = async () => {
    if (!selection) return;
    if (dirty) await save();
    if (!window.confirm(t("applyConfirm"))) return;
    try {
      setSaving(true);
      const result = await api<{ published_tables: string[] }>(`/api/sources/${selection.sourceId}/publish`, { method: "POST" });
      setMessage(t("appliedCount", { count: result.published_tables.length }));
      await reloadCatalog();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setSaving(false);
    }
  };

  const source = catalog?.sources.find((item) => item.id === selection?.sourceId);
  const comparisonSource = catalog?.sources.find((item) => item.id === comparisonSourceId) || catalog?.sources[0];
  const tableIndex = source?.tables.findIndex((table) => table.id === selection?.tableId) ?? -1;
  const moveTable = async (offset: number) => {
    if (!source || tableIndex < 0) return;
    const target = source.tables[tableIndex + offset];
    if (!target) return;
    if (dirty) await save();
    setSelection({ sourceId: source.id, tableId: target.id });
  };
  const differenceCount = useMemo(
    () => countDifferences(detail?.ai_rows || [], detail?.docling_rows || []),
    [detail],
  );
  const physicalPages = detail?.table.source_pages || [];
  const pageWeights = useMemo(() => {
    if (!detail) return [];
    const rowsByPage = new Map(detail.docling_fragments.map((item) => [item.page, item.rows.length]));
    return detail.table.source_pages.map((physicalPage) => rowsByPage.get(physicalPage) || 1);
  }, [detail]);
  useEffect(() => {
    if (!detail || fragment === "combined") return;
    const matchingFragment = detail.docling_fragments.find((item) => item.page === page);
    setFragment(matchingFragment?.id || "combined");
  }, [detail, fragment, page]);
  const region = detail?.preview_regions[String(page)];
  const fragmentRows = fragment === "combined"
    ? detail?.docling_rows || []
    : detail?.docling_fragments.find((item) => item.id === fragment)?.rows || [];
  const rawMarkdown = detail?.docling_fragments.find((item) => item.id === fragment)?.markdown;
  const saveState = saving
    ? { label: t("saving"), className: "saving" }
    : dirty
      ? { label: t("unsaved"), className: "dirty" }
      : { label: message || t("noUnsaved"), className: "saved" };

  if (!catalog) return <main className="loading">{t("loading")}</main>;
  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      <Sidebar
        catalog={catalog}
        selected={selection ? `${selection.sourceId}:${selection.tableId}` : undefined}
        selectedSourceId={comparisonSource?.id}
        viewMode={viewMode}
        collapsed={sidebarCollapsed}
        onSelect={(sourceId, tableId) => {
          setSelection({ sourceId, tableId });
          setComparisonSourceId(sourceId);
          setViewMode("review");
        }}
        onSelectSource={(sourceId) => {
          setComparisonSourceId(sourceId);
          setViewMode("comparison");
        }}
        onViewMode={setViewMode}
        onToggleCollapsed={() => setSidebarCollapsed((value) => !value)}
        saving={saving}
        onPublish={publish}
      />
      {viewMode === "comparison" && comparisonSource ? <ComparisonView source={comparisonSource} /> : <main className="workspace">
        {detail && source ? <>
          <PDFPreview url={detail.pdf_url} page={page} region={region} pages={detail.table.source_pages} onPage={setPage} available={source.pdf_available} />
          <section className="review-panel">
            <div className="tabs">
              {[["final", t("finalTab")], ["ai", t("aiTab")], ["docling", `Docling (${detail.docling_fragments.length})`], ["diff", t("diffTab", { count: differenceCount })]].map(([key, label]) => <button key={key} className={activeTab === key ? "active" : ""} onClick={() => setActiveTab(key)}>{label}</button>)}
              <div className="review-actions">
                <span className={`save-state ${saveState.className}`}>{saveState.label}</span>
                <button className="table-step" title={t("previousTable")} disabled={tableIndex <= 0 || saving} onClick={() => void moveTable(-1)}>&lt;</button>
                <button className="table-step" title={t("nextTable")} disabled={!source || tableIndex < 0 || tableIndex >= source.tables.length - 1 || saving} onClick={() => void moveTable(1)}>&gt;</button>
                <button className={`note-trigger ${note ? "has-note" : ""}`} onClick={() => setShowNote(!showNote)}>{t("note")}{note ? " •" : ""}</button>
                <button className={status === "approved" ? "status-active" : "secondary"} onClick={() => { setStatus(status === "approved" ? "unreviewed" : "approved"); setDirty(true); }}>{t("approve")}</button>
                <button className={status === "ignored" ? "status-active ignored" : "secondary"} onClick={() => { setStatus(status === "ignored" ? "unreviewed" : "ignored"); setDirty(true); }}>{t("ignore")}</button>
                {showNote ? <div className="note-popover">
                  <label>{t("reviewNote")}</label>
                  <textarea
                    autoFocus
                    value={note}
                    onChange={(event) => { setNote(event.target.value); setDirty(true); }}
                    placeholder={t("notePlaceholder")}
                  />
                  <button onClick={() => setShowNote(false)}>{t("done")}</button>
                </div> : null}
              </div>
            </div>
            <div className="tab-body">
              {activeTab === "final" ? <TableGrid rows={rows} editable onChange={editRows} columnWidthKey={`pdf-table-review-widths:${detail.source_id}:${detail.table.id}`} physicalPages={physicalPages} pageWeights={pageWeights} activePhysicalPage={page} onPhysicalPage={setPage} /> : null}
              {activeTab === "ai" ? <><div className="source-actions"><span>{t("aiSource")}</span><button onClick={() => useRows(detail.ai_rows, "ai")}>{t("restoreAi")}</button></div><TableGrid rows={detail.ai_rows} physicalPages={physicalPages} pageWeights={pageWeights} activePhysicalPage={page} onPhysicalPage={setPage} /></> : null}
              {activeTab === "docling" ? <>
                <div className="source-actions"><div className="fragment-tabs"><button className={fragment === "combined" ? "active" : ""} onClick={() => setFragment("combined")}>{t("continuousView")}</button>{detail.docling_fragments.map((item) => <button key={item.id} className={fragment === item.id ? "active" : ""} onClick={() => { setFragment(item.id); setPage(item.page); }}>P{item.page}</button>)}</div><button disabled={!fragmentRows.length} onClick={() => useRows(fragmentRows, "docling")}>{t("useAsFinal")}</button></div>
                <TableGrid rows={fragmentRows} physicalPages={fragment === "combined" ? physicalPages : undefined} pageWeights={fragment === "combined" ? pageWeights : undefined} activePhysicalPage={page} onPhysicalPage={setPage} />
                {rawMarkdown ? <details className="raw-markdown"><summary>{t("rawMarkdown")}</summary><pre>{rawMarkdown}</pre></details> : null}
              </> : null}
              {activeTab === "diff" ? <DiffView ai={detail.ai_rows} docling={detail.docling_rows} count={differenceCount} /> : null}
            </div>
          </section>
        </> : <div className="empty-workspace">{t("selectTable")}</div>}
        {error ? <div className="toast error-box" onClick={() => setError("")}>{error}</div> : null}
      </main>}
    </div>
  );
}

export default function App() {
  return <I18nProvider><ReviewApp /></I18nProvider>;
}
