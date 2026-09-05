import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type Language = "de" | "en";

const messages = {
  de: {
    documentTitle: "PDF-Tabellenprüfung",
    brand: "Tabellenprüfung",
    progress: "{{reviewed}} / {{total}} abgeschlossen",
    tableReview: "Tabellenprüfung",
    documentComparison: "Dokumentvergleich",
    pageCount: "{{count}} Seiten",
    syncScroll: "Synchron scrollen",
    comparisonHint: "PDF und erzeugtes Markdown seitenweise vergleichen",
    loadingComparison: "Dokumentvergleich wird geladen…",
    loadingPdf: "PDF wird geladen…",
    emptyMarkdownPage: "Für diese Seite wurde kein Markdown erzeugt.",
    hideSidebar: "Seitenleiste ausblenden",
    showSidebar: "Seitenleiste einblenden",
    zoomOut: "PDF verkleinern",
    zoomIn: "PDF vergrößern",
    search: "Tabelle oder Seite suchen…",
    pendingOnly: "Nur offene anzeigen",
    difference: "Diff. {{count}}",
    applicationApplied: "Aktuelles Prüfergebnis angewendet",
    applicationStale: "Neue Änderungen – erneut anwenden",
    applicationNever: "Prüfergebnis noch nicht angewendet",
    recorded: "Gespeichert",
    applyPdf: "Prüfergebnis auf aktuelles PDF anwenden",
    pdfUnavailable: "Der Pfad zur Original-PDF ist nicht verfügbar. Bitte source.path in metadata prüfen.",
    showFullPage: "Ganze Seite",
    focusTable: "Tabelle fokussieren",
    emptyTable: "Keine strukturierte Tabelle verfügbar.",
    dimensions: "{{rows}} Zeilen × {{columns}} Spalten",
    rowNumber: "Zeile {{number}}",
    columnNumber: "Spalte {{label}}",
    insertAbove: "Darüber einfügen",
    insertBelow: "Darunter einfügen",
    deleteRow: "Zeile löschen",
    insertLeft: "Links einfügen",
    insertRight: "Rechts einfügen",
    deleteColumn: "Spalte löschen",
    previousPage: "Vorherige Seite",
    nextPage: "Nächste Seite",
    selectRow: "Zeile {{number}} auswählen",
    selectColumn: "Spalte {{label}} auswählen",
    resizeColumn: "Spaltenbreite ziehen",
    diffCells: "{{count}} unterschiedliche Zellen",
    diffEqual: "Beide strukturierten Ergebnisse stimmen überein",
    draftSaved: "Entwurf lokal gespeichert",
    applyConfirm: "Dadurch werden output.md, CSV und metadata anhand des manuellen Prüfergebnisses aktualisiert. Fortfahren?",
    appliedCount: "{{count}} manuelle Ergebnisse angewendet",
    saving: "Speichern…",
    unsaved: "● Änderungen nicht gespeichert",
    noUnsaved: "Keine ungespeicherten Änderungen",
    loading: "Prüfprojekt wird geladen…",
    finalTab: "Manuelles Ergebnis",
    aiTab: "KI-Analyse",
    diffTab: "Differenzen ({{count}})",
    previousTable: "Vorherige Tabelle",
    nextTable: "Nächste Tabelle",
    note: "Notiz",
    approve: "Freigeben",
    ignore: "Ignorieren",
    reviewNote: "Prüfnotiz",
    notePlaceholder: "Problem oder Änderungsgrund festhalten",
    done: "Fertig",
    aiSource: "Von Pi extrahierte Original-CSV (schreibgeschützt)",
    restoreAi: "KI-Ergebnis wiederherstellen",
    continuousView: "Fortlaufende Ansicht",
    useAsFinal: "Als manuelles Ergebnis verwenden",
    rawMarkdown: "Original-Markdown anzeigen",
    selectTable: "Bitte eine Tabelle zur Prüfung auswählen.",
    language: "Sprache",
  },
  en: {
    documentTitle: "PDF Table Review",
    brand: "Table Review",
    progress: "{{reviewed}} / {{total}} completed",
    tableReview: "Table review",
    documentComparison: "Document comparison",
    pageCount: "{{count}} pages",
    syncScroll: "Sync scrolling",
    comparisonHint: "Compare the PDF and generated Markdown page by page",
    loadingComparison: "Loading document comparison…",
    loadingPdf: "Loading PDF…",
    emptyMarkdownPage: "No Markdown was generated for this page.",
    hideSidebar: "Hide sidebar",
    showSidebar: "Show sidebar",
    zoomOut: "Zoom PDF out",
    zoomIn: "Zoom PDF in",
    search: "Search table or page…",
    pendingOnly: "Show pending only",
    difference: "Diff {{count}}",
    applicationApplied: "Current review result applied",
    applicationStale: "New changes — apply again",
    applicationNever: "Review result not yet applied",
    recorded: "Recorded",
    applyPdf: "Apply current PDF review result",
    pdfUnavailable: "The original PDF path is unavailable. Please keep source.path in metadata valid.",
    showFullPage: "Show full page",
    focusTable: "Focus table",
    emptyTable: "No structured table to display.",
    dimensions: "{{rows}} rows × {{columns}} columns",
    rowNumber: "Row {{number}}",
    columnNumber: "Column {{label}}",
    insertAbove: "Insert above",
    insertBelow: "Insert below",
    deleteRow: "Delete row",
    insertLeft: "Insert left",
    insertRight: "Insert right",
    deleteColumn: "Delete column",
    previousPage: "Previous page",
    nextPage: "Next page",
    selectRow: "Select row {{number}}",
    selectColumn: "Select column {{label}}",
    resizeColumn: "Drag to resize column",
    diffCells: "{{count}} cells differ",
    diffEqual: "The two structured results match",
    draftSaved: "Draft saved locally",
    applyConfirm: "This will update output.md, CSV, and metadata using the manual review result. Continue?",
    appliedCount: "Applied {{count}} manual results",
    saving: "Saving…",
    unsaved: "● Unsaved changes",
    noUnsaved: "No unsaved changes",
    loading: "Loading review project…",
    finalTab: "Manual result",
    aiTab: "AI analysis",
    diffTab: "Differences ({{count}})",
    previousTable: "Previous table",
    nextTable: "Next table",
    note: "Note",
    approve: "Approve",
    ignore: "Ignore",
    reviewNote: "Review note",
    notePlaceholder: "Record the issue or reason for the change",
    done: "Done",
    aiSource: "Original CSV extracted by Pi (read-only)",
    restoreAi: "Restore AI result",
    continuousView: "Continuous view",
    useAsFinal: "Use as manual result",
    rawMarkdown: "View original Markdown",
    selectTable: "Select a table to begin reviewing.",
    language: "Language",
  },
} as const;

export type TranslationKey = keyof typeof messages.de;
type Variables = Record<string, string | number>;

interface I18nValue {
  language: Language;
  setLanguage: (language: Language) => void;
  t: (key: TranslationKey, variables?: Variables) => string;
}

const I18nContext = createContext<I18nValue | undefined>(undefined);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguage] = useState<Language>(() => {
    const stored = window.localStorage.getItem("pdf-table-review-language");
    return stored === "de" || stored === "en" ? stored : "de";
  });
  useEffect(() => {
    window.localStorage.setItem("pdf-table-review-language", language);
    document.documentElement.lang = language;
    document.title = messages[language].documentTitle;
  }, [language]);
  const value = useMemo<I18nValue>(() => ({
    language,
    setLanguage,
    t: (key, variables = {}) => Object.entries(variables).reduce(
      (text, [name, replacement]) => text.replaceAll(`{{${name}}}`, String(replacement)),
      messages[language][key] as string,
    ),
  }), [language]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used within I18nProvider");
  return value;
}
