import { useEffect, useRef, useState } from "react";
import { useI18n, type Language } from "../i18n";
import type { Catalog, ReviewFilter, SourceSummary, TableSummary, ViewMode } from "../reviewTypes";
import { pageLabel, statusIcons } from "../reviewUtils";

interface SidebarProps {
  catalog: Catalog;
  selected?: string;
  lastViewedSourceId?: string;
  reviewSourceId?: string;
  viewMode: ViewMode;
  collapsed: boolean;
  onSelect: (sourceId: string, tableId: string) => void;
  onOpenReviewSource: (sourceId: string) => void;
  onCloseReviewSource: () => void;
  onViewMode: (mode: ViewMode) => void;
  onToggleCollapsed: () => void;
  saving: boolean;
  onPublish: () => void;
  currentManualDiff?: { key: string; count: number };
  currentApplicationState?: SourceSummary["application_state"];
  currentReviewFlags?: { key: string; hasNote: boolean; marked: boolean };
}

export function Sidebar({
  catalog, selected, lastViewedSourceId, reviewSourceId, viewMode, collapsed, onSelect,
  onOpenReviewSource, onCloseReviewSource, onViewMode, onToggleCollapsed, saving,
  onPublish, currentManualDiff, currentApplicationState, currentReviewFlags,
}: SidebarProps) {
  const { language, setLanguage, t } = useI18n();
  const [sourceFilter, setSourceFilter] = useState("");
  const [tableFilter, setTableFilter] = useState("");
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>("all");
  const lastViewedSource = useRef<HTMLButtonElement>(null);
  const sourceList = useRef<HTMLDivElement>(null);
  const isBatch = catalog.source_count > 1;
  const activeReviewSourceId = isBatch ? reviewSourceId : catalog.sources[0]?.id;
  const selectedReviewSourceId = selected?.split(":", 1)[0];
  const selectedSource = catalog.sources.find((source) => source.id === activeReviewSourceId)
    || catalog.sources.find((source) => source.id === selectedReviewSourceId);
  const tableMatchesReviewFilter = (table: TableSummary, source: SourceSummary) => {
    const key = `${source.id}:${table.id}`;
    const hasNote = currentReviewFlags?.key === key ? currentReviewFlags.hasNote : table.has_note;
    const marked = currentReviewFlags?.key === key ? currentReviewFlags.marked : table.marked;
    const modified = currentManualDiff?.key === key ? currentManualDiff.count > 0 : table.manual_difference_count > 0;
    const applicationState = source.id === selectedSource?.id && currentApplicationState ? currentApplicationState : source.application_state;
    if (reviewFilter === "pending") return table.status === "unreviewed";
    if (reviewFilter === "modified") return modified;
    if (reviewFilter === "marked") return marked;
    if (reviewFilter === "noted") return hasNote;
    if (reviewFilter === "ignored") return table.status === "ignored";
    if (reviewFilter === "no_apply") return applicationState === "no_apply_needed";
    if (reviewFilter === "applied") return applicationState === "applied";
    return true;
  };
  const visibleSources = catalog.sources.filter((source) => {
    const query = sourceFilter.trim().toLowerCase();
    const matches = `${source.pdf_relative_path} ${source.name}`.toLowerCase().includes(query);
    return matches && source.tables.some((table) => tableMatchesReviewFilter(table, source));
  });
  const visibleTables = selectedSource?.tables.filter((table) => {
    const matches = `${table.name} ${table.id} ${pageLabel(table)}`.toLowerCase().includes(tableFilter.trim().toLowerCase());
    return matches && tableMatchesReviewFilter(table, selectedSource);
  }) || [];
  const applicationState = currentApplicationState || selectedSource?.application_state || "no_apply_needed";
  const applicationLabel = applicationState === "applied"
    ? t("applicationApplied")
    : applicationState === "pending_apply" ? t("applicationPending") : t("applicationNoApply");
  useEffect(() => {
    if (!isBatch || activeReviewSourceId || !lastViewedSourceId) return;
    lastViewedSource.current?.scrollIntoView({ block: "center" });
  }, [activeReviewSourceId, isBatch, lastViewedSourceId]);
  useEffect(() => {
    if (!activeReviewSourceId) return;
    sourceList.current?.scrollTo({ top: 0 });
  }, [activeReviewSourceId]);
  return (
    <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
      <button className={`sidebar-toggle ${collapsed ? "expand" : "collapse"}`} title={t(collapsed ? "showSidebar" : "hideSidebar")} aria-label={t(collapsed ? "showSidebar" : "hideSidebar")} onClick={onToggleCollapsed}><span aria-hidden="true" /></button>
      <div className="brand">
        <div className="brand-mark">TR</div>
        <div><strong>{t("brand")}</strong><span>{t("progress", { reviewed: catalog.reviewed_count, total: catalog.table_count })}</span></div>
        <select className="language-select" aria-label={t("language")} value={language} onChange={(event) => setLanguage(event.target.value as Language)}><option value="de">DE</option><option value="en">EN</option></select>
      </div>
      <div className="overall-progress"><i style={{ width: `${catalog.table_count ? (catalog.reviewed_count / catalog.table_count) * 100 : 0}%` }} /></div>
      {activeReviewSourceId && selectedSource ? <div className="selected-source-context">
        {isBatch ? <button className="back-to-sources" onClick={onCloseReviewSource}>← {t("allPdfs")}</button> : null}
        <div><span className="selected-source-path" title={selectedSource.pdf_relative_path}>{selectedSource.pdf_relative_path}</span><small>{viewMode === "comparison" ? t("pageCount", { count: selectedSource.page_count }) : `${selectedSource.reviewed_count}/${selectedSource.table_count}`}</small></div>
      </div> : null}
      {activeReviewSourceId ? <div className="view-switch"><button className={viewMode === "review" ? "active" : ""} onClick={() => onViewMode("review")}>{t("tableReview")}</button><button className={viewMode === "comparison" ? "active" : ""} onClick={() => onViewMode("comparison")}>{t("documentComparison")}</button></div> : null}
      {viewMode === "review" ? <div className="filters">
        <input value={isBatch && !activeReviewSourceId ? sourceFilter : tableFilter} onChange={(event) => isBatch && !activeReviewSourceId ? setSourceFilter(event.target.value) : setTableFilter(event.target.value)} placeholder={t(isBatch && !activeReviewSourceId ? "searchPdf" : isBatch ? "searchCurrentPdf" : "search")} />
        <div className="review-filter-chips" aria-label={t("reviewFilters")}>{([
          ["all", t("filterAll")], ["pending", t("filterPending")], ["modified", t("filterModified")],
          ["marked", t("filterMarked")], ["noted", t("filterNoted")], ["ignored", t("filterIgnored")],
          ["no_apply", t("filterNoApply")], ["applied", t("filterApplied")],
        ] as [ReviewFilter, string][]).map(([value, label]) => <button key={value} className={reviewFilter === value ? "active" : ""} onClick={() => setReviewFilter(value)}>{label}</button>)}</div>
      </div> : null}
      {viewMode === "review" ? <div className="source-list" ref={sourceList}>
        {isBatch && !activeReviewSourceId ? <div className="pdf-source-list">
          {visibleSources.map((source) => <button className={`pdf-source ${source.id === lastViewedSourceId ? "last-viewed" : ""} ${source.modified_table_count ? "has-manual-changes" : ""}`} key={source.id} ref={source.id === lastViewedSourceId ? lastViewedSource : undefined} aria-current={source.id === lastViewedSourceId ? "true" : undefined} onClick={() => onOpenReviewSource(source.id)}>
            <span className="pdf-source-copy"><strong title={source.pdf_relative_path}>{source.name}</strong><span className="pdf-source-meta">
              <small>{t("pdfSummary", { tables: source.table_count, pages: source.page_count })}</small>
              <b className={`pdf-application-state ${source.application_state}`}>{source.application_state === "applied" ? t("pdfApplied") : source.application_state === "pending_apply" ? t("pdfPendingApply") : t("pdfNoApply")}</b>
              {source.modified_table_count ? <b className="pdf-modified-count">{t("modifiedTables", { count: source.modified_table_count })}</b> : null}
              {source.noted_table_count ? <b className="pdf-note-count">{t("notedTables", { count: source.noted_table_count })}</b> : null}
              {source.marked_table_count ? <b className="pdf-mark-count">{t("markedTables", { count: source.marked_table_count })}</b> : null}
            </span></span>
            <span className="pdf-source-progress"><b>{source.reviewed_count}/{source.table_count}</b><i><span style={{ width: `${source.table_count ? (source.reviewed_count / source.table_count) * 100 : 0}%` }} /></i></span>
          </button>)}
          {!visibleSources.length ? <p className="sidebar-empty">{t("noPdfs")}</p> : null}
        </div> : selectedSource ? <section className={`source-group ${isBatch ? "batch-table-list" : ""}`}>
          {visibleTables.map((table) => {
            const key = `${selectedSource.id}:${table.id}`;
            const manualDifferenceCount = currentManualDiff?.key === key ? currentManualDiff.count : table.manual_difference_count;
            const hasNote = currentReviewFlags?.key === key ? currentReviewFlags.hasNote : table.has_note;
            const marked = currentReviewFlags?.key === key ? currentReviewFlags.marked : table.marked;
            return <button className={`table-nav ${selected === key ? "selected" : ""} status-${table.status} ${manualDifferenceCount ? "manually-modified" : ""}`} key={key} onClick={() => onSelect(selectedSource.id, table.id)}>
              <span className="status-icon">{statusIcons[table.status]}</span>
              <span className="nav-copy"><b><span>{pageLabel(table)} · {table.id}</span><span className="nav-badges"><small className={table.difference_count === 0 ? "diff-zero" : "diff-found"}>{t("difference", { count: table.difference_count })}</small>{manualDifferenceCount ? <small className="manual-diff" title={t("manualDifferenceTitle")}>{t("manualDifference", { count: manualDifferenceCount })}</small> : null}{hasNote ? <small className="note-flag">{t("noteFlag")}</small> : null}{marked ? <small className="mark-flag">{t("markFlag")}</small> : null}</span></b><em>{table.name}</em></span>
            </button>;
          })}
          {!visibleTables.length ? <p className="sidebar-empty">{t("noTables")}</p> : null}
        </section> : null}
      </div> : selectedSource ? <div className="source-list"><section className={`source-group comparison-current-source ${isBatch ? "batch-table-list" : ""}`}><p>{t("comparisonHint")}</p></section></div> : null}
      {viewMode === "review" && selected && activeReviewSourceId ? <div className="sidebar-review">
        <div className={`application-state ${applicationState}`}><span>{applicationLabel}</span>{selectedSource?.last_applied_at ? <time title={selectedSource.last_applied_at}>{t("recorded")}</time> : null}</div>
        <div className="sidebar-actions"><button className="primary" disabled={saving || applicationState !== "pending_apply"} onClick={onPublish}>{t("applyPdf")}</button></div>
      </div> : null}
    </aside>
  );
}
