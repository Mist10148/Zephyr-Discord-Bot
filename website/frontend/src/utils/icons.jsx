import {
  Sun,
  Cloud,
  CloudRain,
  CloudLightning,
  Snowflake,
  CloudFog,
  CloudSun,
  CloudMoon,
  Moon,
  ThermometerSun,
  Wind,
  Droplets,
  Gauge,
} from 'lucide-react';

const ICON_MAP = {
  '01d': { icon: Sun, color: 'text-amber-300' },
  '01n': { icon: Moon, color: 'text-indigo-200' },
  '02d': { icon: CloudSun, color: 'text-sky-200' },
  '02n': { icon: CloudMoon, color: 'text-indigo-200' },
  '03d': { icon: Cloud, color: 'text-slate-200' },
  '03n': { icon: Cloud, color: 'text-slate-300' },
  '04d': { icon: Cloud, color: 'text-slate-300' },
  '04n': { icon: Cloud, color: 'text-slate-400' },
  '09d': { icon: CloudRain, color: 'text-blue-300' },
  '09n': { icon: CloudRain, color: 'text-blue-300' },
  '10d': { icon: CloudRain, color: 'text-blue-300' },
  '10n': { icon: CloudRain, color: 'text-blue-300' },
  '11d': { icon: CloudLightning, color: 'text-yellow-300' },
  '11n': { icon: CloudLightning, color: 'text-yellow-300' },
  '13d': { icon: Snowflake, color: 'text-cyan-200' },
  '13n': { icon: Snowflake, color: 'text-cyan-200' },
  '50d': { icon: CloudFog, color: 'text-slate-300' },
  '50n': { icon: CloudFog, color: 'text-slate-400' },
};

export function getWeatherIconByCode(code, fallbackDescription = '') {
  if (code && ICON_MAP[code]) {
    return ICON_MAP[code];
  }
  return getWeatherIcon(fallbackDescription);
}

export function getWeatherIcon(description = '') {
  const desc = description.toLowerCase();

  if (desc.includes('thunder') || desc.includes('storm')) {
    return { icon: CloudLightning, color: 'text-yellow-300' };
  }
  if (desc.includes('snow') || desc.includes('sleet') || desc.includes('blizzard')) {
    return { icon: Snowflake, color: 'text-cyan-200' };
  }
  if (desc.includes('rain') || desc.includes('drizzle') || desc.includes('shower')) {
    return { icon: CloudRain, color: 'text-blue-300' };
  }
  if (desc.includes('fog') || desc.includes('mist') || desc.includes('haze')) {
    return { icon: CloudFog, color: 'text-slate-300' };
  }
  if (desc.includes('cloud')) {
    return { icon: Cloud, color: 'text-slate-200' };
  }
  if (desc.includes('clear') || desc.includes('sun')) {
    return { icon: Sun, color: 'text-amber-300' };
  }

  return { icon: CloudSun, color: 'text-sky-300' };
}

export function getWeatherIconComponent(description, size = 64, iconCode = '') {
  const { icon: Icon, color } = iconCode
    ? getWeatherIconByCode(iconCode, description)
    : getWeatherIcon(description);
  return <Icon size={size} className={color} />;
}

export {
  Sun,
  Cloud,
  CloudRain,
  CloudLightning,
  Snowflake,
  CloudFog,
  CloudSun,
  CloudMoon,
  Moon,
  ThermometerSun,
  Wind,
  Droplets,
  Gauge,
};
