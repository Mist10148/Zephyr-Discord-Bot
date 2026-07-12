export default function FavoritesTray({ favorites, unit, onSelect }) {
  if (!favorites || favorites.length === 0) return null;

  return (
    <div className="flex items-center gap-2 overflow-x-auto hide-scrollbar pb-2">
      <span className="text-white/50 text-sm whitespace-nowrap">Recent:</span>
      {favorites.map((city, index) => (
        <button
          key={`${city.name}-${index}`}
          type="button"
          onClick={() => onSelect(city.name)}
          className="flex items-center gap-2 px-3 py-1.5 glass rounded-full hover:bg-white/15 transition-colors whitespace-nowrap"
        >
          <span className="text-white text-sm font-medium">{city.name}</span>
          <span className="text-xs bg-white/20 text-white/90 px-1.5 py-0.5 rounded-md">
            {unit === 'C' ? city.temp_c : city.temp_f}°
          </span>
        </button>
      ))}
    </div>
  );
}
