import type { Metadata } from "next";

import { ProfileSettings } from "@/components/console/profile-settings";

export const metadata: Metadata = { title: "프로필과 보안" };

export default function ProfileSettingsPage() {
  return <ProfileSettings />;
}
