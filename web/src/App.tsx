import { useState } from "react";
import { Layout } from "./components/Layout";
import { InboxView } from "./components/InboxView";
import { MorningBrief } from "./components/MorningBrief";
import { ApplyPolicyPanel } from "./components/ApplyPolicyPanel";
import { ComposeDialog } from "./components/ComposeDialog";
import { LabelManager } from "./components/LabelManager";
import { DraftsView } from "./components/DraftsView";
import { SentView } from "./components/SentView";

/**
 * Top-level shell. Tabs switch between the main modes of the app:
 *
 *   Inbox       — browse, classify, reply, archive, label individual threads.
 *   Morning     — the single-call "give me my morning" brief.
 *   Auto-apply  — the bulk-action panel: classify a batch + apply a policy.
 *   Drafts      — the drafts Gmail currently holds that came from this tool.
 *   Sent        — read-only browser over Gmail's Sent folder.
 *   Labels      — create / rename / preview Gmail labels.
 *
 * Compose is modal (ComposeDialog) so it overlays any tab.
 */
export type TabKey =
  | "inbox"
  | "morning"
  | "auto"
  | "drafts"
  | "sent"
  | "labels";

const TABS: { key: TabKey; label: string }[] = [
  { key: "inbox", label: "Inbox" },
  { key: "morning", label: "Morning brief" },
  { key: "auto", label: "Auto-apply" },
  { key: "drafts", label: "Drafts" },
  { key: "sent", label: "Sent" },
  { key: "labels", label: "Labels" },
];

export function App() {
  const [tab, setTab] = useState<TabKey>("inbox");
  const [composeOpen, setComposeOpen] = useState(false);

  return (
    <Layout
      tabs={TABS}
      activeTab={tab}
      onTabChange={setTab}
      onCompose={() => setComposeOpen(true)}
    >
      {tab === "inbox" && <InboxView />}
      {tab === "morning" && <MorningBrief />}
      {tab === "auto" && <ApplyPolicyPanel />}
      {tab === "drafts" && <DraftsView />}
      {tab === "sent" && <SentView />}
      {tab === "labels" && <LabelManager />}

      {composeOpen && <ComposeDialog onClose={() => setComposeOpen(false)} />}
    </Layout>
  );
}
