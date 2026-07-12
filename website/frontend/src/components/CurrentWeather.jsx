import { getWeatherIconComponent } from '../utils/icons';
import { Droplets, Wind, Gauge, Thermometer } from 'lucide-react';

export default function CurrentWeather({ data, city, timezone }) {
  if (!data) return null;

  return (
    <div className="glass-strong p-6 sm:p-8">
      <div className="flex flex-col sm:flex-row items-center justify-between gap-6">
        {/* Left: city & main temp */}
        <div className="text-center sm:text-left">
          <h2 className="text-2xl sm:text-3xl font-semibold text-white">{city}</h2>
          {timezone && (
            <p className="text-white/50 text-sm mt-1">{timezone}</p>
          )}
          <div className="mt-4 flex items-center justify-center sm:justify-start gap-4">
            <span className="text-6xl sm:text-7xl font-bold text-white tracking-tighter">
              {data.temp}°
            </span>
            <div className="flex flex-col items-start">
              <span className="text-lg text-white/80 capitalize">{data.desc}</span>
              <span className="text-sm text-white/50">Feels like {data.temp}°</span>
            </div>
          </div>
        </div>

        {/* Right: weather icon */}
        <div className="flex-shrink-0 p-4 glass rounded-2xl">
          {getWeatherIconComponent(data.desc, 96)}
        </div>
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-8">
        <MetricCard
          icon={<Droplets className="w-5 h-5 text-blue-300" />}
          label="Humidity"
          value={`${data.humidity}%`}
        />
        <MetricCard
          icon={<Wind className="w-5 h-5 text-teal-300" />}
          label="Wind Speed"
          value={`${data.wind_speed} m/s`}
        />
        <MetricCard
          icon={<Gauge className="w-5 h-5 text-purple-300" />}
          label="Pressure"
          value={`${data.pressure} hPa`}
        />
        <MetricCard
          icon={<Thermometer className="w-5 h-5 text-amber-300" />}
          label="Temperature"
          value={`${data.temp}°C`}
        />
      </div>
    </div>
  );
}

function MetricCard({ icon, label, value }) {
  return (
    <div className="glass p-4 flex flex-col items-center text-center gap-2">
      <div className="p-2 bg-white/5 rounded-lg">{icon}</div>
      <span className="text-white/50 text-xs uppercase tracking-wider">{label}</span>
      <span className="text-white font-semibold text-lg">{value}</span>
    </div>
  );
}
