import { Cloud } from 'lucide-react';

export default function Layout({ children }) {
  return (
    <div className="aurora-bg min-h-screen w-full flex flex-col items-center justify-center px-4 py-8 sm:px-6 lg:px-8">
      {/* Decorative floating orbs */}
      <div className="fixed top-20 left-10 w-72 h-72 bg-blue-500/20 rounded-full blur-3xl animate-float pointer-events-none" />
      <div className="fixed bottom-20 right-10 w-96 h-96 bg-purple-500/20 rounded-full blur-3xl animate-float pointer-events-none" style={{ animationDelay: '-3s' }} />
      <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-cyan-500/10 rounded-full blur-3xl pointer-events-none" />

      {/* Header */}
      <header className="mb-8 flex items-center gap-3 z-10">
        <div className="p-3 glass rounded-xl">
          <Cloud className="w-8 h-8 text-sky-300" />
        </div>
        <div>
          <h1 className="text-3xl font-bold text-white tracking-tight">Zephyr</h1>
          <p className="text-sm text-white/60">Weather Dashboard</p>
        </div>
      </header>

      {/* Main content */}
      <main className="w-full max-w-5xl z-10">
        {children}
      </main>

      <footer className="mt-8 text-white/40 text-xs z-10">
        Powered by OpenWeatherMap · Gemini · Flask · React
      </footer>
    </div>
  );
}
