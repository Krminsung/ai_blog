import type { Metadata } from "next";

import { MediaView } from "@/components/console/media-view";

export const metadata: Metadata = { title: "미디어" };

export default function MediaPage() {
  return <MediaView />;
}
