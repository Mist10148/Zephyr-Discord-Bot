export default function Layout({ children, condition = 'clear' }) {
  return (
    <div className={`aurora-bg min-h-screen w-full flex flex-col items-center px-4 py-6 sm:px-6 lg:px-8 weather-${condition}`}>
      {/* Floating orbs — colors controlled by weather condition class */}
      <div className="orb orb-1 fixed top-16 left-6 w-64 h-64 sm:w-80 sm:h-80 animate-float" style={{ animationDelay: '0s' }} />
      <div className="orb orb-2 fixed bottom-20 right-6 w-80 h-80 sm:w-96 sm:h-96 animate-float" style={{ animationDelay: '-4s' }} />
      <div className="orb orb-3 fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] sm:w-[700px] sm:h-[700px] opacity-60" />

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
