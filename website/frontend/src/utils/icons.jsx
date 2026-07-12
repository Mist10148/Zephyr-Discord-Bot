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

export function getWeatherIcon(description = '', size = 64) {
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

export function getWeatherIconComponent(description, size = 64) {
  const { icon: Icon, color } = getWeatherIcon(description, size);
  return <Icon size={size} className={color} />;
}

export { Sun, Cloud, CloudRain, CloudLightning, Snowflake, CloudFog, CloudSun, Moon, ThermometerSun, Wind, Droplets, Gauge };
