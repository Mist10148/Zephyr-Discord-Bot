import { Cloud } from 'lucide-react';

export default function Header({ unit, onUnitChange }) {
  return (
    <header className="mb-6 flex items-center justify-between gap-4 z-10 w-full">
      <div className="flex items-center gap-3">
        <div className="p-3 glass rounded-xl">
          <Cloud className="w-7 h-7 sm:w-8 sm:h-8 text-sky-300" />
        </div>
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold text-white tracking-tight">Zephyr</h1>
          <p className="text-xs sm:text-sm text-white/60">Weather Dashboard</p>
        </div>
      </div>

      {/* Unit toggle */}
      <div className="flex items-center gap-3 glass px-3 py-2 rounded-xl">
        <span className={`text-sm font-medium ${unit === 'C' ? 'text-white' : 'text-white/50'}`}>°C</span>
        <button
          type="button"
          onClick={() => onUnitChange(unit === 'C' ? 'F' : 'C')}
          className="relative inline-flex h-6 w-11 items-center rounded-full bg-white/20 transition-colors focus:outline-none focus:ring-2 focus:ring-sky-400 focus:ring-offset-2 focus:ring-offset-slate-900"
          aria-label={`Switch to ${unit === 'C' ? 'Fahrenheit' : 'Celsius'}`}
        >
          <span
            className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
              unit === 'F' ? 'translate-x-6' : 'translate-x-1'
            }`}
          />
        </button>
        <span className={`text-sm font-medium ${unit === 'F' ? 'text-white' : 'text-white/50'}`}>°F</span>
      </div>
    </header>
  );
}
