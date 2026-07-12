import { AlertTriangle } from 'lucide-react';

export default function AlertsBanner({ alerts }) {
  if (!alerts || alerts.length === 0) return null;

  const alertText = alerts
    .map((alert) => `${alert.event}${alert.description ? `: ${alert.description}` : ''}`)
    .join('  •  ');

  return (
    <div className="fixed top-0 left-0 right-0 z-50 bg-red-600/80 backdrop-blur-md border-b border-red-400/30 py-2 px-4">
      <div className="flex items-center gap-2 max-w-5xl mx-auto">
        <AlertTriangle className="w-5 h-5 text-white flex-shrink-0" />
        <div className="marquee flex-1 text-white text-sm font-medium">
          <span className="marquee-content">{alertText}</span>
        </div>
      </div>
    </div>
  );
}
