import { categoryColor } from "../lib/utils";
import type { Category } from "../types";

const LABELS: Record<Category, string> = {
  urgent: "Urgent",
  needs_reply: "Needs reply",
  fyi: "FYI",
  newsletter: "Newsletter",
  trash: "Trash",
};

export function CategoryBadge({
  category,
  confidence,
}: {
  category: Category;
  confidence?: number;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${categoryColor(
        category,
      )}`}
    >
      {LABELS[category] ?? category}
      {confidence !== undefined && (
        <span className="text-[10px] opacity-60">
          {Math.round(confidence * 100)}%
        </span>
      )}
    </span>
  );
}
