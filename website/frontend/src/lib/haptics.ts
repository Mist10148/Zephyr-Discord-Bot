export function haptic(pattern: number | number[] = 8) { if ('vibrate' in navigator) navigator.vibrate(pattern) }
