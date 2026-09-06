import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useI18n } from "../i18n";
import { getDocument, type PDFDocumentProxy } from "../pdfRuntime";
import type { ComparisonPayload, SourceSummary } from "../reviewTypes";
import { api, readReviewRoute, writeReviewRoute } from "../reviewUtils";

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

function ComparisonPDFPage({ document, page, scrollRoot, zoom }: { document: PDFDocumentProxy; page: number; scrollRoot: HTMLDivElement | null; zoom: number }) {
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
    const observer = new IntersectionObserver(([entry]) => entry.isIntersecting && setVisible(true), { root: scrollRoot, rootMargin: "900px 0px" });
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
      renderTask = pdfPage.render({ canvas: canvas.current, canvasContext: canvas.current.getContext("2d")!, viewport, transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0] });
      return renderTask.promise;
    }).catch((reason) => {
      if (!cancelled && !String(reason).includes("RenderingCancelledException")) setError(String(reason));
    });
    return () => { cancelled = true; renderTask?.cancel(); };
  }, [document, page, visible, zoom]);
  return <article className="comparison-pdf-page" data-page={page} ref={host} style={{ aspectRatio: ratio, width: `${zoom * 100}%` }}><span className="comparison-page-label">P{page}</span>{error ? <span className="comparison-page-error">{error}</span> : null}<canvas ref={canvas} /></article>;
}

function ContinuousPDF({ url, available, pageCount, pages, scrollRoot, zoom }: { url: string; available: boolean; pageCount: number; pages: number[]; scrollRoot: HTMLDivElement | null; zoom: number }) {
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
    return () => { active = false; task.destroy(); };
  }, [available, url]);
  if (!available) return <div className="pdf-empty">{t("pdfUnavailable")}</div>;
  if (error) return <div className="error-box comparison-load-error">{error}</div>;
  if (!document) return <div className="comparison-loading">{t("loadingPdf")}</div>;
  return <div className="comparison-pdf-pages">{(pages.length ? pages : Array.from({ length: document.numPages || pageCount }, (_, index) => index + 1)).map((page) => <ComparisonPDFPage key={page} document={document} page={page} scrollRoot={scrollRoot} zoom={zoom} />)}</div>;
}

export function ComparisonView({ source }: { source: SourceSummary }) {
  const { t } = useI18n();
  const [comparison, setComparison] = useState<ComparisonPayload>();
  const [error, setError] = useState("");
  const [syncEnabled, setSyncEnabled] = useState(true);
  const [pdfZoom, setPdfZoom] = useState(1);
  const left = useRef<HTMLDivElement>(null);
  const right = useRef<HTMLDivElement>(null);
  const synchronizing = useRef<HTMLDivElement | null>(null);
  const restoring = useRef(false);
  const positions = useRef({ pdfPage: readReviewRoute().pdfPage, markdownPage: readReviewRoute().markdownPage });
  useEffect(() => {
    setComparison(undefined);
    setError("");
    api<ComparisonPayload>(`/api/sources/${source.id}/comparison`).then(setComparison).catch((reason) => setError(String(reason)));
  }, [source.id]);
  const pages = useMemo(() => markdownPages(comparison?.markdown || ""), [comparison]);
  const visiblePage = useCallback((container: HTMLDivElement | null): number | undefined => {
    if (!container) return undefined;
    const pageElements = Array.from(container.querySelectorAll<HTMLElement>("[data-page]"));
    if (!pageElements.length) return undefined;
    const anchor = container.scrollTop + 24;
    const current = [...pageElements].reverse().find((item) => item.offsetTop <= anchor) || pageElements[0];
    const value = Number(current.dataset.page);
    return Number.isInteger(value) && value > 0 ? value : undefined;
  }, []);
  const recordPosition = useCallback((side: "pdf" | "markdown", container: HTMLDivElement | null) => {
    if (restoring.current) return;
    const currentPage = visiblePage(container);
    if (!currentPage) return;
    const key = side === "pdf" ? "pdfPage" : "markdownPage";
    if (positions.current[key] === currentPage) return;
    positions.current[key] = currentPage;
    const route = readReviewRoute();
    writeReviewRoute({
      pdf: source.pdf_path || route.pdf,
      table: route.table,
      pdfPage: positions.current.pdfPage,
      markdownPage: positions.current.markdownPage,
      view: "comparison",
    }, true);
  }, [source.pdf_path, visiblePage]);
  const synchronize = useCallback((origin: HTMLDivElement | null, target: HTMLDivElement | null) => {
    if (restoring.current || !syncEnabled || !origin || !target || synchronizing.current === origin) return;
    const originPages = Array.from(origin.querySelectorAll<HTMLElement>("[data-page]"));
    const targetPages = Array.from(target.querySelectorAll<HTMLElement>("[data-page]"));
    if (!originPages.length || !targetPages.length) return;
    const anchor = origin.scrollTop + 24;
    const originPage = [...originPages].reverse().find((item) => item.offsetTop <= anchor) || originPages[0];
    const targetPage = targetPages.find((item) => item.dataset.page === originPage.dataset.page) || targetPages[0];
    const progress = Math.max(0, Math.min(1, (anchor - originPage.offsetTop) / Math.max(1, originPage.offsetHeight)));
    synchronizing.current = target;
    target.scrollTop = Math.max(0, targetPage.offsetTop + progress * targetPage.offsetHeight - 24);
    window.requestAnimationFrame(() => { if (synchronizing.current === target) synchronizing.current = null; });
  }, [syncEnabled]);
  useEffect(() => {
    if (!comparison) return;
    const route = readReviewRoute();
    positions.current = { pdfPage: route.pdfPage, markdownPage: route.markdownPage };
    restoring.current = true;
    let attempts = 0;
    let frame = 0;
    const restore = () => {
      attempts += 1;
      const moveToPage = (container: HTMLDivElement | null, targetPage?: number) => {
        if (!container || !targetPage) return true;
        const target = container.querySelector<HTMLElement>(`[data-page="${targetPage}"]`);
        if (!target) return false;
        container.scrollTop = Math.max(0, target.offsetTop - 18);
        return true;
      };
      const pdfReady = moveToPage(left.current, positions.current.pdfPage);
      const markdownReady = moveToPage(right.current, positions.current.markdownPage);
      if ((!pdfReady || !markdownReady) && attempts < 180) {
        frame = window.requestAnimationFrame(restore);
        return;
      }
      frame = window.requestAnimationFrame(() => { restoring.current = false; });
    };
    frame = window.requestAnimationFrame(restore);
    return () => { window.cancelAnimationFrame(frame); restoring.current = false; };
  }, [comparison, source.id]);
  if (error) return <div className="empty-workspace error-box">{error}</div>;
  if (!comparison) return <div className="empty-workspace">{t("loadingComparison")}</div>;
  return <main className="comparison-workspace">
    <div className="comparison-columns">
      <section className="comparison-pane"><div className="comparison-pane-title"><strong>PDF</strong><div className="comparison-zoom"><span>{t("pageCount", { count: comparison.selected_pages.length || comparison.page_count })}</span><button aria-label={t("zoomOut")} title={t("zoomOut")} disabled={pdfZoom <= 0.6} onClick={() => setPdfZoom((value) => Math.max(0.6, value - 0.2))}>−</button><b>{Math.round(pdfZoom * 100)}%</b><button aria-label={t("zoomIn")} title={t("zoomIn")} disabled={pdfZoom >= 2} onClick={() => setPdfZoom((value) => Math.min(2, value + 0.2))}>＋</button></div></div><div className="comparison-scroll pdf-comparison-scroll" ref={left} onScroll={() => { recordPosition("pdf", left.current); synchronize(left.current, right.current); }}><ContinuousPDF url={comparison.pdf_url} available={source.pdf_available} pageCount={comparison.page_count} pages={comparison.selected_pages} scrollRoot={left.current} zoom={pdfZoom} /></div></section>
      <section className="comparison-pane"><div className="comparison-pane-title"><strong>Markdown</strong><div className="comparison-pane-actions"><span>output.md</span><label className="sync-toggle compact"><input type="checkbox" checked={syncEnabled} onChange={(event) => setSyncEnabled(event.target.checked)} />{t("syncScroll")}</label></div></div><div className="comparison-scroll markdown-comparison-scroll" ref={right} onScroll={() => { recordPosition("markdown", right.current); synchronize(right.current, left.current); }}>{pages.map(({ page, content }) => <article className="markdown-page" data-page={page} key={page}><span className="comparison-page-label">P{page}</span>{content ? <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ img: ({ src, alt }) => <img src={publicArtifactUrl(comparison.artifact_base_url, src)} alt={alt || ""} loading="lazy" />, a: ({ href, children }) => <a href={publicArtifactUrl(comparison.artifact_base_url, href) || href} target="_blank" rel="noreferrer">{children}</a> }}>{content}</ReactMarkdown> : <p className="empty-markdown-page">{t("emptyMarkdownPage")}</p>}</article>)}</div></section>
    </div>
  </main>;
}
