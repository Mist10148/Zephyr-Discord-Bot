import { useState } from 'react';
import { getWeatherIconComponent } from '../utils/icons';
import { Droplets, Wind, Gauge, ChevronDown, ChevronUp, Sun, Moon } from 'lucide-react';

export default function ForecastCard({ day }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      className={`glass flex-shrink-0 w-72 transition-all duration-300 hover:-translate-y-1 ${
        expanded ? 'bg-white/15' : ''
      }`}
    >
      {/* Header */}
      <div
        className="p-5 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && setExpanded(!expanded)}
      >
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-lg font-semibold text-white">{day.weekday}</h3>
            <p className="text-white/50 text-sm">{day.date}</p>
          </div>
          <button className="text-white/40 hover:text-white transition-colors">
            {expanded ? <ChevronUp className="w-5 h-5" /> : <ChevronDown className="w-5 h-5" />}
          </button>
        </div>

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            {getWeatherIconComponent(day.day_desc, 48)}
            <div>
              <p className="text-white font-medium">{day.day_temp}°</p>
              <p className="text-white/50 text-xs capitalize">{day.day_desc}</p>
            </div>
          </div>
          <div className="text-right">
            <p className="text-white/60 text-sm">{day.night_temp}°</p>
            <p className="text-white/40 text-xs">Night</p>
          </div>
        </div>
      </div>

      {/* Expanded details */}
      {expanded && (
        <div className="px-5 pb-5 pt-0 border-t border-white/10">
          <div className="grid grid-cols-2 gap-3 mt-4">
            <DetailRow icon={<Sun className="w-4 h-4 text-amber-300" />} label="Day" value={`${day.day_temp}°C`} />
            <DetailRow icon={<Moon className="w-4 h-4 text-indigo-300" />} label="Night" value={`${day.night_temp}°C`} />
            <DetailRow icon={<Droplets className="w-4 h-4 text-blue-300" />} label="Humidity" value={`${day.day_humidity}%`} />
            <DetailRow icon={<Wind className="w-4 h-4 text-teal-300" />} label="Wind" value={`${day.day_wind} m/s`} />
            <DetailRow icon={<Gauge className="w-4 h-4 text-purple-300" />} label="Pressure" value={`${day.day_pressure} hPa`} />
          </div>
        </div>
      )}
    </div>
  );
}

function DetailRow({ icon, label, value }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <div className="text-white/60">{icon}</div>
      <span className="text-white/50">{label}:</span>
      <span className="text-white font-medium ml-auto">{value}</span>
    </div>
  );
}
