import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// `cn` — the usual Tailwind merge helper. Lets components take className
// props without fighting the design-system classes.
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    const now = new Date();
    const sameDay =
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate();
    if (sameDay) {
      return d.toLocaleTimeString(undefined, {
        hour: "numeric",
        minute: "2-digit",
      });
    }
    return d.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

export function categoryColor(cat: string): string {
  switch (cat) {
    case "urgent":
      return "bg-red-100 text-red-800 border-red-200";
    case "needs_reply":
      return "bg-blue-100 text-blue-800 border-blue-200";
    case "fyi":
      return "bg-slate-100 text-slate-700 border-slate-200";
    case "newsletter":
      return "bg-violet-100 text-violet-800 border-violet-200";
    case "trash":
      return "bg-zinc-100 text-zinc-600 border-zinc-200";
    default:
      return "bg-slate-100 text-slate-700 border-slate-200";
  }
}
