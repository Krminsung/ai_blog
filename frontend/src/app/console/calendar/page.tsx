import type { Metadata } from "next";

import { CalendarView } from "@/components/console/calendar-view";

export const metadata: Metadata = { title: "캘린더" };

export default function CalendarPage() {
  return <CalendarView />;
}
