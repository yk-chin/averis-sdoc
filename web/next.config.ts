import type { NextConfig } from "next";

const API_BASE = (process.env.API_BASE || "https://shipdoc-api-705106212012.asia-southeast1.run.app").replace(/\/$/, "");

const nextConfig: NextConfig = {
  // Browser calls go to /api/* and are proxied server-side to Cloud Run: no CORS, backend URL not exposed.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_BASE}/:path*` }];
  },
};

export default nextConfig;
