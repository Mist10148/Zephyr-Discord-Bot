import ForecastCard from './ForecastCard';
import { Calendar } from 'lucide-react';

export default function ForecastList({ forecast, unit }) {
  if (!forecast || forecast.length === 0) return null;

  return (
    <div className="mt-6">
      <div className="flex items-center gap-2 mb-4 px-1">
        <Calendar className="w-5 h-5 text-sky-300" />
        <h3 className="text-xl font-semibold text-white">4-Day Forecast</h3>
      </div>
      <div className="flex gap-4 overflow-x-auto pb-4 hide-scrollbar snap-x snap-mandatory">
        {forecast.map((day, index) => (
          <div key={`${day.date}-${index}`} className="snap-start">
            <ForecastCard day={day} unit={unit} />
          </div>
        ))}
      </div>
    </div>
  );
}
