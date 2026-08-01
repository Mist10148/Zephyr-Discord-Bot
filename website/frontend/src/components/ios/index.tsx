import * as Dialog from '@radix-ui/react-dialog'
import { motion } from 'motion/react'
import { Link } from 'react-router-dom'
import type { ReactNode } from 'react'

// Every frosted surface carries `data-glass`, which is what the single
// prefers-reduced-transparency rule in theme.css keys off. A new glass component
// gets the opaque fallback for free instead of being forgotten in that rule.
//
// `tier` picks the blur weight by role, not by taste: thin for day cards and
// notices, regular for content cards, thick for chrome that floats over content
// (tab bar, palette, sheets, save bar). A sheet reads heavier than a card because
// it is a tier up, not because it invented its own number.
type Tier = 'thin' | 'regular' | 'thick'

export function GlassSurface({ children, className = '', interactive = false, tier = 'regular' }: { children: ReactNode; className?: string; interactive?: boolean; tier?: Tier }) {
  return <section data-glass="1" className={`glass glass-${tier} ${interactive ? 'glass-interactive' : ''} ${className}`.trim()}>{children}</section>
}

// No `href` prop on purpose: a polymorphic button|a needs discriminated-union props,
// and blurring the two semantics is the classic design-system mistake. A real link
// (the OAuth sign-in) is a plain <a className="ios-button">, which keeps middle-click
// and copy-link working and performs a genuine navigation to Flask that react-router
// <Link> could never do.
export function PressableButton({ children, onClick, className = '', disabled = false, type = 'button', variant = 'primary', title }: { children: ReactNode; onClick?: () => void; className?: string; disabled?: boolean; type?: 'button' | 'submit'; variant?: 'primary' | 'secondary' | 'danger'; title?: string }) {
  return <motion.button
    type={type}
    title={title}
    whileTap={disabled ? undefined : { scale: .96 }}
    whileHover={disabled ? undefined : { y: -1 }}
    transition={{ type: 'spring', stiffness: 400, damping: 30 }}
    className={`ios-button ${variant} ${className}`.trim()}
    disabled={disabled}
    onClick={onClick}
  >{children}</motion.button>
}

/** A circular icon button — the transport controls and the small row actions. */
export function IconButton({ children, onClick, label, variant = 'secondary', size = 44, disabled = false }: { children: ReactNode; onClick?: () => void; label: string; variant?: 'primary' | 'secondary' | 'danger'; size?: number; disabled?: boolean }) {
  return <motion.button
    type="button"
    aria-label={label}
    title={label}
    disabled={disabled}
    whileTap={disabled ? undefined : { scale: .92 }}
    transition={{ type: 'spring', stiffness: 400, damping: 30 }}
    className={`icon-button ${variant}`}
    style={{ width: size, height: size }}
    onClick={onClick}
  >{children}</motion.button>
}

export function Sheet({ open, onOpenChange, children, label }: { open: boolean; onOpenChange(value: boolean): void; children: ReactNode; label?: string }) {
  return <Dialog.Root open={open} onOpenChange={onOpenChange}>
    <Dialog.Portal>
      <Dialog.Overlay className="sheet-overlay" />
      <Dialog.Content className="sheet" data-glass="1" aria-label={label}>
        <div className="grabber" aria-hidden />
        {children}
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>
}

export function SegmentedControl({ values, value, onChange, labels }: { values: string[]; value: string; onChange(value: string): void; labels?: Record<string, string> }) {
  return <div className="segmented" role="group">
    {values.map(item => (
      <button key={item} type="button" className={item === value ? 'active' : ''} aria-pressed={item === value} onClick={() => onChange(item)}>
        {labels?.[item] ?? item}
      </button>
    ))}
  </div>
}

export function ListGroup({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`list-group ${className}`.trim()}>{children}</div>
}

// `label` widens from string to ReactNode, so both existing call sites still compile
// and still take the plain <div> branch. `to`/`onClick` add the chevron for free.
// `pressed` turns the row into a toggle rather than a destination: it carries
// aria-pressed and drops the chevron, because a chevron promises navigation and a
// row that ticks a checkbox in place does not navigate anywhere.
export function ListRow({ label, detail, leading, to, onClick, pressed, children, className = '' }: { label: ReactNode; detail?: ReactNode; leading?: ReactNode; to?: string; onClick?: () => void; pressed?: boolean; children?: ReactNode; className?: string }) {
  const inner = <>
    {leading && <span className="row-leading">{leading}</span>}
    <span className="row-label">{label}{detail !== undefined && <small>{detail}</small>}</span>
    {children}
    {(to ?? onClick) && pressed === undefined ? <Chevron /> : null}
  </>
  const cls = `list-row ${className}`.trim()
  if (to) return <Link className={cls} to={to}>{inner}</Link>
  if (onClick) return <button className={cls} type="button" aria-pressed={pressed} onClick={onClick}>{inner}</button>
  return <div className={cls}>{inner}</div>
}

export function Chevron() { return <span className="chevron" aria-hidden>›</span> }

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange(value: boolean): void; label?: string }) {
  return <button type="button" className={`toggle ${checked ? 'on' : ''}`} onClick={() => onChange(!checked)} aria-pressed={checked} aria-label={label}><i /></button>
}

export function Slider({ value, onChange, label }: { value: number; onChange(value: number): void; label?: string }) {
  return <input className="slider" type="range" value={value} onChange={event => onChange(+event.target.value)} aria-label={label} aria-valuetext={String(value)} />
}

export function Stepper({ value, onChange }: { value: number; onChange(value: number): void }) {
  return <div className="stepper">
    <button type="button" aria-label="Decrement" onClick={() => onChange(value - 1)}>−</button>
    <span>{value}</span>
    <button type="button" aria-label="Increment" onClick={() => onChange(value + 1)}>+</button>
  </div>
}

export function LargeTitleHeader({ title, subtitle, note }: { title: string; subtitle?: string; note?: ReactNode }) {
  return <header className="large-title">
    <h1>{title}</h1>
    {subtitle && <p className="subtitle">{subtitle}</p>}
    {note && <p className="subtitle faint">{note}</p>}
  </header>
}

/** The uppercase eyebrow that heads a group of rows ("Configuration", "Personas"). */
export function SectionLabel({ children }: { children: ReactNode }) { return <h2 className="section-label">{children}</h2> }

/** The "‹ Back" affordance every screen ends with. A real Link, not a history
    pop: these point at a specific page, so they must survive a deep link. */
export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return <p className="back-link-row"><Link className="back-link" to={to}><span aria-hidden>‹</span>{children}</Link></p>
}

export function CapsuleToast({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'error' | 'success' }) {
  return <div className={`toast ${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
    {tone === 'neutral' ? <i className="toast-dot" aria-hidden /> : <i className="toast-badge" aria-hidden>{tone === 'error' ? '!' : '✓'}</i>}
    <span>{children}</span>
  </div>
}

export function WidgetGrid({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`widget-grid ${className}`.trim()}>{children}</div>
}

// The shimmer needs no prefers-reduced-motion branch: theme.css already forces
// animation-duration to .01ms on everything under that query.
export function Skeleton({ lines = 3 }: { lines?: number }) {
  return <div className="skeleton" aria-busy="true">{Array.from({ length: lines }, (_, index) => <i key={index} />)}</div>
}
