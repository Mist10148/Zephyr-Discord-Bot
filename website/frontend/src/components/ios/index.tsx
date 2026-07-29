import * as Dialog from '@radix-ui/react-dialog'
import { motion } from 'motion/react'
import { Link } from 'react-router-dom'
import type { ReactNode } from 'react'

export function GlassSurface({ children, className = '' }: { children: ReactNode; className?: string }) { return <section className={`glass ${className}`}>{children}</section> }
// No `href` prop on purpose: a polymorphic button|a needs discriminated-union props,
// and blurring the two semantics is the classic design-system mistake. A real link
// (the OAuth sign-in) is a plain <a className="ios-button">, which keeps middle-click
// and copy-link working and performs a genuine navigation to Flask that react-router
// <Link> could never do.
export function PressableButton({ children, onClick, className = '', disabled = false, type = 'button', variant = 'primary' }: { children: ReactNode; onClick?: () => void; className?: string; disabled?: boolean; type?: 'button' | 'submit'; variant?: 'primary' | 'secondary' | 'danger' }) { return <motion.button type={type} whileTap={disabled ? undefined : { scale: .96 }} transition={{ type: 'spring', stiffness: 400, damping: 30 }} className={`ios-button ${variant} ${className}`.trim()} disabled={disabled} onClick={onClick}>{children}</motion.button> }
export function Sheet({ open, onOpenChange, children }: { open: boolean; onOpenChange(value: boolean): void; children: ReactNode }) { return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className="sheet-overlay" /><Dialog.Content className="sheet"><div className="grabber" />{children}</Dialog.Content></Dialog.Portal></Dialog.Root> }
export function SegmentedControl({ values, value, onChange }: { values: string[]; value: string; onChange(value: string): void }) { return <div className="segmented">{values.map(item => <button key={item} className={item === value ? 'active' : ''} onClick={() => onChange(item)}>{item}</button>)}</div> }
export function ListGroup({ children }: { children: ReactNode }) { return <div className="list-group">{children}</div> }
// `label` widens from string to ReactNode, so both existing call sites still compile
// and still take the plain <div> branch. `to`/`onClick` add the chevron for free.
export function ListRow({ label, detail, leading, to, onClick, children }: { label: ReactNode; detail?: ReactNode; leading?: ReactNode; to?: string; onClick?: () => void; children?: ReactNode }) {
  const inner = <>{leading && <span className="row-leading">{leading}</span>}<span className="row-label">{label}{detail !== undefined && <small>{detail}</small>}</span>{children}{(to ?? onClick) ? <i className="chevron" aria-hidden /> : null}</>
  if (to) return <Link className="list-row" to={to}>{inner}</Link>
  if (onClick) return <button className="list-row" type="button" onClick={onClick}>{inner}</button>
  return <div className="list-row">{inner}</div>
}
export function Toggle({ checked, onChange }: { checked: boolean; onChange(value: boolean): void }) { return <button className={`toggle ${checked ? 'on' : ''}`} onClick={() => onChange(!checked)} aria-pressed={checked}><i /></button> }
export function Slider({ value, onChange }: { value: number; onChange(value: number): void }) { return <input className="slider" type="range" value={value} onChange={event => onChange(+event.target.value)} /> }
export function Stepper({ value, onChange }: { value: number; onChange(value: number): void }) { return <div className="stepper"><button onClick={() => onChange(value - 1)}>−</button><span>{value}</span><button onClick={() => onChange(value + 1)}>+</button></div> }
export function LargeTitleHeader({ title }: { title: string }) { return <header className="large-title"><h1>{title}</h1></header> }
export function TabBar({ children }: { children: ReactNode }) { return <nav className="tab-bar">{children}</nav> }
export function DynamicIsland({ children }: { children: ReactNode }) { return <aside className="island">{children}</aside> }
export function CapsuleToast({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'error' }) { return <div className={`toast ${tone}`} role={tone === 'error' ? 'alert' : 'status'}>{children}</div> }
export function PullToRefresh({ children }: { children: ReactNode }) { return <div className="pull-refresh">{children}</div> }
export function WidgetGrid({ children }: { children: ReactNode }) { return <div className="widget-grid">{children}</div> }
// The shimmer needs no prefers-reduced-motion branch: theme.css already forces
// animation-duration to .01ms on everything under that query.
export function Skeleton({ lines = 3 }: { lines?: number }) { return <div className="skeleton" aria-busy="true">{Array.from({ length: lines }, (_, index) => <i key={index} />)}</div> }
