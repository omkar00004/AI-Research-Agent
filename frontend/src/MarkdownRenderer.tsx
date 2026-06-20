import { useEffect, useRef, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import mermaid from "mermaid";

/* ---- Mermaid config ---- */
mermaid.initialize({
  startOnLoad: false,
  theme: "default",
  securityLevel: "loose",
  fontFamily: "'Outfit', sans-serif",
});

let mermaidCounter = 0;

/**
 * Renders a Mermaid code block as an inline SVG diagram.
 */
function MermaidBlock({ code }: { code: string }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let isCancelled = false;
    const id = `mermaid-${++mermaidCounter}`;
    
    const cleanCode = code.replace(/^mermaid\s*\n/, "").trim();

    // Use a small timeout to ensure the container is mounted and ready
    const timer = setTimeout(() => {
      if (containerRef.current) {
        mermaid.render(id, cleanCode, containerRef.current)
          .then(({ svg }) => {
            if (!isCancelled && containerRef.current) {
              containerRef.current.innerHTML = svg;
            }
          })
          .catch((err) => {
            console.error("Mermaid render error:", err);
            if (!isCancelled && containerRef.current) {
              containerRef.current.innerHTML = `
                <div class="flex flex-col gap-2 w-full">
                  <div class="text-red-600 font-bold text-sm bg-red-50 p-2 rounded border border-red-200">
                    Failed to render diagram: ${err.message || err}
                  </div>
                  <pre class="bg-gray-100 p-3 rounded text-xs text-gray-800 overflow-auto whitespace-pre-wrap font-mono border border-gray-200"><code>${cleanCode.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</code></pre>
                </div>
              `;
            }
          });
      }
    }, 50);

    return () => {
      isCancelled = true;
      clearTimeout(timer);
    };
  }, [code]);

  return (
    <div
      ref={containerRef}
      className="my-6 flex justify-center overflow-x-auto rounded-xl bg-[#F9FAFB] p-6 border border-[#E5E7EB] min-h-[100px]"
    />
  );
}

/* ---- Markdown Renderer ---- */

// Safely extract text from React children
function extractTextFromChildren(children: any): string {
  if (typeof children === "string" || typeof children === "number") {
    return String(children);
  }
  if (Array.isArray(children)) {
    return children.map(extractTextFromChildren).join("");
  }
  if (children && typeof children === "object" && children.props && children.props.children) {
    return extractTextFromChildren(children.props.children);
  }
  return "";
}

interface Props {
  content: string;
}

export default function MarkdownRenderer({ content }: Props) {
  const copyToClipboard = useCallback((text: string) => {
    navigator.clipboard.writeText(text);
  }, []);

  return (
    <article className="prose-report">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          /* ---- Code blocks ---- */
          code({ className, children, ...rest }) {
            const match = /language-(\w+)/.exec(className || "");
            const codeString = extractTextFromChildren(children).replace(/\n$/, "");

            // Mermaid diagrams
            if (match?.[1] === "mermaid") {
              return <MermaidBlock code={codeString} />;
            }

            // Multi-line code blocks (rendered inside <pre> by react-markdown)
            const isInline = !className && !codeString.includes("\n");
            if (isInline) {
              return (
                <code className="inline-code" {...rest}>
                  {children}
                </code>
              );
            }

            return (
              <div className="code-block-wrapper">
                <div className="code-block-header">
                  <span className="code-block-lang">{match?.[1] || "text"}</span>
                  <button
                    onClick={() => copyToClipboard(codeString)}
                    className="code-copy-btn"
                  >
                    Copy
                  </button>
                </div>
                <code className={className} {...rest}>
                  {children}
                </code>
              </div>
            );
          },

          /* ---- Tables ---- */
          table({ children }) {
            return (
              <div className="table-wrapper">
                <table>{children}</table>
              </div>
            );
          },

          /* ---- Links open in new tab ---- */
          a({ href, children }) {
            return (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            );
          },

          /* ---- Blockquotes ---- */
          blockquote({ children }) {
            return <blockquote className="report-blockquote">{children}</blockquote>;
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </article>
  );
}
