import type { Metadata } from "next";

import { Dashboard } from "@/components/console/dashboard";

export const metadata: Metadata = {
  title: "대시보드",
};

export default function ConsoleHomePage() {
  return <Dashboard />;
}
