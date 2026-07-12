import { useState, useEffect, useCallback } from 'react';
import Layout from './components/Layout';
import Header from './components/Header';
import LocationTabs from './components/LocationTabs';
import SearchBar from './components/SearchBar';
import FavoritesTray from './components/FavoritesTray';
import AlertsBanner from './components/AlertsBanner';
import CurrentWeather from './components/CurrentWeather';
import MetricsGrid from './components/MetricsGrid';
import HourlyForecast from './components/HourlyForecast';
import ForecastList from './components/ForecastList';
import ErrorMessage from './components/ErrorMessage';
import LoadingSkeleton from './components/LoadingSkeleton';
import { fetchDefaultWeather, fetchWeatherByCity } from './api/weather';

const DEFAULT_CITY = 'Iloilo City, Philippines';
const FAVORITES_KEY = 'zephyr_recent_cities';

export default function App() {
  const [activeTab, setActiveTab] = useState('iloilo');
  const [weather, setWeather] = useState(null);
  const [city, setCity] = useState(DEFAULT_CITY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [unit, setUnit] = useState('C');
  const [favorites, setFavorites] = useState([]);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(FAVORITES_KEY);
      if (stored) {
        setFavorites(JSON.parse(stored));
      }
    } catch {
      // ignore storage errors
    }
  }, []);

  const saveFavorites = useCallback((newFavorites) => {
    setFavorites(newFavorites);
    try {
      localStorage.setItem(FAVORITES_KEY, JSON.stringify(newFavorites.slice(0, 3)));
    } catch {
      // ignore storage errors
    }
  }, []);

  const addToFavorites = useCallback(
    (weatherData) => {
      if (!weatherData) return;
      const entry = {
        name: weatherData.city,
        temp_c: weatherData.current?.temp_c,
        temp_f: weatherData.current?.temp_f,
      };
      const withoutExisting = favorites.filter((f) => f.name !== entry.name);
      saveFavorites([entry, ...withoutExisting].slice(0, 3));
    },
    [favorites, saveFavorites]
  );

  const loadWeather = useCallback(
    async (cityName) => {
      setLoading(true);
      setError(null);
      try {
        const data =
          cityName === DEFAULT_CITY
            ? await fetchDefaultWeather()
            : await fetchWeatherByCity(cityName);
        setWeather(data);
        setCity(data.city || cityName);
        addToFavorites(data);
      } catch (err) {
        setError(err.message || 'Failed to fetch weather data');
        setWeather(null);
      } finally {
        setLoading(false);
      }
    },
    [addToFavorites]
  );

  useEffect(() => {
    loadWeather(DEFAULT_CITY);
  }, [loadWeather]);

  const handleSearch = (searchCity) => {
    loadWeather(searchCity);
  };

  const handleTabChange = (tab) => {
    setActiveTab(tab);
    if (tab === 'iloilo' && city !== 'Iloilo City') {
      loadWeather(DEFAULT_CITY);
    }
  };

  const handleUnitChange = (newUnit) => {
    setUnit(newUnit);
  };

  const condition = weather?.current?.condition || 'clear';

  return (
    <>
      <AlertsBanner alerts={weather?.alerts || []} />
      <Layout condition={condition}>
        <div className={`w-full max-w-4xl mx-auto ${weather?.alerts?.length > 0 ? 'pt-10' : ''}`}>
          <Header unit={unit} onUnitChange={handleUnitChange} />

          <LocationTabs activeTab={activeTab} onTabChange={handleTabChange} />

          {activeTab === 'search' && (
            <div className="mb-4">
              <SearchBar onSearch={handleSearch} isLoading={loading} />
            </div>
          )}

          <div className="mb-6">
            <FavoritesTray favorites={favorites} unit={unit} onSelect={handleSearch} />
          </div>

          {loading && <LoadingSkeleton />}

          {!loading && error && <ErrorMessage message={error} />}

          {!loading && weather && (
            <div className="space-y-6 animate-in fade-in duration-500">
              <CurrentWeather
                data={weather.current}
                city={weather.city}
                timezone={weather.timezone}
                unit={unit}
              />
              <MetricsGrid data={weather.current} unit={unit} />
              <HourlyForecast hourly={weather.hourly} unit={unit} />
              <ForecastList forecast={weather.daily} unit={unit} />
            </div>
          )}
        </div>
      </Layout>
    </>
  );
}
