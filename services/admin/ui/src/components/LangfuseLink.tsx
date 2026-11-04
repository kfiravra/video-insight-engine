interface LangfuseLinkProps {
  /** The server-built Langfuse URL; nothing renders without one. */
  href: string | null;
  /** The visible text, e.g. "Trace". */
  label: string;
  /** What the link opens, for screen readers, e.g. "Open trace in Langfuse". */
  description: string;
}

export function LangfuseLink({ href, label, description }: LangfuseLinkProps) {
  if (!href) return null;
  // min-h-6: a 24px hit area (WCAG 2.5.8) around the 10px label, so the Trace and Session
  // links stacked on phones are separate targets.
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="inline-flex min-h-6 items-center gap-0.5 text-[10px] text-[var(--color-primary)] hover:underline whitespace-nowrap"
      aria-label={description}
    >
      {label} <span aria-hidden="true">↗</span>
    </a>
  );
}
