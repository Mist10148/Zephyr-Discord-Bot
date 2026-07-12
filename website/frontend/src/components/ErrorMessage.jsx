import { AlertCircle } from 'lucide-react';

export default function ErrorMessage({ message }) {
  return (
    <div className="glass border-red-400/30 bg-red-500/10 p-5 flex items-start gap-3">
      <AlertCircle className="w-5 h-5 text-red-300 flex-shrink-0 mt-0.5" />
      <div>
        <h3 className="text-white font-medium">Unable to load weather</h3>
        <p className="text-white/60 text-sm mt-1">{message}</p>
      </div>
    </div>
  );
}
