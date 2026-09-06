import { useCallback, useEffect, useMemo, useState } from "react";
import { ComparisonView } from "./components/ComparisonView";
import { PDFPreview } from "./components/PDFPreview";
import { Sidebar } from "./components/Sidebar";
import { DiffView, TableGrid } from "./components/TableGrid";
import { I18nProvider, useI18n } from "./i18n";
import type { Catalog, Detail, ReviewRoute, Rows, SourceSummary, Status, ViewMode } from "./reviewTypes";
import { api, cloneRows, countDifferences, readReviewRoute, writeReviewRoute } from "./reviewUtils";

function ReviewApp() {
  const { t } = useI18n();
  const [catalog, setCatalog] = useState<Catalog>();
  const [viewMode, setViewMode] = useState<ViewMode>("review");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [reviewSourceId, setReviewSourceId] = useState<string>();
  const [lastViewedSourceId, setLastViewedSourceId] = useState<string>();
  const [comparisonSourceId, setComparisonSourceId] = useState<string>();
  const [selection, setSelection] = useState<{ sourceId: string; tableId: string }>();
  const [detail, setDetail] = useState<Detail>();
  const [activeTab, setActiveTab] = useState("ai");
  const [fragment, setFragment] = useState("combined");
  const [rows, setRows] = useState<Rows>([]);
  const [status, setStatus] = useState<Status>("unreviewed");
  const [selectedSource, setSelectedSource] = useState("ai");
  const [note, setNote] = useState("");
  const [marked, setMarked] = useState(false);
  const [showNote, setShowNote] = useState(false);
  const [page, setPage] = useState(1);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const applyReviewRoute = useCallback((value: Catalog, route: ReviewRoute): boolean => {
    const routedSource = route.pdf ? value.sources.find((source) => source.pdf_path === route.pdf) : undefined;
    if (!routedSource) return false;
    const routedTable = route.table ? routedSource.tables.find((table) => table.id === route.table) : undefined;
    setLastViewedSourceId(routedSource.id);
    setReviewSourceId(routedSource.id);
    setComparisonSourceId(routedSource.id);
    setSelection(routedTable ? { sourceId: routedSource.id, tableId: routedTable.id } : undefined);
    setViewMode(route.view);
    return true;
  }, []);

  const reloadCatalog = useCallback(async () => {
    const value = await api<Catalog>("/api/project");
    setCatalog(value);
    if (applyReviewRoute(value, readReviewRoute())) return;
    if (value.source_count === 1) {
      const onlySource = value.sources[0];
      const firstTable = onlySource?.tables[0];
      setReviewSourceId(onlySource?.id);
      setComparisonSourceId(onlySource?.id);
      if (onlySource && firstTable) {
        setSelection({ sourceId: onlySource.id, tableId: firstTable.id });
        writeReviewRoute({ pdf: onlySource.pdf_path, table: firstTable.id, view: "review" }, true);
      }
    } else {
      setComparisonSourceId((current) => current || value.sources[0]?.id);
    }
  }, [applyReviewRoute]);

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
        setMarked(value.draft?.marked || false);
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
        body: JSON.stringify({ status, selected_source: selectedSource, note, marked, rows }),
      });
      setDirty(false);
      setMessage(t("draftSaved"));
      await reloadCatalog();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setSaving(false);
    }
  }, [selection, dirty, status, selectedSource, note, marked, rows, reloadCatalog, t]);

  useEffect(() => {
    if (!dirty) return;
    const timer = window.setTimeout(save, 900);
    return () => window.clearTimeout(timer);
  }, [dirty, save]);

  useEffect(() => {
    if (!catalog) return;
    const onPopState = () => {
      const apply = () => {
        if (!applyReviewRoute(catalog, readReviewRoute())) {
          setReviewSourceId(catalog.source_count === 1 ? catalog.sources[0]?.id : undefined);
          setComparisonSourceId(catalog.sources[0]?.id);
          setSelection(undefined);
          setViewMode("review");
        }
      };
      if (dirty) void save().then(apply);
      else apply();
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [applyReviewRoute, catalog, dirty, save]);

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
  const openReviewSource = async (sourceId: string) => {
    if (dirty) await save();
    const nextSource = catalog?.sources.find((item) => item.id === sourceId);
    const nextSelection = selection?.sourceId === sourceId ? selection : nextSource?.tables[0] ? { sourceId, tableId: nextSource.tables[0].id } : undefined;
    setLastViewedSourceId(sourceId);
    setReviewSourceId(sourceId);
    setComparisonSourceId(sourceId);
    setSelection(nextSelection);
    setViewMode("review");
    writeReviewRoute({ pdf: nextSource?.pdf_path, table: nextSelection?.tableId, view: "review" });
  };
  const closeReviewSource = async () => {
    if (dirty) await save();
    setReviewSourceId(undefined);
    setViewMode("review");
    writeReviewRoute({ view: "review" });
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
    writeReviewRoute({ pdf: source.pdf_path, table: target.id, view: "review" });
  };
  const differenceCount = useMemo(() => countDifferences(detail?.ai_rows || [], detail?.docling_rows || []), [detail]);
  const manualDifferenceCount = useMemo(() => countDifferences(detail?.ai_rows || [], rows), [detail, rows]);
  const draftApplicationState = useMemo<SourceSummary["application_state"]>(() => {
    if (!detail) return "no_apply_needed";
    if (countDifferences(detail.applied_rows, rows)) return "pending_apply";
    return countDifferences(detail.ai_rows, detail.applied_rows) ? "applied" : "no_apply_needed";
  }, [detail, rows]);
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
  const fragmentRows = fragment === "combined" ? detail?.docling_rows || [] : detail?.docling_fragments.find((item) => item.id === fragment)?.rows || [];
  const rawMarkdown = detail?.docling_fragments.find((item) => item.id === fragment)?.markdown;
  const saveState = saving ? { label: t("saving"), className: "saving" } : dirty ? { label: t("unsaved"), className: "dirty" } : { label: message || t("noUnsaved"), className: "saved" };

  if (!catalog) return <main className="loading">{t("loading")}</main>;
  return <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
    <Sidebar
      catalog={catalog}
      selected={selection ? `${selection.sourceId}:${selection.tableId}` : undefined}
      lastViewedSourceId={lastViewedSourceId}
      reviewSourceId={reviewSourceId}
      viewMode={viewMode}
      collapsed={sidebarCollapsed}
      onSelect={(sourceId, tableId) => {
        setSelection({ sourceId, tableId });
        setLastViewedSourceId(sourceId);
        setComparisonSourceId(sourceId);
        setViewMode("review");
        const selectedPdf = catalog.sources.find((item) => item.id === sourceId);
        writeReviewRoute({ pdf: selectedPdf?.pdf_path, table: tableId, view: "review" });
      }}
      onOpenReviewSource={(sourceId) => { void openReviewSource(sourceId); }}
      onCloseReviewSource={() => { void closeReviewSource(); }}
      onViewMode={(mode) => {
        setViewMode(mode);
        const routedSource = catalog.sources.find((item) => item.id === (reviewSourceId || comparisonSourceId));
        const routedTableId = selection?.sourceId === routedSource?.id ? selection?.tableId : undefined;
        writeReviewRoute({ pdf: routedSource?.pdf_path, table: routedTableId, pdfPage: mode === "comparison" ? page : undefined, markdownPage: mode === "comparison" ? page : undefined, view: mode });
      }}
      onToggleCollapsed={() => setSidebarCollapsed((value) => !value)}
      saving={saving}
      onPublish={publish}
      currentManualDiff={selection ? { key: `${selection.sourceId}:${selection.tableId}`, count: manualDifferenceCount } : undefined}
      currentApplicationState={dirty ? draftApplicationState : undefined}
      currentReviewFlags={selection ? { key: `${selection.sourceId}:${selection.tableId}`, hasNote: Boolean(note.trim()), marked } : undefined}
    />
    {viewMode === "comparison" && comparisonSource ? <ComparisonView source={comparisonSource} /> : <main className="workspace">
      {detail && source ? <>
        <PDFPreview url={detail.pdf_url} page={page} region={region} pages={detail.table.source_pages} pageCount={source.page_count} onPage={setPage} available={source.pdf_available} />
        <section className="review-panel">
          <div className="tabs">
            {[["final", t("finalTab")], ["ai", t("aiTab")], ["docling", `Docling (${detail.docling_fragments.length})`], ["diff", t("diffTab", { count: differenceCount })]].map(([key, label]) => <button key={key} className={activeTab === key ? "active" : ""} onClick={() => setActiveTab(key)}>{label}</button>)}
            <div className="review-actions">
              <span className={`save-state ${saveState.className}`}>{saveState.label}</span>
              <button className="table-step" title={t("previousTable")} disabled={tableIndex <= 0 || saving} onClick={() => void moveTable(-1)}>&lt;</button>
              <button className="table-step" title={t("nextTable")} disabled={!source || tableIndex < 0 || tableIndex >= source.tables.length - 1 || saving} onClick={() => void moveTable(1)}>&gt;</button>
              <button className={`note-trigger ${note ? "has-note" : ""}`} onClick={() => setShowNote(!showNote)}>{t(note ? "noted" : "note")}{note ? " •" : ""}</button>
              <button className={marked ? "mark-trigger marked" : "secondary"} onClick={() => { setMarked(!marked); setDirty(true); }}>{t(marked ? "marked" : "mark")}</button>
              <button className={status === "approved" ? "status-active" : "secondary"} onClick={() => { setStatus(status === "approved" ? "unreviewed" : "approved"); setDirty(true); }}>{t("approve")}</button>
              <button className={status === "ignored" ? "status-active ignored" : "secondary"} onClick={() => { setStatus(status === "ignored" ? "unreviewed" : "ignored"); setDirty(true); }}>{t("ignore")}</button>
              {showNote ? <div className="note-popover"><label>{t("reviewNote")}</label><textarea autoFocus value={note} onChange={(event) => { setNote(event.target.value); setDirty(true); }} placeholder={t("notePlaceholder")} /><button onClick={() => setShowNote(false)}>{t("done")}</button></div> : null}
            </div>
          </div>
          <div className="tab-body">
            {activeTab === "final" ? <TableGrid rows={rows} comparisonRows={detail.ai_rows} editable onChange={editRows} columnWidthKey={`llmpdf-review-widths-v2:${detail.source_id}:${detail.table.id}`} physicalPages={physicalPages} pageWeights={pageWeights} activePhysicalPage={page} onPhysicalPage={setPage} /> : null}
            {activeTab === "ai" ? <><div className="source-actions"><span>{t("aiSource")}</span><button onClick={() => useRows(detail.ai_rows, "ai")}>{t("restoreAi")}</button></div><TableGrid rows={detail.ai_rows} physicalPages={physicalPages} pageWeights={pageWeights} activePhysicalPage={page} onPhysicalPage={setPage} /></> : null}
            {activeTab === "docling" ? <><div className="source-actions"><div className="fragment-tabs"><button className={fragment === "combined" ? "active" : ""} onClick={() => setFragment("combined")}>{t("continuousView")}</button>{detail.docling_fragments.map((item) => <button key={item.id} className={fragment === item.id ? "active" : ""} onClick={() => { setFragment(item.id); setPage(item.page); }}>P{item.page}</button>)}</div><button disabled={!fragmentRows.length} onClick={() => useRows(fragmentRows, "docling")}>{t("useAsFinal")}</button></div><TableGrid rows={fragmentRows} physicalPages={fragment === "combined" ? physicalPages : undefined} pageWeights={fragment === "combined" ? pageWeights : undefined} activePhysicalPage={page} onPhysicalPage={setPage} />{rawMarkdown ? <details className="raw-markdown"><summary>{t("rawMarkdown")}</summary><pre>{rawMarkdown}</pre></details> : null}</> : null}
            {activeTab === "diff" ? <DiffView ai={detail.ai_rows} docling={detail.docling_rows} count={differenceCount} /> : null}
          </div>
        </section>
      </> : <div className="empty-workspace">{t("selectTable")}</div>}
      {error ? <div className="toast error-box" onClick={() => setError("")}>{error}</div> : null}
    </main>}
  </div>;
}

export default function App() {
  return <I18nProvider><ReviewApp /></I18nProvider>;
}
