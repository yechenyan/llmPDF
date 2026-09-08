type MarkdownNode = {
  type: string;
  value?: string;
  children?: MarkdownNode[];
};

// Render the inline breaks emitted by CSV tables without enabling arbitrary HTML.
export default function remarkCellBreaks() {
  return function transform(tree: MarkdownNode) {
    function visit(node: MarkdownNode) {
      node.children?.forEach((child, index, children) => {
        if (child.type === "html" && /^<br\s*\/?>$/i.test(child.value || "")) {
          children[index] = { type: "break" };
        } else {
          visit(child);
        }
      });
    }
    visit(tree);
  };
}
