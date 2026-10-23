/** @type {import('next').NextConfig} */

// Same-origin proxy to the FastAPI backend: the browser only ever talks to
// the Next origin, so the HttpOnly portal cookie (host-scoped, port-free)
// applies to both the API calls and the ws://localhost:8000/ws/voice
// handshake without any CORS/credentials ceremony.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${BACKEND_URL}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
