const API_BASE = '/api';

async function handleResponse(response) {
  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: 'Failed to fetch weather data' }));
    throw new Error(error.error || `HTTP ${response.status}`);
  }
  return response.json();
}

export async function fetchDefaultWeather() {
  const response = await fetch(`${API_BASE}/weather/default`);
  return handleResponse(response);
}

export async function fetchWeatherByCity(city) {
  const params = new URLSearchParams({ city });
  const response = await fetch(`${API_BASE}/weather/search?${params.toString()}`);
  return handleResponse(response);
}

export async function fetchCitySuggestions(query) {
  if (!query || query.length < 2) return [];
  const params = new URLSearchParams({ query });
  const response = await fetch(`${API_BASE}/weather/suggest?${params.toString()}`);
  return handleResponse(response);
}

export async function fetchHealth() {
  const response = await fetch('/health');
  return response.json();
}
