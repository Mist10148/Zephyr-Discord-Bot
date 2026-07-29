import * as Dialog from '@radix-ui/react-dialog'
import { motion } from 'motion/react'
import type { ReactNode } from 'react'

export function GlassSurface({ children, className = '' }: { children: ReactNode; className?: string }) { return <section className={`glass ${className}`}>{children}</section> }
export function PressableButton({ children, onClick }: { children: ReactNode; onClick?: () => void }) { return <motion.button whileTap={{ scale: .96 }} transition={{ type: 'spring', stiffness: 400, damping: 30 }} className="ios-button" onClick={onClick}>{children}</motion.button> }
export function Sheet({ open, onOpenChange, children }: { open: boolean; onOpenChange(value: boolean): void; children: ReactNode }) { return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className="sheet-overlay" /><Dialog.Content className="sheet"><div className="grabber" />{children}</Dialog.Content></Dialog.Portal></Dialog.Root> }
export function SegmentedControl({ values, value, onChange }: { values: string[]; value: string; onChange(value: string): void }) { return <div className="segmented">{values.map(item => <button key={item} className={item === value ? 'active' : ''} onClick={() => onChange(item)}>{item}</button>)}</div> }
export function ListGroup({ children }: { children: ReactNode }) { return <div className="list-group">{children}</div> }
export function ListRow({ label, children }: { label: string; children?: ReactNode }) { return <div className="list-row"><span>{label}</span>{children}</div> }
export function Toggle({ checked, onChange }: { checked: boolean; onChange(value: boolean): void }) { return <button className={`toggle ${checked ? 'on' : ''}`} onClick={() => onChange(!checked)} aria-pressed={checked}><i /></button> }
export function Slider({ value, onChange }: { value: number; onChange(value: number): void }) { return <input className="slider" type="range" value={value} onChange={event => onChange(+event.target.value)} /> }
export function Stepper({ value, onChange }: { value: number; onChange(value: number): void }) { return <div className="stepper"><button onClick={() => onChange(value - 1)}>−</button><span>{value}</span><button onClick={() => onChange(value + 1)}>+</button></div> }
export function LargeTitleHeader({ title }: { title: string }) { return <header className="large-title"><h1>{title}</h1></header> }
export function TabBar({ children }: { children: ReactNode }) { return <nav className="tab-bar">{children}</nav> }
export function DynamicIsland({ children }: { children: ReactNode }) { return <aside className="island">{children}</aside> }
export function CapsuleToast({ children }: { children: ReactNode }) { return <div className="toast">{children}</div> }
export function PullToRefresh({ children }: { children: ReactNode }) { return <div className="pull-refresh">{children}</div> }
export function WidgetGrid({ children }: { children: ReactNode }) { return <div className="widget-grid">{children}</div> }
