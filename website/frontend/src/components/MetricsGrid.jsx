import { Droplets, Wind, Gauge, Thermometer, Sun, Leaf } from 'lucide-react';

export default function MetricsGrid({ data, unit }) {
  if (!data) return null;

  const feelsLike = unit === 'C' ? data.feels_like_c : data.feels_like_f;
  const aqiLabel = data.aqi_label || 'Unknown';

  const metrics = [
    {
      icon: <Droplets className="w-5 h-5 text-blue-300" />,
      label: 'Humidity',
      value: `${data.humidity}%`,
    },
    {
      icon: <Wind className="w-5 h-5 text-teal-300" />,
      label: 'Wind Speed',
      value: `${data.wind_speed} m/s`,
    },
    {
      icon: <Gauge className="w-5 h-5 text-purple-300" />,
      label: 'Pressure',
      value: `${data.pressure} hPa`,
    },
    {
      icon: <Thermometer className="w-5 h-5 text-amber-300" />,
      label: 'Feels Like',
      value: `${feelsLike}°${unit}`,
    },
    {
      icon: <Sun className="w-5 h-5 text-orange-300" />,
      label: 'UV Index',
      value: data.uvi !== null && data.uvi !== undefined ? data.uvi : '—',
    },
    {
      icon: <Leaf className="w-5 h-5 text-green-300" />,
      label: 'Air Quality',
      value: data.aqi !== null && data.aqi !== undefined ? `${data.aqi} · ${aqiLabel}` : '—',
    },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 sm:gap-4">
      {metrics.map((metric) => (
        <div key={metric.label} className="glass p-4 flex flex-col items-center text-center gap-2">
          <div className="p-2 bg-white/5 rounded-lg">{metric.icon}</div>
          <span className="text-white/50 text-xs uppercase tracking-wider">{metric.label}</span>
          <span className="text-white font-semibold text-base sm:text-lg">{metric.value}</span>
        </div>
      ))}
    </div>
  );
}
