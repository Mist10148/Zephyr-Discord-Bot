import { MapPin, Search } from 'lucide-react';

export default function LocationTabs({ activeTab, onTabChange }) {
  const tabs = [
    { id: 'iloilo', label: 'Iloilo City', icon: MapPin },
    { id: 'search', label: 'Search City', icon: Search },
  ];

  return (
    <div className="glass inline-flex p-1 rounded-xl mb-6">
      {tabs.map((tab) => {
        const Icon = tab.icon;
        const isActive = activeTab === tab.id;
        return (
          <button
            key={tab.id}
            onClick={() => onTabChange(tab.id)}
            className={`flex items-center gap-2 px-4 sm:px-5 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 ${
              isActive
                ? 'bg-sky-500 text-white shadow-lg shadow-sky-500/25'
                : 'text-white/60 hover:text-white hover:bg-white/5'
            }`}
          >
            <Icon className="w-4 h-4" />
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
