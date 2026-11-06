// Thin app-level helpers. Visual building blocks live in the design system (src/design-system).
import { StatusChip, TierBadge } from "../design-system";

export const Status = ({ s }: { s: string }) => <StatusChip status={s} />;
export const Grounder = ({ g }: { g: string | null }) => <TierBadge tier={g} />;

export const money = (v: number | null | undefined, digits = 4) => `$${Number(v ?? 0).toFixed(digits)}`;

export const ago = (iso: string | null | undefined) => {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
};
