import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { useI18n } from "../i18n";
import type { Rows } from "../reviewTypes";
import { cloneRows, columnLabel, countDifferences } from "../reviewUtils";

const AUTO_COLUMN_MIN_WIDTH = 72;
const AUTO_COLUMN_MAX_WIDTH = 300;
const AUTO_COLUMN_MAX_LINE_UNITS = 26;

function textDisplayUnits(value: string): number {
  return Array.from(value).reduce((units, character) => units + (/[^\u0000-\u00ff]/.test(character) ? 2 : 1), 0);
}

function preferredColumnWidths(rows: Rows, width: number): number[] {
  return Array.from({ length: width }, (_, column) => {
    const longestLine = rows.slice(0, 200).reduce((longest, row) => Math.max(longest, ...(row[column] || "").split("\n").map(textDisplayUnits)), 0);
    const fittedLine = Math.min(longestLine, AUTO_COLUMN_MAX_LINE_UNITS);
    return Math.min(AUTO_COLUMN_MAX_WIDTH, Math.max(AUTO_COLUMN_MIN_WIDTH, 22 + fittedLine * 5.7));
  });
}

function AutoSizeTextarea({ value, onValueChange }: { value: string; onValueChange: (value: string) => void }) {
  const element = useRef<HTMLTextAreaElement>(null);
  const fitHeight = useCallback(() => {
    if (!element.current) return;
    element.current.style.height = "0px";
    element.current.style.height = `${element.current.scrollHeight}px`;
  }, []);
  useLayoutEffect(fitHeight, [fitHeight, value]);
  useEffect(() => {
    if (!element.current || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(fitHeight);
    observer.observe(element.current.parentElement || element.current);
    return () => observer.disconnect();
  }, [fitHeight]);
  return <textarea ref={element} rows={1} value={value} onChange={(event) => onValueChange(event.target.value)} />;
}

interface TableGridProps {
  rows: Rows;
  comparisonRows?: Rows;
  editable?: boolean;
  onChange?: (rows: Rows) => void;
  columnWidthKey?: string;
  physicalPages?: number[];
  pageWeights?: number[];
  activePhysicalPage?: number;
  onPhysicalPage?: (page: number) => void;
}

export function TableGrid({ rows, comparisonRows, editable = false, onChange, columnWidthKey, physicalPages, pageWeights, activePhysicalPage, onPhysicalPage }: TableGridProps) {
  const { t } = useI18n();
  const pageSize = 80;
  const [localPage, setLocalPage] = useState(0);
  const [gridSelection, setGridSelection] = useState<{ kind: "row" | "column"; index: number }>();
  const width = Math.max(...rows.map((row) => row.length), 0);
  const manualDifferenceCount = comparisonRows ? countDifferences(comparisonRows, rows) : 0;
  const automaticColumnWidths = useMemo(() => preferredColumnWidths(rows, width), [rows, width]);
  const repeatedCellTones = useMemo(() => {
    const normalized = rows.map((row) => Array.from({ length: width }, (_, column) => (row[column] || "").trim()));
    const visited = new Set<string>();
    const tones = new Map<string, string>();
    let groupIndex = 0;
    for (let row = 0; row < normalized.length; row += 1) {
      for (let column = 0; column < width; column += 1) {
        const key = `${row}:${column}`;
        const value = normalized[row][column];
        if (!value || visited.has(key)) continue;
        const group: Array<[number, number]> = [];
        const queue: Array<[number, number]> = [[row, column]];
        visited.add(key);
        while (queue.length) {
          const [currentRow, currentColumn] = queue.shift()!;
          group.push([currentRow, currentColumn]);
          for (const [nextRow, nextColumn] of [[currentRow - 1, currentColumn], [currentRow + 1, currentColumn], [currentRow, currentColumn - 1], [currentRow, currentColumn + 1]]) {
            const nextKey = `${nextRow}:${nextColumn}`;
            if (nextRow < 0 || nextRow >= normalized.length || nextColumn < 0 || nextColumn >= width || visited.has(nextKey) || normalized[nextRow][nextColumn] !== value) continue;
            visited.add(nextKey);
            queue.push([nextRow, nextColumn]);
          }
        }
        if (group.length < 2) continue;
        const tone = `repeated-cell repeated-tone-${groupIndex % 4}`;
        group.forEach(([groupRow, groupColumn]) => tones.set(`${groupRow}:${groupColumn}`, tone));
        groupIndex += 1;
      }
    }
    return tones;
  }, [rows, width]);
  const [columnWidthOverrides, setColumnWidthOverrides] = useState<Array<number | null>>([]);
  const columnWidths = Array.from({ length: width }, (_, index) => columnWidthOverrides[index] ?? automaticColumnWidths[index] ?? AUTO_COLUMN_MIN_WIDTH);
  const linkedPages = physicalPages?.length ? physicalPages : undefined;
  const pageCount = linkedPages?.length || Math.max(1, Math.ceil(rows.length / pageSize));
  const linkedPageIndex = linkedPages ? Math.max(0, linkedPages.indexOf(activePhysicalPage ?? linkedPages[0])) : -1;
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
    let stored: Array<number | null> = [];
    if (columnWidthKey) {
      try {
        const parsed = JSON.parse(window.localStorage.getItem(columnWidthKey) || "[]");
        if (Array.isArray(parsed)) stored = parsed.map((value) => typeof value === "number" ? value : null);
      } catch { stored = []; }
    }
    setColumnWidthOverrides(Array.from({ length: width }, (_, index) => stored[index] ?? null));
  }, [columnWidthKey, width]);
  useEffect(() => {
    if (!columnWidthKey || columnWidthOverrides.length !== width) return;
    window.localStorage.setItem(columnWidthKey, JSON.stringify(columnWidthOverrides));
  }, [columnWidthKey, columnWidthOverrides, width]);
  const pageStart = rowBounds?.[page] ?? page * pageSize;
  const pageEnd = rowBounds?.[page + 1] ?? (page + 1) * pageSize;
  const visible = rows.slice(pageStart, pageEnd);
  const changePage = (nextPage: number) => linkedPages ? onPhysicalPage?.(linkedPages[nextPage]) : setLocalPage(nextPage);
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
  const removeRow = (rowIndex: number) => rows.length > 1 && onChange?.(rows.filter((_, index) => index !== rowIndex));
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
      setColumnWidthOverrides((current) => current.map((value, index) => index === column ? nextWidth : value));
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
  return <div className="grid-wrap">
    <div className="grid-meta"><span>{t("dimensions", { rows: rows.length, columns: width })}{comparisonRows && manualDifferenceCount ? ` · ${t("manualChanges", { count: manualDifferenceCount })}` : ""}</span><div className="grid-controls">
      {editable && gridSelection?.kind === "row" ? <div className="selection-tools"><b>{t("rowNumber", { number: gridSelection.index + 1 })}</b><button onClick={() => insertRow(gridSelection.index)}>{t("insertAbove")}</button><button onClick={() => insertRow(gridSelection.index + 1)}>{t("insertBelow")}</button><button className="danger" disabled={rows.length <= 1} onClick={() => removeRow(gridSelection.index)}>{t("deleteRow")}</button></div> : null}
      {editable && gridSelection?.kind === "column" ? <div className="selection-tools"><b>{t("columnNumber", { label: columnLabel(gridSelection.index) })}</b><button onClick={() => insertColumn(gridSelection.index)}>{t("insertLeft")}</button><button onClick={() => insertColumn(gridSelection.index + 1)}>{t("insertRight")}</button><button className="danger" disabled={width <= 1} onClick={() => removeColumn(gridSelection.index)}>{t("deleteColumn")}</button></div> : null}
      {pageCount > 1 ? <><button disabled={page === 0} onClick={() => changePage(page - 1)}>{t("previousPage")}</button><span>P{linkedPages?.[page] ?? page + 1} · {page + 1}/{pageCount}</span><button disabled={page + 1 === pageCount} onClick={() => changePage(page + 1)}>{t("nextPage")}</button></> : null}
    </div></div>
    <div className="table-scroll"><table className="data-grid">
      {editable ? <thead className="sheet-columns"><tr><th className="sheet-corner" />{Array.from({ length: width }, (_, column) => <th key={column} style={{ width: columnWidths[column], minWidth: columnWidths[column], maxWidth: columnWidths[column] }} className={gridSelection?.kind === "column" && gridSelection.index === column ? "selected" : ""}><button title={t("selectColumn", { label: columnLabel(column) })} onClick={() => setGridSelection({ kind: "column", index: column })}>{columnLabel(column)}</button><span className="column-resizer" title={t("resizeColumn")} onMouseDown={(event) => beginColumnResize(column, event)} /></th>)}</tr></thead> : null}
      <tbody>{visible.map((row, visibleIndex) => {
        const rowIndex = pageStart + visibleIndex;
        return <tr key={rowIndex} className={rowIndex === 0 ? "header-row" : ""}><th className="row-number">{editable ? <button className={gridSelection?.kind === "row" && gridSelection.index === rowIndex ? "selected" : ""} title={t("selectRow", { number: rowIndex + 1 })} onClick={() => setGridSelection({ kind: "row", index: rowIndex })}>{rowIndex + 1}</button> : rowIndex + 1}</th>{Array.from({ length: width }, (_, column) => {
          const manuallyChanged = comparisonRows && (row[column] || "") !== (comparisonRows[rowIndex]?.[column] || "");
          const className = [repeatedCellTones.get(`${rowIndex}:${column}`) || "", manuallyChanged ? "manually-changed" : ""].filter(Boolean).join(" ");
          return <td key={column} className={className} style={editable ? { width: columnWidths[column], minWidth: columnWidths[column], maxWidth: columnWidths[column] } : undefined}>{editable ? <AutoSizeTextarea value={row[column] || ""} onValueChange={(value) => update(rowIndex, column, value)} /> : <span>{row[column] || " "}</span>}</td>;
        })}</tr>;
      })}</tbody>
    </table></div>
  </div>;
}

export function DiffView({ ai, docling, count }: { ai: Rows; docling: Rows; count: number }) {
  const { t } = useI18n();
  const rowCount = Math.max(ai.length, docling.length);
  const width = Math.max(...ai.map((row) => row.length), ...docling.map((row) => row.length), 0);
  const differences = Array.from({ length: rowCount }, (_, row) => Array.from({ length: width }, (_, column) => (ai[row]?.[column] || "") !== (docling[row]?.[column] || "")));
  return <div className="diff-view"><div className="diff-summary">{count ? t("diffCells", { count }) : t("diffEqual")}</div><div className="table-scroll"><table className="data-grid diff-grid"><tbody>{differences.slice(0, 200).map((row, rowIndex) => <tr key={rowIndex}><th className="row-number">{rowIndex + 1}</th>{row.map((different, column) => <td key={column} className={different ? "different" : ""}><small>AI</small><span>{ai[rowIndex]?.[column] || " "}</span><small>Docling</small><span>{docling[rowIndex]?.[column] || " "}</span></td>)}</tr>)}</tbody></table></div></div>;
}
