import type { Metadata } from "next";

import { PostsView } from "@/components/console/posts-view";

export const metadata: Metadata = { title: "발행된 글" };

export default function PostsPage() {
  return <PostsView />;
}
