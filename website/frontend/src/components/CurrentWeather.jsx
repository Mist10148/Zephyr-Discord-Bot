import { getWeatherIconComponent } from '../utils/icons';

export default function CurrentWeather({ data, city, timezone, unit }) {
  if (!data) return null;

  const temp = unit === 'C' ? data.temp_c : data.temp_f;
  const feelsLike = unit === 'C' ? data.feels_like_c : data.feels_like_f;

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
            <span className="text-7xl sm:text-8xl font-bold text-white tracking-tighter">
              {temp}°
            </span>
            <div className="flex flex-col items-start">
              <span className="text-lg text-white/80 capitalize">{data.description}</span>
              <span className="text-sm text-white/50">Feels like {feelsLike}°</span>
            </div>
          </div>
        </div>

        {/* Right: weather icon */}
        <div className="flex-shrink-0 p-5 glass rounded-2xl">
          {getWeatherIconComponent(data.description, 112, data.icon)}
        </div>
      </div>
    </div>
  );
}
