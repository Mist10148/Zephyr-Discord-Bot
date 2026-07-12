import { Clock, Droplets } from 'lucide-react';
import { getWeatherIconComponent } from '../utils/icons';

export default function HourlyForecast({ hourly, unit }) {
  if (!hourly || hourly.length === 0) return null;

  return (
    <div className="mt-6">
      <div className="flex items-center gap-2 mb-4 px-1">
        <Clock className="w-5 h-5 text-sky-300" />
        <h3 className="text-xl font-semibold text-white">24-Hour Forecast</h3>
      </div>
      <div className="flex gap-3 overflow-x-auto pb-4 hide-scrollbar snap-x snap-mandatory">
        {hourly.map((hour, index) => (
          <div
            key={`${hour.time}-${index}`}
            className="snap-start flex-shrink-0 glass p-4 w-24 flex flex-col items-center text-center gap-2 hover:bg-white/15 transition-colors"
          >
            <span className="text-white/70 text-sm font-medium">{hour.time}</span>
            <div className="py-1">
              {getWeatherIconComponent('', 32, hour.icon)}
            </div>
            <span className="text-white font-semibold text-lg">
              {unit === 'C' ? hour.temp_c : hour.temp_f}°
            </span>
            <div className="flex items-center gap-1 text-white/50 text-xs">
              <Droplets className="w-3 h-3" />
              <span>{hour.pop}%</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
