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
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="text-[10px] text-[var(--color-primary)] hover:underline whitespace-nowrap"
      aria-label={description}
    >
      {label} <span aria-hidden="true">↗</span>
    </a>
  );
}
