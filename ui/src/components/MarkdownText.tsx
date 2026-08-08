import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface MarkdownTextProps {
  text: string;
  className?: string;
}

const blockComponents = {
  h1({ children }: { children?: React.ReactNode }) {
    return (
      <h1 className="text-xs font-bold text-ink border-b border-hairline pb-1 mt-1 mb-1 uppercase tracking-wide">
        {children}
      </h1>
    );
  },
  h2({ children }: { children?: React.ReactNode }) {
    return <h2 className="text-xs font-bold text-ink mt-1.5 mb-0.5">{children}</h2>;
  },
  h3({ children }: { children?: React.ReactNode }) {
    return <h3 className="text-xs font-semibold text-ink mt-1 mb-0.5">{children}</h3>;
  },
  p({ children }: { children?: React.ReactNode }) {
    return <p className="text-xs text-muted leading-relaxed my-1">{children}</p>;
  },
  ul({ children }: { children?: React.ReactNode }) {
    return <ul className="list-disc pl-4 space-y-0.5 text-xs text-muted my-1">{children}</ul>;
  },
  ol({ children }: { children?: React.ReactNode }) {
    return <ol className="list-decimal pl-4 space-y-0.5 text-xs text-muted my-1">{children}</ol>;
  },
  li({ children }: { children?: React.ReactNode }) {
    return <li className="text-xs text-muted leading-normal">{children}</li>;
  },
  strong({ children }: { children?: React.ReactNode }) {
    return <strong className="font-semibold text-ink">{children}</strong>;
  },
  em({ children }: { children?: React.ReactNode }) {
    return <em className="italic">{children}</em>;
  },
  del({ children }: { children?: React.ReactNode }) {
    return <del className="line-through text-faint opacity-80">{children}</del>;
  },
  pre({ children }: { children?: React.ReactNode }) {
    return (
      <pre className="p-2 rounded bg-surface border border-line font-mono text-xs overflow-x-auto my-1 text-ink bg-canvas/60">
        {children}
      </pre>
    );
  },
  code({
    className,
    children,
  }: {
    className?: string;
    children?: React.ReactNode;
  }) {
    if (className || (typeof children === 'string' && children.includes('\n'))) {
      return <code className={`font-mono text-xs text-ink ${className || ''}`}>{children}</code>;
    }
    return (
      <code className="px-1 py-0.5 rounded bg-surface border border-line font-mono text-micro text-ink">
        {children}
      </code>
    );
  },
  a({ href, children }: { href?: string; children?: React.ReactNode }) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-accent underline hover:text-accent/80 transition-colors"
      >
        {children}
      </a>
    );
  },
  blockquote({ children }: { children?: React.ReactNode }) {
    return (
      <blockquote className="border-l-2 border-accent/40 pl-2 italic text-faint text-xs my-1">
        {children}
      </blockquote>
    );
  },
};

const inlineComponents = {
  p({ children }: { children?: React.ReactNode }) {
    return <>{children}</>;
  },
  strong({ children }: { children?: React.ReactNode }) {
    return <strong className="font-semibold text-ink">{children}</strong>;
  },
  em({ children }: { children?: React.ReactNode }) {
    return <em className="italic">{children}</em>;
  },
  del({ children }: { children?: React.ReactNode }) {
    return <del className="line-through text-faint opacity-80">{children}</del>;
  },
  code({ children }: { children?: React.ReactNode }) {
    return (
      <code className="px-1 py-0.5 rounded bg-surface border border-line font-mono text-micro text-ink">
        {children}
      </code>
    );
  },
  a({ href, children }: { href?: string; children?: React.ReactNode }) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-accent underline hover:text-accent/80 transition-colors"
      >
        {children}
      </a>
    );
  },
};

export function MarkdownText({ text, className }: MarkdownTextProps) {
  if (typeof text !== 'string' || !text) return null;
  return (
    <div className={className || 'space-y-1'}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={blockComponents}>
        {text}
      </ReactMarkdown>
    </div>
  );
}

export function MarkdownInline({ text }: { text: string }) {
  if (typeof text !== 'string' || !text) return null;
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={inlineComponents}>
      {text}
    </ReactMarkdown>
  );
}
