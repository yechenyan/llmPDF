export type Rows = string[][];
export type Status = "unreviewed" | "approved" | "ignored";
export type ViewMode = "review" | "comparison";
export type ReviewFilter = "all" | "pending" | "modified" | "marked" | "noted" | "ignored" | "no_apply" | "applied";

export interface TableSummary {
  id: string;
  page: number;
  source_pages: number[];
  name: string;
  status: Status;
  difference_count: number;
  manual_difference_count: number;
  has_note: boolean;
  marked: boolean;
  lineage_action?: string;
}

export interface SourceSummary {
  id: string;
  name: string;
  result_dir: string;
  pdf_path?: string;
  pdf_relative_path: string;
  pdf_available: boolean;
  page_count: number;
  selected_pages: number[];
  table_count: number;
  reviewed_count: number;
  modified_table_count: number;
  noted_table_count: number;
  marked_table_count: number;
  application_state: "no_apply_needed" | "applied" | "pending_apply";
  last_applied_at?: string;
  draft_updated_at?: string;
  tables: TableSummary[];
}

export interface Catalog {
  source_count: number;
  table_count: number;
  reviewed_count: number;
  sources: SourceSummary[];
}

export interface BBox {
  x0: number;
  top: number;
  x1: number;
  bottom: number;
}

export interface Fragment {
  id: string;
  page: number;
  status: string;
  markdown: string;
  rows: Rows;
}

export interface Draft {
  status: Status;
  selected_source?: string;
  note?: string;
  marked?: boolean;
  rows?: Rows;
}

export interface Detail {
  source_id: string;
  table: TableSummary & {
    bbox: BBox;
    header_rows: number;
    lineage?: { action: string; docling_table_ids: string[] };
  };
  ai_rows: Rows;
  applied_rows: Rows;
  docling_fragments: Fragment[];
  docling_rows: Rows;
  preview_regions: Record<string, BBox | undefined>;
  pdf_url: string;
  draft?: Draft;
}

export interface ComparisonPayload {
  source_id: string;
  name: string;
  page_count: number;
  selected_pages: number[];
  pdf_url: string;
  artifact_base_url: string;
  markdown: string;
}

export interface ReviewRoute {
  pdf?: string;
  table?: string;
  view: ViewMode;
}
