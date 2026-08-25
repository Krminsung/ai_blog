import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin the workspace root so Turbopack does not walk up past the repository
  // and pick up an unrelated lockfile in the home directory.
  turbopack: {
    root: path.join(__dirname),
  },
};

export default nextConfig;
