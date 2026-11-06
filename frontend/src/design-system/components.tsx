/**
 * PortalPilot design system: the Studio's building blocks. Rules (docs/DESIGN_SYSTEM.md):
 *  - components take semantic props (tone="bad", tier="qwen"), never colors
 *  - every status is shown with text as well as color, so nothing depends on color vision
 *  - interactive elements are real buttons/links with visible focus; icons are never the only label
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";

export type Tone = "neutral" | "accent" | "ok" | "warn" | "bad" | "operator";
export type Tier = "cache" | "qwen" | "claude" | "dom" | "operator";

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

// ------------------------------------------------------------------ actions
type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "secondary" | "primary" | "danger" | "ok" | "ghost";
  size?: "sm" | "md";
  busy?: boolean;
};
export function Button({ variant = "secondary", size = "md", busy, className, children, disabled, ...rest }: ButtonProps) {
  return (
    <button {...rest} disabled={disabled || busy} aria-busy={busy || undefined}
            className={cx("ds-btn", `ds-btn--${variant}`, size === "sm" && "ds-btn--sm", className)}>
      {busy ? "Working…" : children}
    </button>
  );
}

// ------------------------------------------------------------------ status
const STATUS_TONE: Record<string, Tone> = {
  succeeded: "ok", completed: "ok", resolved: "ok", confirmed: "ok", reconciled_found: "ok",
  running: "accent", queued: "accent", in_flight: "accent", acknowledged: "accent",
  waiting_human: "warn", completed_with_errors: "warn", open: "warn", ticket: "warn", intent: "neutral",
  failed: "bad", rejected: "bad", expired: "bad", invalid: "bad", firing: "bad", page: "bad",
};
export const toneFor = (status: string): Tone => STATUS_TONE[status] ?? "neutral";

/** A status word in a tinted outline. The text is always the status itself. */
export function StatusChip({ status, tone }: { status: string; tone?: Tone }) {
  return <span className={cx("ds-chip", `ds-tone--${tone ?? toneFor(status)}`)}>{status.replaceAll("_", " ")}</span>;
}

/** Which grounding tier produced an action. Filled, because it sits on screenshots and in dense timelines. */
export function TierBadge({ tier }: { tier: string | null }) {
  return <span className={cx("ds-tier", tier ? `ds-tier--${tier}` : "ds-tier--none")}>{tier ?? "—"}</span>;
}

export function LiveDot({ on, label }: { on: boolean; label?: string }) {
  return <span className={cx("ds-live", on && "ds-live--on")} role="status"><i aria-hidden />{label ?? (on ? "live" : "offline")}</span>;
}

// ------------------------------------------------------------------ containers
export function Card({ children, tone, className, as: As = "div", ...rest }:
  { children: ReactNode; tone?: Extract<Tone, "warn" | "bad">; className?: string; as?: "div" | "section" | "a"; href?: string }) {
  return <As {...rest} className={cx("ds-card", tone && `ds-card--${tone}`, className)}>{children}</As>;
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  return (
    <Card>
      <div className="ds-stat__label">{label}</div>
      <div className={cx("ds-stat__value", tone && `ds-tone--${tone}`)}>{value}</div>
      {hint && <div className="ds-stat__hint">{hint}</div>}
    </Card>
  );
}

export function StatGrid({ children }: { children: ReactNode }) {
  return <div className="ds-stats">{children}</div>;
}

export function PageHeader({ title, actions, children }: { title: string; actions?: ReactNode; children?: ReactNode }) {
  return (
    <header className="ds-page-header">
      <div><h1>{title}</h1>{children && <p className="ds-muted">{children}</p>}</div>
      {actions && <div className="ds-row">{actions}</div>}
    </header>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="ds-empty">
      <b>{title}</b>
      {children && <div className="ds-muted">{children}</div>}
    </div>
  );
}

/** A labelled definition list for inspectors (task, ledger, incident). */
export function KeyValue({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="ds-kv">
      {items.map(([k, v]) => <div key={k} className="ds-kv__row"><dt>{k}</dt><dd>{v ?? "—"}</dd></div>)}
    </dl>
  );
}

// ------------------------------------------------------------------ data
export function Progress({ parts, label }: { parts: { n: number; tone: Tone | Tier; label: string }[]; label: string }) {
  const total = parts.reduce((a, p) => a + p.n, 0) || 1;
  return (
    <div className="ds-progress" role="img" aria-label={`${label}: ${parts.map((p) => `${p.label} ${p.n}`).join(", ")}`}>
      {parts.map((p, i) => (
        <span key={i} title={`${p.label}: ${p.n}`} className={`ds-fill--${p.tone}`} style={{ width: `${(100 * p.n) / total}%` }} />
      ))}
    </div>
  );
}

/** A vertical event timeline (incidents, task history). */
export function Timeline({ items }: { items: { at: string; title: ReactNode; body?: ReactNode; tone?: Tone }[] }) {
  return (
    <ol className="ds-timeline">
      {items.map((it, i) => (
        <li key={i} className={`ds-tone--${it.tone ?? "neutral"}`}>
          <time>{new Date(it.at).toLocaleTimeString()}</time>
          <div><div className="ds-timeline__title">{it.title}</div>{it.body && <div className="ds-muted">{it.body}</div>}</div>
        </li>
      ))}
    </ol>
  );
}

// ------------------------------------------------------------------ forms
export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="ds-field">
      <span className="ds-field__label">{label}</span>
      {children}
      {hint && <span className="ds-field__hint">{hint}</span>}
    </label>
  );
}
