// Flat ESLint config (ESLint 9). `next lint` was removed in Next.js 16, so
// `npm run lint` invokes the ESLint CLI directly against app/, lib/, scripts/.
// eslint-config-next@16 exports a flat-config array (includes TypeScript,
// React, react-hooks, jsx-a11y, import, and @next/next rules).
import nextConfig from 'eslint-config-next';
import globals from 'globals';

export default [
    {
        ignores: [
            '.next/**',
            'node_modules/**',
            'public/**',
            'next-env.d.ts',
        ],
    },
    ...nextConfig,
    {
        // Build-time Node scripts (CommonJS, not bundled by Next)
        files: ['scripts/**/*.js'],
        languageOptions: {
            globals: globals.node,
            sourceType: 'commonjs',
        },
    },
];
