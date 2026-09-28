import type { NextConfig } from "next";

// Two builds from one tree.
//
//   docker  — a server, with /api proxied to the engine over the private network
//   desktop — a static export loaded by the Tauri shell, talking straight to the
//             engine on loopback (see lib/api.ts)
const desktop = process.env.CONVERTOR_TARGET === "desktop";

const nextConfig: NextConfig = desktop
  ? {
      output: "export",
      distDir: "out-build",
      images: { unoptimized: true },
      // Tauri serves the export from a file-like origin, so every asset path
      // has to be relative rather than rooted at /.
      assetPrefix: "./",
    }
  : {
      output: "standalone",
      // /api is proxied by a runtime route handler rather than a rewrite:
      // rewrite destinations are frozen into the build, and this deployment
      // needs ENGINE_URL to be read when the container starts.
      experimental: {
        proxyTimeout: 30 * 60 * 1000,
      },
    };

export default nextConfig;
