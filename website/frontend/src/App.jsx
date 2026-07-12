import { useState, useEffect, useCallback } from 'react';
import Layout from './components/Layout';
import LocationTabs from './components/LocationTabs';
import SearchBar from './components/SearchBar';
import CurrentWeather from './components/CurrentWeather';
import ForecastList from './components/ForecastList';
import ErrorMessage from './components/ErrorMessage';
import LoadingSkeleton from './components/LoadingSkeleton';
import { fetchWeather } from './api/weather';

const DEFAULT_CITY = 'Iloilo City, Philippines';

export default function App() {
  const [activeTab, setActiveTab] = useState('iloilo');
  const [weather, setWeather] = useState(null);
  const [city, setCity] = useState(DEFAULT_CITY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const loadWeather = useCallback(async (cityName) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchWeather(cityName);
      setWeather(data);
      setCity(cityName);
    } catch (err) {
      setError(err.message || 'Failed to fetch weather data');
      setWeather(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadWeather(DEFAULT_CITY);
  }, [loadWeather]);

  const handleSearch = (searchCity) => {
    loadWeather(searchCity);
  };

  const handleTabChange = (tab) => {
    setActiveTab(tab);
    if (tab === 'iloilo' && city !== DEFAULT_CITY) {
      loadWeather(DEFAULT_CITY);
    }
  };

  return (
    <Layout>
      <div className="w-full max-w-4xl mx-auto">
        <LocationTabs activeTab={activeTab} onTabChange={handleTabChange} />

        {activeTab === 'search' && (
          <div className="mb-6">
            <SearchBar onSearch={handleSearch} isLoading={loading} />
          </div>
        )}

        {loading && <LoadingSkeleton />}

        {!loading && error && <ErrorMessage message={error} />}

        {!loading && weather && (
          <div className="space-y-6 animate-in fade-in duration-500">
            <CurrentWeather
              data={weather.current}
              city={city}
              timezone={weather.timezone}
            />
            <ForecastList forecast={weather.forecast} />
          </div>
        )}
      </div>
    </Layout>
  );
}
