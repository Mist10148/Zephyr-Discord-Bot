import { useEffect, useState } from 'react'
import { registerSW } from 'virtual:pwa-register'
import { PressableButton } from './ios'

export function PwaUpdate() {
  const [update, setUpdate] = useState<(() => void) | null>(null)
  useEffect(() => { const refresh = registerSW({ onNeedRefresh() { setUpdate(() => () => refresh(true)) } }); return () => undefined }, [])
  return update ? <div className="pwa-update" role="status">A new Zephyr version is ready.<PressableButton className="small" onClick={update}>Reload</PressableButton></div> : null
}
