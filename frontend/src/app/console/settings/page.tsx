import type { Metadata } from "next";

import { WorkspaceSettings } from "@/components/console/workspace-settings";

export const metadata: Metadata = { title: "워크스페이스 설정" };

export default function SettingsPage() {
  return <WorkspaceSettings />;
}
