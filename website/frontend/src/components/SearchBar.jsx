import { useState } from 'react';
import { Search, MapPin } from 'lucide-react';

export default function SearchBar({ onSearch, isLoading }) {
  const [city, setCity] = useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    const trimmed = city.trim();
    if (trimmed) {
      onSearch(trimmed);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="w-full">
      <div className="glass flex items-center gap-3 p-2 pr-3">
        <div className="pl-3 text-white/40">
          <MapPin className="w-5 h-5" />
        </div>
        <input
          type="text"
          value={city}
          onChange={(e) => setCity(e.target.value)}
          placeholder="Search for a city..."
          className="flex-1 bg-transparent text-white placeholder-white/40 outline-none text-base py-2"
          disabled={isLoading}
        />
        <button
          type="submit"
          disabled={isLoading || !city.trim()}
          className="flex items-center gap-2 px-5 py-2.5 bg-sky-500 hover:bg-sky-400 disabled:bg-sky-500/40 disabled:cursor-not-allowed text-white rounded-xl font-medium transition-all duration-200 shadow-lg shadow-sky-500/20"
        >
          <Search className="w-4 h-4" />
          <span>Search</span>
        </button>
      </div>
    </form>
  );
}
