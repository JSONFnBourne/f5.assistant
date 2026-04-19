/** @type {import('next').NextConfig} */

const isProd = process.env.NODE_ENV === 'production';

// Dev: Turbopack requires unsafe-eval for on-the-fly module compilation and
// unsafe-inline for its HMR overlay. connect-src must allow ws: for HMR.
// Prod: no eval, locked connect-src. 'unsafe-inline' must remain for scripts
// because Next.js emits inline scripts for the RSC payload (__next_f.push)
// and the next-themes anti-flash snippet — without it React never hydrates,
// onClick handlers don't attach, and the theme toggle / file picker break.
// Switching to nonce-based CSP would require a custom middleware.
const CSP = isProd
    ? "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self';"
    : "default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; font-src 'self'; connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'self'; form-action 'self';";

const nextConfig = {
    reactStrictMode: true,
    output: 'standalone',
    poweredByHeader: false,
    experimental: {
        serverActions: {
            bodySizeLimit: '2000mb'
        },
        proxyClientMaxBodySize: '2000mb'
    },
    async headers() {
        return [
            {
                source: '/:path*',
                headers: [
                    {
                        key: 'X-DNS-Prefetch-Control',
                        value: 'on'
                    },
                    {
                        key: 'Strict-Transport-Security',
                        value: 'max-age=63072000; includeSubDomains; preload'
                    },
                    {
                        key: 'X-XSS-Protection',
                        value: '1; mode=block'
                    },
                    {
                        key: 'X-Frame-Options',
                        value: 'DENY'
                    },
                    {
                        key: 'Cross-Origin-Resource-Policy',
                        value: 'same-origin'
                    },
                    {
                        key: 'Cross-Origin-Opener-Policy',
                        value: 'same-origin'
                    },
                    {
                        key: 'X-Permitted-Cross-Domain-Policies',
                        value: 'none'
                    },
                    {
                        key: 'X-Content-Type-Options',
                        value: 'nosniff'
                    },
                    {
                        key: 'Referrer-Policy',
                        value: 'origin-when-cross-origin'
                    },
                    {
                        key: 'Permissions-Policy',
                        value: 'camera=(), microphone=(), geolocation=(), payment=()'
                    },
                    {
                        key: 'Content-Security-Policy',
                        value: CSP
                    }
                ]
            }
        ]
    },
}

module.exports = nextConfig
