'use client'

export default function Error({
    error,
    reset,
}: {
    error: Error & { digest?: string }
    reset: () => void
}) {
    return (
        <div className="flex flex-col items-center justify-center min-h-[60vh] text-center space-y-6">
            <h2 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
                Something went wrong
            </h2>
            <p className="text-slate-600 dark:text-slate-400 max-w-md">
                An unexpected error occurred. Please try again.
            </p>
            <button
                onClick={() => reset()}
                className="inline-flex items-center px-6 py-3 rounded-lg bg-amber-600 text-white font-medium hover:bg-amber-700 transition-colors"
            >
                Try Again
            </button>
        </div>
    )
}
