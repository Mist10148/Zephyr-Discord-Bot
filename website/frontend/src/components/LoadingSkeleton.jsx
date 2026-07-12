export default function LoadingSkeleton() {
  return (
    <div className="space-y-6 animate-pulse">
      {/* Current weather skeleton */}
      <div className="glass-strong p-6 sm:p-8">
        <div className="flex flex-col sm:flex-row items-center justify-between gap-6">
          <div className="w-full sm:w-auto space-y-3">
            <div className="h-8 w-48 shimmer rounded-lg mx-auto sm:mx-0" />
            <div className="h-6 w-24 shimmer rounded-lg mx-auto sm:mx-0" />
            <div className="h-20 w-40 shimmer rounded-lg mx-auto sm:mx-0 mt-4" />
          </div>
          <div className="h-28 w-28 shimmer rounded-2xl" />
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 sm:gap-4 mt-8">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="h-28 shimmer rounded-xl" />
          ))}
        </div>
      </div>

      {/* Hourly skeleton */}
      <div>
        <div className="h-7 w-40 shimmer rounded-lg mb-4" />
        <div className="flex gap-3 overflow-hidden">
          {[...Array(8)].map((_, i) => (
            <div key={i} className="h-32 w-24 flex-shrink-0 shimmer rounded-2xl" />
          ))}
        </div>
      </div>

      {/* Forecast skeleton */}
      <div>
        <div className="h-7 w-40 shimmer rounded-lg mb-4" />
        <div className="flex gap-4 overflow-hidden">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-40 w-64 flex-shrink-0 shimmer rounded-2xl" />
          ))}
        </div>
      </div>
    </div>
  );
}
