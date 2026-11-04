import { useState } from "react";
import { shot, type Step } from "../api";

const TIER_COLOR: Record<string, string> = {
  qwen: "var(--qwen)", claude: "var(--claude)", cache: "var(--cache)", dom: "var(--dom)", operator: "var(--operator)",
};

/**
 * A screenshot with the agent's perception drawn on top, in the screenshot's own pixel space:
 *  - every tier's proposed point (rejected ones as ✕ with the tier name)
 *  - the winning point as a crosshair and the verified element's bounding box
 * In takeover mode the image is the live browser and a click sends an operator click at that pixel.
 */
export default function ScreenshotStage({ sha, step, takeover, banner, onClick }: {
  sha: string | null | undefined;
  step?: Step | null;
  takeover?: boolean;
  banner?: string;
  onClick?: (x: number, y: number) => void;
}) {
  const [nat, setNat] = useState<{ w: number; h: number } | null>(null);
  const w = step?.width ?? nat?.w ?? 1280;
  const h = step?.height ?? nat?.h ?? 800;
  const winner = step?.grounder;
  const color = TIER_COLOR[winner ?? ""] ?? "var(--accent)";

  const click = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!takeover || !onClick) return;
    const r = e.currentTarget.getBoundingClientRect();
    onClick(Math.round(((e.clientX - r.left) / r.width) * w), Math.round(((e.clientY - r.top) / r.height) * h));
  };

  if (!sha) return <div className="stagebox" style={{ aspectRatio: "16 / 10", display: "grid", placeItems: "center" }}><span className="muted">No screenshot yet</span></div>;
  return (
    <div className={`stagebox ${takeover ? "takeover" : ""}`} onClick={click}>
      <img src={shot(sha)} alt="browser screenshot" onLoad={(e) => setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })} />
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
        {step?.grounder_trace?.filter((a) => a.found && !a.verified && a.x != null).map((a, i) => (
          <g key={i} stroke="var(--bad)" strokeWidth={3}>
            <line x1={a.x! - 9} y1={a.y! - 9} x2={a.x! + 9} y2={a.y! + 9} />
            <line x1={a.x! - 9} y1={a.y! + 9} x2={a.x! + 9} y2={a.y! - 9} />
            <text x={a.x! + 12} y={a.y! - 10} fill="var(--bad)" stroke="none" fontSize={15} fontWeight={700}>{a.tier} ✕</text>
          </g>
        ))}
        {step?.bbox && (
          <rect x={step.bbox.x - 3} y={step.bbox.y - 3} width={step.bbox.w + 6} height={step.bbox.h + 6}
                fill="none" stroke={color} strokeWidth={3} rx={4} />
        )}
        {step?.point_x != null && step.point_y != null && (
          <g stroke={color} strokeWidth={2.5}>
            <circle cx={step.point_x} cy={step.point_y} r={11} fill="none" />
            <line x1={step.point_x - 18} y1={step.point_y} x2={step.point_x + 18} y2={step.point_y} />
            <line x1={step.point_x} y1={step.point_y - 18} x2={step.point_x} y2={step.point_y + 18} />
            <text x={step.point_x + 16} y={step.point_y + 28} fill={color} stroke="none" fontSize={15} fontWeight={700}>
              {winner}{step.verified ? " ✓" : ""}
            </text>
          </g>
        )}
      </svg>
      {banner && <div className="banner">{banner}</div>}
    </div>
  );
}
