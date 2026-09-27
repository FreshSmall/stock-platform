import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

// Shared markdown renderer with GFM support, so pipe tables (and other GFM
// syntax) render as real tables everywhere .assistant-md styles are used.
export default function Markdown({ children }: { children?: string | null }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]}>{children ?? ''}</ReactMarkdown>;
}
