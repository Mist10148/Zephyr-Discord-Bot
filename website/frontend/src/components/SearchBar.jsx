import { useState, useEffect, useRef, useCallback } from 'react';
import { Search, MapPin, Loader2 } from 'lucide-react';
import { fetchCitySuggestions } from '../api/weather';

export default function SearchBar({ onSearch, isLoading }) {
  const [city, setCity] = useState('');
  const [suggestions, setSuggestions] = useState([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const wrapperRef = useRef(null);

  const fetchSuggestions = useCallback(async (query) => {
    if (query.length < 2) {
      setSuggestions([]);
      return;
    }
    setSuggestLoading(true);
    try {
      const data = await fetchCitySuggestions(query);
      setSuggestions(data);
    } catch {
      setSuggestions([]);
    } finally {
      setSuggestLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      fetchSuggestions(city.trim());
    }, 250);
    return () => clearTimeout(timer);
  }, [city, fetchSuggestions]);

  useEffect(() => {
    function handleClickOutside(event) {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target)) {
        setShowSuggestions(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleSubmit = (e) => {
    e.preventDefault();
    const trimmed = city.trim();
    if (trimmed) {
      setShowSuggestions(false);
      onSearch(trimmed);
    }
  };

  const handleSuggestionClick = (suggestion) => {
    setCity(suggestion.name);
    setShowSuggestions(false);
    onSearch(suggestion.name);
  };

  return (
    <div ref={wrapperRef} className="relative w-full">
      <form onSubmit={handleSubmit} className="w-full">
        <div className="glass flex items-center gap-3 p-2 pr-3">
          <div className="pl-3 text-white/40">
            <MapPin className="w-5 h-5" />
          </div>
          <input
            type="text"
            value={city}
            onChange={(e) => {
              setCity(e.target.value);
              setShowSuggestions(true);
            }}
            onFocus={() => city.trim().length >= 2 && setShowSuggestions(true)}
            placeholder="Search for a city..."
            className="flex-1 bg-transparent text-white placeholder-white/40 outline-none text-base py-2"
            disabled={isLoading}
          />
          <button
            type="submit"
            disabled={isLoading || !city.trim()}
            className="flex items-center gap-2 px-5 py-2.5 bg-sky-500 hover:bg-sky-400 disabled:bg-sky-500/40 disabled:cursor-not-allowed text-white rounded-xl font-medium transition-all duration-200 shadow-lg shadow-sky-500/20"
          >
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
            <span>Search</span>
          </button>
        </div>
      </form>

      {showSuggestions && (suggestions.length > 0 || suggestLoading) && (
        <div className="absolute top-full left-0 right-0 mt-2 glass max-h-64 overflow-y-auto z-50">
          {suggestLoading && suggestions.length === 0 && (
            <div className="px-4 py-3 text-white/50 text-sm flex items-center gap-2">
              <Loader2 className="w-4 h-4 animate-spin" /> Loading suggestions...
            </div>
          )}
          {suggestions.map((suggestion, index) => (
            <button
              key={`${suggestion.name}-${suggestion.country}-${index}`}
              type="button"
              onClick={() => handleSuggestionClick(suggestion)}
              className="w-full text-left px-4 py-3 hover:bg-white/10 transition-colors flex items-center justify-between"
            >
              <span className="text-white font-medium">{suggestion.name}</span>
              <span className="text-white/50 text-sm">{suggestion.country}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
