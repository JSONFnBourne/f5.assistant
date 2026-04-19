import Link from 'next/link'

export default function NotFound() {
    return (
        <div className="flex flex-col items-center justify-center min-h-[60vh] text-center space-y-6">
            <h2 className="text-6xl font-extrabold text-slate-300 dark:text-slate-700">404</h2>
            <h3 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
                Page Not Found
            </h3>
            <p className="text-slate-600 dark:text-slate-400 max-w-md">
                The page you&apos;re looking for doesn&apos;t exist or has been moved.
            </p>
            <Link
                href="/"
                className="inline-flex items-center px-6 py-3 rounded-lg bg-amber-600 text-white font-medium hover:bg-amber-700 transition-colors"
            >
                Back to Home
            </Link>
        </div>
    )
}
