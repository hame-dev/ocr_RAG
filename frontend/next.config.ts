import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // The browser talks to Django directly (NEXT_PUBLIC_API_BASE) rather than
  // through a Next proxy — that is what keeps the SSE streams unbuffered.
};

export default config;
