import type { BBox, ReviewRoute, Rows, Status, TableSummary } from "./reviewTypes";

export const statusIcons: Record<Status, string> = {
  unreviewed: "○",
  approved: "✓",
  ignored: "–",
};

export function readReviewRoute(): ReviewRoute {
  const params = new URLSearchParams(window.location.search);
  const positivePage = (name: string): number | undefined => {
    const value = Number(params.get(name));
    return Number.isInteger(value) && value > 0 ? value : undefined;
  };
  return {
    pdf: params.get("pdf") || undefined,
    table: params.get("table") || undefined,
    pdfPage: positivePage("pdf_page"),
    markdownPage: positivePage("markdown_page"),
    view: params.get("view") === "comparison" ? "comparison" : "review",
  };
}

export function writeReviewRoute(route: ReviewRoute, replace = false): void {
  const url = new URL(window.location.href);
  for (const key of ["pdf", "table", "pdf_page", "markdown_page", "view"]) url.searchParams.delete(key);
  if (route.pdf) url.searchParams.set("pdf", route.pdf);
  if (route.table) url.searchParams.set("table", route.table);
  if (route.pdfPage) url.searchParams.set("pdf_page", String(route.pdfPage));
  if (route.markdownPage) url.searchParams.set("markdown_page", String(route.markdownPage));
  if (route.view === "comparison") url.searchParams.set("view", route.view);
  window.history[replace ? "replaceState" : "pushState"]({}, "", url);
}

export async function api<T>(url: string, options?: RequestInit): Promise<T> {
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

export function cloneRows(rows: Rows): Rows {
  return rows.map((row) => [...row]);
}

export function columnLabel(index: number): string {
  let value = index + 1;
  let label = "";
  while (value > 0) {
    label = String.fromCharCode(65 + ((value - 1) % 26)) + label;
    value = Math.floor((value - 1) / 26);
  }
  return label;
}

export function pageLabel(table: TableSummary): string {
  const pages = table.source_pages;
  return pages.length > 1 ? `P${pages[0]}–${pages[pages.length - 1]}` : `P${table.page}`;
}

export function countDifferences(ai: Rows, docling: Rows): number {
  const rowCount = Math.max(ai.length, docling.length);
  const width = Math.max(...ai.map((row) => row.length), ...docling.map((row) => row.length), 0);
  let count = 0;
  for (let row = 0; row < rowCount; row += 1) {
    for (let column = 0; column < width; column += 1) {
      count += Number((ai[row]?.[column] || "") !== (docling[row]?.[column] || ""));
    }
  }
  return count;
}

export function repeatedCellToneMap(rows: Rows): Map<string, string> {
  const width = Math.max(...rows.map((row) => row.length), 0);
  const normalized = rows.map((row) =>
    Array.from({ length: width }, (_, column) => (row[column] || "").trim()),
  );
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
        for (const [nextRow, nextColumn] of [
          [currentRow - 1, currentColumn],
          [currentRow + 1, currentColumn],
          [currentRow, currentColumn - 1],
          [currentRow, currentColumn + 1],
        ]) {
          const nextKey = `${nextRow}:${nextColumn}`;
          if (
            nextRow < 0 ||
            nextRow >= normalized.length ||
            nextColumn < 0 ||
            nextColumn >= width ||
            visited.has(nextKey) ||
            normalized[nextRow][nextColumn] !== value
          ) continue;
          visited.add(nextKey);
          queue.push([nextRow, nextColumn]);
        }
      }
      if (group.length < 2) continue;
      const tone = `repeated-cell repeated-tone-${groupIndex % 4}`;
      group.forEach(([groupRow, groupColumn]) =>
        tones.set(`${groupRow}:${groupColumn}`, tone),
      );
      groupIndex += 1;
    }
  }
  return tones;
}

export function rotatedBBox(region: BBox, rotation: number, pageWidth: number, pageHeight: number): BBox {
  if (rotation === 90) return { x0: pageHeight - region.bottom, top: region.x0, x1: pageHeight - region.top, bottom: region.x1 };
  if (rotation === 180) return { x0: pageWidth - region.x1, top: pageHeight - region.bottom, x1: pageWidth - region.x0, bottom: pageHeight - region.top };
  if (rotation === 270) return { x0: region.top, top: pageWidth - region.x1, x1: region.bottom, bottom: pageWidth - region.x0 };
  return region;
}
