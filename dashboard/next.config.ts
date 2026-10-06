import type { NextConfig } from "next";
const config: NextConfig = { output: process.env.POWERALL_STANDALONE === "1" ? "standalone" : undefined, poweredByHeader: false };
export default config;
