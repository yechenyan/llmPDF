import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import { getDocument, type PDFDocumentProxy } from "../pdfRuntime";
import type { BBox } from "../reviewTypes";
import { rotatedBBox } from "../reviewUtils";

interface PDFPreviewProps {
  url: string;
  page: number;
  region?: BBox;
  pages: number[];
  pageCount: number;
  onPage: (page: number) => void;
  available: boolean;
}

export function PDFPreview({ url, page, region, pages, pageCount, onPage, available }: PDFPreviewProps) {
  const { t } = useI18n();
  const canvas = useRef<HTMLCanvasElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const [document, setDocument] = useState<PDFDocumentProxy>();
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
  const [pageSize, setPageSize] = useState({ width: 0, height: 0 });
  const [error, setError] = useState("");

  useEffect(() => {
    if (!available) return;
    setRotation(0);
    let active = true;
    const task = getDocument({ url });
    task.promise.then((value) => active && setDocument(value)).catch((reason) => setError(String(reason)));
    return () => { active = false; task.destroy(); };
  }, [url, available]);

  useEffect(() => {
    if (!document || !canvas) return;
    let cancelled = false;
    let renderTask: ReturnType<Awaited<ReturnType<typeof document.getPage>>["render"]> | undefined;
    document.getPage(page).then((pdfPage) => {
      if (cancelled || !canvas.current) return;
      const scale = 1.35 * zoom;
      const baseViewport = pdfPage.getViewport({ scale: 1 });
      const viewport = pdfPage.getViewport({ scale, rotation: (pdfPage.rotate + rotation) % 360 });
      const outputScale = window.devicePixelRatio || 1;
      const target = canvas.current;
      target.width = Math.floor(viewport.width * outputScale);
      target.height = Math.floor(viewport.height * outputScale);
      target.style.width = `${viewport.width}px`;
      target.style.height = `${viewport.height}px`;
      setViewportSize({ width: viewport.width, height: viewport.height });
      setPageSize({ width: baseViewport.width, height: baseViewport.height });
      renderTask = pdfPage.render({
        canvas: target,
        canvasContext: target.getContext("2d")!,
        viewport,
        transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
      });
      return renderTask.promise;
    }).catch((reason) => {
      if (!cancelled && !String(reason).includes("RenderingCancelledException")) setError(String(reason));
    });
    return () => { cancelled = true; renderTask?.cancel(); };
  }, [document, page, zoom, rotation]);

  const scale = 1.35 * zoom;
  const focused = Boolean(region);
  const displayRegion = region && pageSize.width && pageSize.height ? rotatedBBox(region, rotation, pageSize.width, pageSize.height) : region;
  const stageStyle = { width: viewportSize.width, height: viewportSize.height };
  const overlayStyle = displayRegion
    ? { left: displayRegion.x0 * scale, top: displayRegion.top * scale, width: (displayRegion.x1 - displayRegion.x0) * scale, height: (displayRegion.bottom - displayRegion.top) * scale }
    : undefined;

  useEffect(() => {
    if (!focused || !displayRegion || !scroll.current || !stage.current || !viewportSize.width) return;
    const frame = requestAnimationFrame(() => {
      const host = scroll.current;
      const pageStage = stage.current;
      if (!host || !pageStage) return;
      const tableLeft = pageStage.offsetLeft + displayRegion.x0 * scale;
      const tableTop = pageStage.offsetTop + displayRegion.top * scale;
      host.scrollTo({
        left: Math.max(0, tableLeft - 16),
        top: Math.max(0, tableTop - 72),
        behavior: "smooth",
      });
    });
    return () => cancelAnimationFrame(frame);
  }, [focused, displayRegion?.x0, displayRegion?.x1, displayRegion?.top, displayRegion?.bottom, scale, viewportSize.width, viewportSize.height]);

  if (!available) return <div className="pdf-empty">{t("pdfUnavailable")}</div>;

  return <div className="pdf-panel">
    <div className="pdf-toolbar"><div className="page-chips">{pages.map((value) => <button key={value} className={value === page ? "active" : ""} onClick={() => onPage(value)}>P{value}</button>)}</div><div className="viewer-actions">
      <div className="pdf-page-navigation"><button aria-label={t("previousPage")} title={t("previousPage")} disabled={page <= 1} onClick={() => onPage(page - 1)}>‹</button><button className="current-table-page" title={t("returnToTablePage")} onClick={() => onPage(pages[0] || page)}>{page}/{pageCount}</button><button aria-label={t("nextPage")} title={t("nextPage")} disabled={page >= pageCount} onClick={() => onPage(page + 1)}>›</button></div>
      <button aria-label={t("rotateLeft")} title={t("rotateLeft")} onClick={() => setRotation((value) => (value + 270) % 360)}>↶</button>
      <button aria-label={t("rotateRight")} title={t("rotateRight")} onClick={() => setRotation((value) => (value + 90) % 360)}>↷</button>
      <button onClick={() => setZoom(Math.max(0.6, zoom - 0.15))}>−</button><span>{Math.round(zoom * 100)}%</span><button onClick={() => setZoom((value) => value + 0.15)}>＋</button>
    </div></div>
    <div ref={scroll} className={`pdf-scroll ${focused ? "focused" : ""}`}>{error ? <div className="error-box">{error}</div> : null}<div ref={stage} className="pdf-stage" style={stageStyle}><canvas ref={canvas} />{overlayStyle ? <div className="bbox-overlay" style={overlayStyle} /> : null}</div></div>
  </div>;
}
