import { repeatedCellToneMap } from "./reviewUtils";

type MarkdownNode = {
  type: string;
  value?: string;
  children?: MarkdownNode[];
  data?: {
    hProperties?: Record<string, unknown>;
    [key: string]: unknown;
  };
};

function nodeText(node: MarkdownNode): string {
  if (node.type === "break") return "\n";
  if (typeof node.value === "string") return node.value;
  return (node.children || []).map(nodeText).join("");
}

export default function remarkRepeatedCellTones() {
  return function transform(tree: MarkdownNode) {
    function visit(node: MarkdownNode) {
      if (node.type === "table") {
        const rows = (node.children || []).filter((child) => child.type === "tableRow");
        const cells = rows.map((row) =>
          (row.children || []).filter((child) => child.type === "tableCell"),
        );
        const tones = repeatedCellToneMap(
          cells.map((row) => row.map(nodeText)),
        );
        cells.forEach((row, rowIndex) => row.forEach((cell, columnIndex) => {
          const tone = tones.get(`${rowIndex}:${columnIndex}`);
          if (!tone) return;
          const hProperties = cell.data?.hProperties || {};
          const existing = hProperties.className;
          cell.data = {
            ...cell.data,
            hProperties: {
              ...hProperties,
              className: [
                ...(Array.isArray(existing) ? existing : existing ? [existing] : []),
                ...tone.split(" "),
              ],
            },
          };
        }));
      }
      node.children?.forEach(visit);
    }
    visit(tree);
  };
}
