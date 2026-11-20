import clsx from "clsx";
import type { ReactNode } from "react";
import { statusLabel, statusTone, type Tone } from "../lib/format";

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "border-slate-600/50 bg-slate-700/25 text-slate-300",
  good: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  warn: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  bad: "border-rose-500/40 bg-rose-500/10 text-rose-300",
  info: "border-cyan-500/40 bg-cyan-500/10 text-cyan-300",
  violet: "border-violet-500/40 bg-violet-500/10 text-violet-300",
};

const DOT_CLASSES: Record<Tone, string> = {
  neutral: "bg-slate-400",
  good: "bg-emerald-400",
  warn: "bg-amber-400",
  bad: "bg-rose-400",
  info: "bg-cyan-400",
  violet: "bg-violet-400",
};

export function StatusBadge({
  status,
  label,
  tone,
  pulse = false,
  className,
}: {
  status?: string | null;
  label?: ReactNode;
  tone?: Tone;
  pulse?: boolean;
  className?: string;
}) {
  const resolvedTone = tone ?? statusTone(status);
  return (
    <span className={clsx("badge", TONE_CLASSES[resolvedTone], className)}>
      <span className={clsx("h-1.5 w-1.5 rounded-full", DOT_CLASSES[resolvedTone], pulse && "pulse-dot")} />
      {label ?? statusLabel(status)}
    </span>
  );
}
